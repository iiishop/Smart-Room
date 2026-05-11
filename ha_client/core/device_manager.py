from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from ..config import HAConfig
from .event_bus import EventBus, EventType
from .ha_api import HAClient

logger = logging.getLogger(__name__)


class DeviceManager:
    def __init__(
        self,
        config: HAConfig,
        event_bus: EventBus,
        ha_client: HAClient,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._config = config
        self._event_bus = event_bus
        self._ha_client = ha_client
        self._loop = loop
        self._devices: dict[str, dict[str, Any]] = {}
        self._domains: dict[str, list[str]] = {}
        self._initialized: bool = False

        self._event_bus.subscribe_sync(EventType.STATE_CHANGED, self._on_state_changed)
        self._event_bus.subscribe_sync(EventType.CONNECTED, self._on_connected)
        self._event_bus.subscribe_sync(EventType.DISCONNECTED, self._on_disconnected)

    @property
    def devices(self) -> dict[str, dict[str, Any]]:
        return dict(self._devices)

    @property
    def initialized(self) -> bool:
        return self._initialized

    def get_device(self, entity_id: str) -> Optional[dict[str, Any]]:
        return self._devices.get(entity_id)

    def get_domain_devices(self, domain: str) -> list[str]:
        return list(self._domains.get(domain, []))

    def get_domains(self) -> list[str]:
        return sorted(self._domains.keys())

    async def refresh(self) -> None:
        raw = await self._ha_client.rest_get("/api/states")
        if not raw:
            return

        self._devices.clear()
        self._domains.clear()

        for entry in raw if isinstance(raw, list) else []:
            entity_id: str = entry.get("entity_id", "")
            if not entity_id:
                continue
            self._devices[entity_id] = {
                "entity_id": entity_id,
                "state": entry.get("state", ""),
                "attributes": entry.get("attributes", {}),
            }
            domain = entity_id.split(".", 1)[0]
            if domain not in self._domains:
                self._domains[domain] = []
            self._domains[domain].append(entity_id)

        self._domains = {k: sorted(v) for k, v in self._domains.items()}
        self._initialized = True

    async def get_history(self, entity_id: str, limit: int = 24) -> list[dict[str, Any]]:
        result = await self._ha_client.rest_get(
            f"/api/history/period?filter_entity_id={entity_id}&minimal_response"
        )
        if isinstance(result, list) and result:
            entries = result[0] if isinstance(result[0], list) else result
            return list(entries[-limit:]) if entries else []
        return []

    def _on_state_changed(self, **data: Any) -> None:
        entity_id = data.get("entity_id", "")
        state = data.get("state", "")
        attributes = data.get("attributes", {})
        if not entity_id:
            return
        self._devices[entity_id] = {
            "entity_id": entity_id,
            "state": state,
            "attributes": attributes,
        }
        domain = entity_id.split(".", 1)[0]
        if domain not in self._domains:
            self._domains[domain] = []
        if entity_id not in self._domains[domain]:
            self._domains[domain].append(entity_id)
            self._domains[domain].sort()

    def _on_connected(self, **data: Any) -> None:
        self._loop.create_task(self.refresh())

    def _on_disconnected(self, **data: Any) -> None:
        self._initialized = False
