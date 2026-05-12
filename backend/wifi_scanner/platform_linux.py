import re
import subprocess
import time

from .base import BaseWifiScanner
from .models import RSSISample


class LinuxWifiScanner(BaseWifiScanner):
    def scan(self) -> list[RSSISample]:
        interface = self.config.interface or "wlan0"
        cmd = ["iw", "dev", interface, "scan"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return []

        samples = self._parse_iw_scan(result.stdout)
        return self._apply_bssid_filter(samples)

    def _parse_iw_scan(self, output: str) -> list[RSSISample]:
        now = time.time()
        samples: list[RSSISample] = []
        current_bssid = ""
        current_ssid = ""
        current_rssi: int | None = None
        current_freq: float | None = None

        def flush() -> None:
            if current_bssid and current_rssi is not None and current_freq is not None:
                samples.append(
                    RSSISample(
                        bssid=current_bssid,
                        ssid=current_ssid,
                        rssi=current_rssi,
                        frequency=current_freq,
                        timestamp=now,
                    )
                )

        for raw_line in output.splitlines():
            line = raw_line.strip()
            if line.startswith("BSS "):
                flush()
                match = re.search(r"BSS\s+([0-9a-fA-F:]{17})", line)
                current_bssid = match.group(1).lower() if match else ""
                current_ssid = ""
                current_rssi = None
                current_freq = None
                continue
            if line.startswith("SSID:"):
                current_ssid = line.split(":", 1)[1].strip()
                continue
            if line.startswith("signal:"):
                match = re.search(r"(-?\d+(?:\.\d+)?)", line)
                if match:
                    current_rssi = int(float(match.group(1)))
                continue
            if line.startswith("freq:"):
                match = re.search(r"(\d+(?:\.\d+)?)", line)
                if match:
                    current_freq = float(match.group(1))
                continue

        flush()
        return samples
