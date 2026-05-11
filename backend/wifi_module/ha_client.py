from __future__ import annotations

import asyncio
import logging
from typing import Optional

import aiohttp

from .models import WifiDeviceInfo, DeviceStatus, RssiSample

logger = logging.getLogger(__name__)


class HomeAssistantClient:
    def __init__(
        self,
        base_url: str,
        access_token: str,
        verify_ssl: bool = True,
        request_timeout: int = 10,
    ):
        self._base_url = base_url.rstrip("/")
        self._access_token = access_token
        self._verify_ssl = verify_ssl
        self._request_timeout = request_timeout
        self._session: Optional[aiohttp.ClientSession] = None
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

    async def start(self) -> bool:
        if self._session is not None:
            await self.stop()
        connector = aiohttp.TCPConnector(verify_ssl=self._verify_ssl)
        timeout = aiohttp.ClientTimeout(total=self._request_timeout)
        self._session = aiohttp.ClientSession(
            headers=self._headers, connector=connector, timeout=timeout
        )
        healthy = await self.health_check()
        if healthy:
            logger.info("Connected to Home Assistant at %s", self._base_url)
        else:
            logger.warning("Home Assistant health check failed at %s", self._base_url)
        return healthy

    async def stop(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None
            logger.info("Home Assistant client session closed")

    async def health_check(self) -> bool:
        try:
            resp = await self._session.get(f"{self._base_url}/api/")
            return resp.status == 200
        except Exception as exc:
            logger.warning("HA health check error: %s", exc)
            return False

    async def get_device_tracker_entities(self) -> list[dict]:
        if not self._session:
            raise RuntimeError("HA client not started. Call start() first.")

        try:
            resp = await self._session.get(f"{self._base_url}/api/states")
            if resp.status != 200:
                logger.warning("HA GET /api/states returned %d", resp.status)
                return []

            entities: list[dict] = await resp.json()
            return [
                entity
                for entity in entities
                if entity.get("entity_id", "").startswith("device_tracker.")
            ]
        except Exception as exc:
            logger.error("Failed to fetch HA states: %s", exc)
            return []

    async def _get_entity_state(self, entity_id: str) -> Optional[dict]:
        if not self._session:
            return None
        try:
            resp = await self._session.get(
                f"{self._base_url}/api/states/{entity_id}"
            )
            if resp.status == 200:
                return await resp.json()
        except Exception as exc:
            logger.debug("Failed to get entity state for %s: %s", entity_id, exc)
        return None

    async def extract_wifi_devices(
        self, target_macs: Optional[list[str]] = None
    ) -> list[WifiDeviceInfo]:
        entities = await self.get_device_tracker_entities()
        devices: list[WifiDeviceInfo] = []

        target_set = {mac.lower() for mac in (target_macs or [])}

        for entity in entities:
            attrs = entity.get("attributes", {})
            mac = (attrs.get("mac") or "").lower()

            if target_set and mac not in target_set:
                continue
            if not mac:
                continue

            state = entity.get("state", "not_home")
            status = DeviceStatus.ONLINE if state == "home" else DeviceStatus.OFFLINE

            rssi = None
            raw_rssi = attrs.get("rssi")
            signal = attrs.get("signal")
            if raw_rssi is not None:
                try:
                    rssi = float(raw_rssi)
                except (ValueError, TypeError):
                    pass
            elif signal is not None:
                try:
                    rssi = float(signal)
                except (ValueError, TypeError):
                    pass

            ip = attrs.get("ip", attrs.get("ip_address", ""))
            friendly = attrs.get("friendly_name", "")
            hostname = attrs.get("host_name", "")
            device_name = friendly or hostname or entity.get("entity_id", "")

            devices.append(
                WifiDeviceInfo(
                    mac=mac,
                    ip=str(ip),
                    rssi=rssi,
                    last_seen=entity.get("last_changed", ""),
                    device_name=device_name,
                    status=status,
                    ha_entity_id=entity.get("entity_id", ""),
                )
            )

        return devices

    async def get_rssi_for_device(self, entity_id: str) -> Optional[RssiSample]:
        entity = await self._get_entity_state(entity_id)
        if not entity:
            return None

        attrs = entity.get("attributes", {})
        mac = (attrs.get("mac") or "").lower()
        if not mac:
            return None

        rssi = None
        raw_rssi = attrs.get("rssi")
        signal = attrs.get("signal")
        if raw_rssi is not None:
            try:
                rssi = float(raw_rssi)
            except (ValueError, TypeError):
                pass
        elif signal is not None:
            try:
                rssi = float(signal)
            except (ValueError, TypeError):
                pass

        if rssi is None:
            return None

        import datetime as dt

        return RssiSample(
            mac=mac,
            rssi=rssi,
            timestamp_utc=entity.get(
                "last_changed", dt.datetime.now(dt.timezone.utc).isoformat()
            ),
        )
