import re
import subprocess
import time

from .base import BaseWifiScanner
from .models import RSSISample


def _channel_to_frequency(channel: int) -> float:
    if 1 <= channel <= 13:
        return 2412 + (channel - 1) * 5
    if channel == 14:
        return 2484
    return 5000 + channel * 5


def _signal_percent_to_dbm(signal_percent: int) -> int:
    return int((signal_percent / 2) - 100)


class WindowsWifiScanner(BaseWifiScanner):
    def scan(self) -> list[RSSISample]:
        cmd = ["netsh", "wlan", "show", "networks", "mode=bssid"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return []

        samples = self._parse_netsh_output(result.stdout)
        return self._apply_bssid_filter(samples)

    def _parse_netsh_output(self, output: str) -> list[RSSISample]:
        now = time.time()
        samples: list[RSSISample] = []
        current_ssid = ""
        current_bssid = ""
        current_signal: int | None = None
        current_channel: int | None = None

        def flush() -> None:
            if current_bssid and current_signal is not None and current_channel is not None:
                samples.append(
                    RSSISample(
                        bssid=current_bssid,
                        ssid=current_ssid,
                        rssi=_signal_percent_to_dbm(current_signal),
                        frequency=_channel_to_frequency(current_channel),
                        timestamp=now,
                    )
                )

        for raw_line in output.splitlines():
            line = raw_line.strip()
            if line.startswith("SSID "):
                flush()
                current_bssid = ""
                current_signal = None
                current_channel = None
                current_ssid = line.split(":", 1)[1].strip()
                continue
            if line.startswith("BSSID "):
                flush()
                current_bssid = line.split(":", 1)[1].strip().lower()
                current_signal = None
                current_channel = None
                continue
            if line.startswith("Signal"):
                match = re.search(r"(\d+)%", line)
                if match:
                    current_signal = int(match.group(1))
                continue
            if line.startswith("Channel"):
                match = re.search(r"(\d+)", line)
                if match:
                    current_channel = int(match.group(1))
                continue

        flush()
        return samples
