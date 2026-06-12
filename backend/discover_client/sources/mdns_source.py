"""mDNS service discovery source using zeroconf."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from zeroconf import ServiceStateChange
from zeroconf.asyncio import (
    AsyncServiceBrowser,
    AsyncServiceInfo,
    AsyncZeroconf,
    AsyncZeroconfServiceTypes,
)

from discover_client.source import Source, SourceConfig, SourceEvent

logger = logging.getLogger(__name__)

DEFAULT_SERVICE_TYPES = [
    "_mqtt._tcp.local.",
    "_home-assistant._tcp.local.",
    "_http._tcp.local.",
]


def _decode_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _service_info_to_payload(
    service_type: str, name: str, info: AsyncServiceInfo | None
) -> dict[str, Any]:
    """Convert zeroconf service info into the discovery payload shape."""
    payload: dict[str, Any] = {
        "service_type": service_type,
        "name": name,
        "host": None,
        "addresses": [],
        "port": None,
        "properties": {},
    }
    if info is None:
        return payload

    payload["host"] = info.server
    payload["addresses"] = info.parsed_addresses()
    payload["port"] = info.port
    payload["properties"] = {
        _decode_value(key): _decode_value(value)
        for key, value in info.properties.items()
    }
    return payload


class MdnsSource(Source):
    """Discovers mDNS services and emits discovery events."""

    def __init__(self, config: SourceConfig, emit) -> None:
        super().__init__(config, emit)
        self._scan_interval = int(config.settings.get("scan_interval_s", 30))
        self._configured_service_types = list(
            config.settings.get("service_types", DEFAULT_SERVICE_TYPES)
        )
        self._service_types = list(self._configured_service_types)

        self._zeroconf: AsyncZeroconf | None = None
        self._browsers: dict[str, AsyncServiceBrowser] = {}
        self._known_services: dict[str, set[str]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False
        self._sweep_task: asyncio.Task[None] | None = None

    def emit(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        """Schedule event delivery onto the owning asyncio loop."""
        event = SourceEvent(
            source_id=self.source_id,
            source_type=self.source_type,
            timestamp=time.time(),
            event_type=event_type,
            payload=payload or {},
        )
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._emit, event)
            return

        self._emit(event)

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._running = True

        try:
            self._zeroconf = AsyncZeroconf()
            if not self._service_types:
                self._service_types = list(
                    await AsyncZeroconfServiceTypes.async_find(aiozc=self._zeroconf)
                )
            await self._ensure_browsers(self._service_types)
        except Exception as exc:
            self.emit("error", {"msg": f"Failed to initialize zeroconf: {exc}"})
            await self._close_zeroconf()
            self._running = False
            return

        self.emit("status", {"msg": "scanning", "service_types": self._service_types})
        self._sweep_task = asyncio.create_task(self._periodic_sweep())

    async def stop(self) -> None:
        self._running = False

        if self._sweep_task is not None:
            self._sweep_task.cancel()
            try:
                await self._sweep_task
            except asyncio.CancelledError:
                pass
            self._sweep_task = None

        for browser in list(self._browsers.values()):
            await browser.async_cancel()
        self._browsers.clear()
        self._known_services.clear()

        await self._close_zeroconf()
        self.emit("status", {"msg": "stopped"})

    async def _close_zeroconf(self) -> None:
        if self._zeroconf is not None:
            await self._zeroconf.async_close()
            self._zeroconf = None

    async def _ensure_browsers(self, service_types: list[str]) -> None:
        if self._zeroconf is None:
            return

        for service_type in service_types:
            if service_type in self._browsers:
                continue

            self._browsers[service_type] = AsyncServiceBrowser(
                self._zeroconf.zeroconf,
                service_type,
                handlers=[self._on_service_state_change],
            )
            self._known_services.setdefault(service_type, set())

    async def _periodic_sweep(self) -> None:
        while self._running:
            await asyncio.sleep(self._scan_interval)
            if not self._running or self._zeroconf is None:
                break

            try:
                if not self._configured_service_types:
                    discovered_types = list(
                        await AsyncZeroconfServiceTypes.async_find(aiozc=self._zeroconf)
                    )
                    await self._ensure_browsers(discovered_types)
                    self._service_types = list(self._browsers.keys())

                for service_type, names in list(self._known_services.items()):
                    for name in list(names):
                        await self._resolve_service(service_type, name)
            except Exception as exc:
                self.emit("error", {"msg": f"Sweep error: {exc}"})

    async def _resolve_service(self, service_type: str, name: str) -> None:
        if self._zeroconf is None:
            return

        info = AsyncServiceInfo(service_type, name)
        resolved = await info.async_request(self._zeroconf.zeroconf, 8000)
        payload = _service_info_to_payload(service_type, name, info if resolved else None)
        self.emit("discovery", payload)

    def _schedule_resolve(self, service_type: str, name: str) -> None:
        if self._loop is None:
            return

        def runner() -> None:
            asyncio.create_task(self._resolve_service(service_type, name))

        self._loop.call_soon_threadsafe(runner)

    def _on_service_state_change(
        self,
        zeroconf,
        service_type: str,
        name: str,
        state_change: ServiceStateChange,
    ) -> None:
        _ = zeroconf
        services = self._known_services.setdefault(service_type, set())

        if state_change is ServiceStateChange.Removed:
            services.discard(name)
            self.emit(
                "discovery",
                {"service_type": service_type, "name": name, "removed": True},
            )
            return

        services.add(name)
        self._schedule_resolve(service_type, name)
