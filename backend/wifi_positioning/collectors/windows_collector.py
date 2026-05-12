from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

from wifi_positioning.collectors.base import RssiCollector
from wifi_positioning.models import RssiReading, RssiSource


logger = logging.getLogger(__name__)


def _percent_to_dbm(percent: int) -> float:
    bounded = max(0, min(100, percent))
    return (bounded / 2.0) - 100.0


class WindowsRssiCollector(RssiCollector):
    def __init__(self, scan_interval: float = 1.0) -> None:
        self.scan_interval = scan_interval
        self._running = False

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def collect(self):
        await self.start()
        while self._running:
            try:
                result = await asyncio.create_subprocess_exec(
                    "netsh",
                    "wlan",
                    "show",
                    "networks",
                    "mode=bssid",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError as exc:
                raise RuntimeError("netsh not found on this system.") from exc

            stdout, stderr = await result.communicate()
            if result.returncode != 0:
                err = stderr.decode("utf-8", errors="ignore").strip()
                logger.warning("Windows RSSI scan failed: %s", err or "netsh scan failed")
                await asyncio.sleep(self.scan_interval)
                continue

            output = stdout.decode("utf-8", errors="ignore")
            readings = self.parse_netsh_output(output)
            if not readings:
                logger.warning("Windows RSSI scan returned no BSSID entries")
                await asyncio.sleep(self.scan_interval)
                continue

            for reading in readings:
                yield reading

            await asyncio.sleep(self.scan_interval)

    async def list_aps(self) -> list[str]:
        try:
            result = await asyncio.create_subprocess_exec(
                "netsh",
                "wlan",
                "show",
                "networks",
                "mode=bssid",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("netsh not found on this system.") from exc

        stdout, stderr = await result.communicate()
        if result.returncode != 0:
            err = stderr.decode("utf-8", errors="ignore").strip()
            logger.warning("Windows list_aps scan failed: %s", err or "netsh scan failed")
            return []

        readings = self.parse_netsh_output(stdout.decode("utf-8", errors="ignore"))
        return sorted({r.ap_bssid for r in readings})

    @staticmethod
    def parse_netsh_output(output: str) -> list[RssiReading]:
        readings: list[RssiReading] = []
        current_bssid: str | None = None

        for raw_line in output.splitlines():
            line = raw_line.strip()
            bssid_match = re.match(r"^BSSID\s+\d+\s*:\s*([0-9a-fA-F:]{17})", line)
            if bssid_match:
                current_bssid = bssid_match.group(1).lower()
                continue

            signal_match = re.match(r"^Signal\s*:\s*(\d+)%", line)
            if signal_match and current_bssid:
                signal_percent = int(signal_match.group(1))
                readings.append(
                    RssiReading(
                        source=RssiSource.AP_MONITOR,
                        ap_bssid=current_bssid,
                        device_mac=None,
                        rssi=_percent_to_dbm(signal_percent),
                        frequency=None,
                        timestamp=datetime.now(timezone.utc),
                    )
                )

        return readings
