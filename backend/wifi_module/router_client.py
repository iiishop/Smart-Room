from __future__ import annotations

import asyncio
import logging
import time

from .config import WifiModuleConfig
from .models import RssiSample

logger = logging.getLogger(__name__)


class RouterClient:
    def __init__(self, config: WifiModuleConfig):
        self._config = config
        self._last_rssi: dict[str, tuple[float, float]] = {}

    async def start(self) -> bool:
        logger.info(
            "Router client initialized (type=%s, host=%s)",
            self._config.router_type,
            self._config.router_host,
        )
        return True

    async def stop(self) -> None:
        pass

    async def get_rssi_values(self) -> list[RssiSample]:
        samples: list[RssiSample] = []

        if self._config.router_type == "openwrt":
            samples = await self._poll_openwrt()
        elif self._config.router_type == "unifi":
            samples = await self._poll_unifi()
        elif self._config.router_type == "asuswrt":
            samples = await self._poll_asuswrt()
        elif self._config.router_type == "ssh_generic":
            samples = await self._poll_ssh_generic()
        else:
            logger.debug("No router RSSI source configured (router_type is empty)")

        for sample in samples:
            self._last_rssi[sample.mac] = (sample.rssi, time.monotonic())

        return samples

    async def _poll_openwrt(self) -> list[RssiSample]:
        try:
            import asyncssh
        except ImportError:
            logger.warning(
                "asyncssh not installed, cannot poll OpenWrt router. "
                "Install with: pip install asyncssh"
            )
            return []

        try:
            async with asyncssh.connect(
                host=self._config.router_host,
                username=self._config.router_username,
                password=self._config.router_password,
                known_hosts=None,
            ) as conn:
                result = await conn.run(
                    "iwinfo wlan0 assoclist 2>/dev/null || iwinfo phy0-ap0 assoclist 2>/dev/null || iw dev wlan0 station dump"
                )
                return self._parse_iw_station_dump(
                    result.stdout or result.stderr or ""
                )
        except Exception as exc:
            logger.error("OpenWrt SSH poll failed: %s", exc)
            return []

    async def _poll_unifi(self) -> list[RssiSample]:
        logger.warning("Unifi router polling not yet implemented")
        return []

    async def _poll_asuswrt(self) -> list[RssiSample]:
        logger.warning("AsusWRT router polling not yet implemented")
        return []

    async def _poll_ssh_generic(self) -> list[RssiSample]:
        try:
            import asyncssh
        except ImportError:
            logger.warning("asyncssh not installed for generic SSH router polling")
            return []

        try:
            async with asyncssh.connect(
                host=self._config.router_host,
                username=self._config.router_username,
                password=self._config.router_password,
                known_hosts=None,
            ) as conn:
                result = await conn.run(
                    "iw dev wlan0 station dump 2>/dev/null || iw dev wlan1 station dump 2>/dev/null || iwinfo 2>/dev/null"
                )
                return self._parse_iw_station_dump(
                    result.stdout or result.stderr or ""
                )
        except Exception as exc:
            logger.error("Generic SSH router poll failed: %s", exc)
            return []

    def _parse_iw_station_dump(self, output: str) -> list[RssiSample]:
        import datetime as dt
        from re import finditer

        samples: list[RssiSample] = []
        now = dt.datetime.now(dt.timezone.utc).isoformat()

        current_mac = None
        current_signal = None

        for line in output.splitlines():
            line = line.strip()

            if line.lower().startswith("station "):
                parts = line.split()
                if len(parts) >= 2:
                    current_mac = parts[1].lower()

            signal_match = next(
                iter(
                    [
                        m.group(1)
                        for m in finditer(r"signal:\s*(-?\d+)", line)
                    ]
                ),
                None,
            )
            if signal_match:
                current_signal = float(signal_match.group(0)) if signal_match else None
                if not current_signal:
                    parts = line.split(":")
                    if len(parts) >= 2:
                        try:
                            current_signal = float(parts[1].strip().split()[0])
                        except (ValueError, IndexError):
                            pass

            if current_mac and current_signal is not None:
                samples.append(
                    RssiSample(mac=current_mac, rssi=current_signal, timestamp_utc=now)
                )
                current_mac = None
                current_signal = None

        return samples
