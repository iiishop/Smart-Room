from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

from wifi_positioning.collectors.base import RssiCollector
from wifi_positioning.models import RssiReading, RssiSource


logger = logging.getLogger(__name__)


class LinuxRssiCollector(RssiCollector):
    def __init__(self, interface: str = "wlan0", scan_interval: float = 1.0) -> None:
        self.interface = interface
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
                    "iw",
                    "dev",
                    self.interface,
                    "scan",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError as exc:
                raise RuntimeError("iw command not found. Please install wireless-tools/iw.") from exc

            stdout, stderr = await result.communicate()
            if result.returncode != 0:
                err = stderr.decode("utf-8", errors="ignore").strip() or "iw scan failed"
                logger.warning("Linux RSSI scan failed: %s", err)
                await asyncio.sleep(self.scan_interval)
                continue

            output = stdout.decode("utf-8", errors="ignore")
            for reading in self.parse_iw_output(output):
                yield reading

            await asyncio.sleep(self.scan_interval)

    async def list_aps(self) -> list[str]:
        try:
            result = await asyncio.create_subprocess_exec(
                "iw",
                "dev",
                self.interface,
                "scan",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("iw command not found. Please install wireless-tools/iw.") from exc

        stdout, stderr = await result.communicate()
        if result.returncode != 0:
            err = stderr.decode("utf-8", errors="ignore").strip() or "iw scan failed"
            logger.warning("Linux list_aps scan failed: %s", err)
            return []

        readings = self.parse_iw_output(stdout.decode("utf-8", errors="ignore"))
        return sorted({r.ap_bssid for r in readings})

    @staticmethod
    def parse_iw_output(output: str) -> list[RssiReading]:
        readings: list[RssiReading] = []
        current_bssid: str | None = None
        current_freq: int | None = None

        for raw_line in output.splitlines():
            line = raw_line.strip()
            bss_match = re.match(r"^BSS\s+([0-9a-fA-F:]{17})", line)
            if bss_match:
                current_bssid = bss_match.group(1).lower()
                current_freq = None
                continue

            freq_match = re.match(r"^freq:\s*(\d+)", line)
            if freq_match:
                current_freq = int(freq_match.group(1))
                continue

            signal_match = re.match(r"^signal:\s*(-?\d+(?:\.\d+)?)\s*dBm", line)
            if signal_match and current_bssid:
                readings.append(
                    RssiReading(
                        source=RssiSource.AP_MONITOR,
                        ap_bssid=current_bssid,
                        device_mac=None,
                        rssi=float(signal_match.group(1)),
                        frequency=current_freq,
                        timestamp=datetime.now(timezone.utc),
                    )
                )

        return readings
