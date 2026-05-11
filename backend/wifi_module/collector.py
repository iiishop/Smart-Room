from __future__ import annotations

import asyncio
import logging
import time
from threading import Lock
from typing import Optional

from .config import WifiModuleConfig
from .ha_client import HomeAssistantClient
from .models import RssiSample, WifiDeviceInfo, WifiDeviceList, DeviceStatus
from .mqtt_publisher import MqttPublisher
from .router_client import RouterClient

logger = logging.getLogger(__name__)


class WifiCollector:
    def __init__(self, config: Optional[WifiModuleConfig] = None):
        self._config = config or WifiModuleConfig()
        self._ha_client = HomeAssistantClient(
            base_url=self._config.ha_url,
            access_token=self._config.ha_token,
            verify_ssl=self._config.ha_verify_ssl,
            request_timeout=self._config.ha_request_timeout,
        )
        self._mqtt = MqttPublisher(self._config)
        self._router_client = RouterClient(self._config)

        self._lock = Lock()
        self._devices: dict[str, WifiDeviceInfo] = {}
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._ha_connected = False
        self._mqtt_connected = False

    @property
    def devices(self) -> list[WifiDeviceInfo]:
        with self._lock:
            return list(self._devices.values())

    @property
    def status(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "ha_connected": self._ha_connected,
                "mqtt_connected": self._mqtt_connected,
                "device_count": len(self._devices),
            }

    async def start(self) -> None:
        if self._running:
            return

        self._running = True
        logger.info("WiFi collector starting...")

        if self._config.ha_enabled:
            self._ha_connected = await self._ha_client.start()
            if not self._ha_connected:
                logger.warning(
                    "HA connection failed, will retry in poll loop"
                )

        await self._router_client.start()

        if self._config.mqtt_enabled:
            self._mqtt_connected = await self._mqtt.start()

        self._task = asyncio.create_task(self._poll_loop())
        logger.info("WiFi collector started (HA=%s, MQTT=%s, Poll=%.1fs)",
                     self._ha_connected, self._mqtt_connected, self._config.poll_interval_seconds)

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        await self._ha_client.stop()
        await self._mqtt.stop()
        await self._router_client.stop()
        logger.info("WiFi collector stopped")

    async def _poll_loop(self) -> None:
        last_discovery_time = 0.0
        discovery_interval = max(
            5.0, self._config.poll_interval_seconds * 5
        )

        while self._running:
            try:
                now = time.monotonic()

                if now - last_discovery_time >= discovery_interval:
                    await self._discover_devices()
                    last_discovery_time = now

                await self._poll_rssi()

                await asyncio.sleep(self._config.poll_interval_seconds)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Poll loop error: %s", exc, exc_info=True)
                await asyncio.sleep(min(self._config.poll_interval_seconds * 5, 5.0))

    async def _discover_devices(self) -> None:
        if not self._config.ha_enabled:
            return

        try:
            if not self._ha_connected:
                self._ha_connected = await self._ha_client.start()
                if not self._ha_connected:
                    return

            ha_devices = await self._ha_client.extract_wifi_devices(
                target_macs=self._config.target_macs or None
            )

            prev_macs = set()
            with self._lock:
                prev_macs = set(self._devices.keys())

            new_macs = {d.mac for d in ha_devices}

            joined_macs = new_macs - prev_macs
            left_macs = prev_macs - new_macs

            with self._lock:
                for device in ha_devices:
                    existing = self._devices.get(device.mac)
                    if existing:
                        device.rssi = existing.rssi or device.rssi
                        device.last_seen = existing.last_seen or device.last_seen
                    self._devices[device.mac] = device

                for mac in left_macs:
                    dev = self._devices.pop(mac, None)
                    if dev:
                        logger.info("Device left: %s (%s)", dev.device_name, mac)

            for mac in joined_macs:
                dev = self._devices.get(mac)
                name = dev.device_name if dev else mac
                logger.info("Device joined: %s (%s)", name, mac)
                if self._mqtt_connected:
                    await self._mqtt.publish_device_join(mac)

            for mac in left_macs:
                if self._mqtt_connected:
                    await self._mqtt.publish_device_leave(mac)

            if joined_macs or left_macs:
                logger.info(
                    "Device delta: +%d joined, -%d left, total=%d",
                    len(joined_macs),
                    len(left_macs),
                    len(self._devices),
                )

            self._update_offline_devices()

        except Exception as exc:
            logger.error("Device discovery error: %s", exc)
            self._ha_connected = False

    async def _poll_rssi(self) -> None:
        samples: list[RssiSample] = []

        if self._config.router_enabled:
            try:
                router_samples = await self._router_client.get_rssi_values()
                samples.extend(router_samples)
            except Exception as exc:
                logger.warning("Router RSSI poll error: %s", exc)

        if self._config.ha_enabled and self._ha_connected:
            tracked_entities = []
            with self._lock:
                tracked_entities = [
                    d.ha_entity_id for d in self._devices.values() if d.ha_entity_id
                ]

            for entity_id in tracked_entities:
                try:
                    sample = await self._ha_client.get_rssi_for_device(entity_id)
                    if sample:
                        existing = any(s.mac == sample.mac for s in samples)
                        if not existing:
                            samples.append(sample)
                except Exception as exc:
                    logger.debug(
                        "HA RSSI poll error for %s: %s", entity_id, exc
                    )

        if samples:
            self._apply_rssi_samples(samples)

        if self._mqtt_connected and samples:
            await self._mqtt.publish_batch(samples)

    def _apply_rssi_samples(self, samples: list[RssiSample]) -> None:
        now = time.monotonic()
        import datetime as dt

        with self._lock:
            for sample in samples:
                device = self._devices.get(sample.mac)
                if device:
                    device.rssi = sample.rssi
                    device.last_seen = sample.timestamp_utc
                    device.status = DeviceStatus.ONLINE
                elif self._config.router_enabled:
                    self._devices[sample.mac] = WifiDeviceInfo(
                        mac=sample.mac,
                        ip="",
                        rssi=sample.rssi,
                        last_seen=sample.timestamp_utc,
                        device_name=sample.mac,
                        status=DeviceStatus.ONLINE,
                        ha_entity_id="",
                    )

    def _update_offline_devices(self) -> None:
        import datetime as dt

        now = dt.datetime.now(dt.timezone.utc)
        timeout = self._config.device_offline_timeout_seconds
        offline_macs = []

        with self._lock:
            for mac, device in list(self._devices.items()):
                if device.last_seen:
                    try:
                        last_seen = dt.datetime.fromisoformat(
                            device.last_seen.replace("Z", "+00:00")
                        )
                        elapsed = (now - last_seen).total_seconds()
                        if elapsed > timeout:
                            device.status = DeviceStatus.OFFLINE
                    except (ValueError, TypeError):
                        pass

    async def force_refresh(self) -> None:
        await self._discover_devices()
        await self._poll_rssi()

    def get_device_list(self) -> WifiDeviceList:
        import datetime as dt

        with self._lock:
            devices = list(self._devices.values())
        return WifiDeviceList(
            devices=devices,
            count=len(devices),
            timestamp_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
        )
