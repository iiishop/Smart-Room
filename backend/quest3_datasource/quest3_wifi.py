from __future__ import annotations

import time
from typing import Any

from .models import Quest3Config, Quest3WiFiAccessPoint, Quest3WiFiData


class Quest3WiFiCollector:
    """Collect WiFi scan data from Android WifiManager.

    Quest 3 deployment:
    1) APK path: package this module in a Chaquopy-based Android app.
    2) ADB path: push this package to an on-device Python runtime and start it there.

    Android permissions to declare/request:
    - android.permission.ACCESS_WIFI_STATE
    - android.permission.CHANGE_WIFI_STATE
    - android.permission.ACCESS_FINE_LOCATION
    """

    def __init__(self, config: Quest3Config, context: Any | None = None) -> None:
        self._config = config
        self._context = context
        self._wifi_manager = self._build_wifi_manager(context)

    def _build_wifi_manager(self, context: Any | None):
        if context is None:
            return None
        try:
            from jnius import autoclass  # type: ignore[import-untyped]
        except Exception as ex:  # pragma: no cover - unavailable on desktop dev
            raise RuntimeError("PyJNIus is required in Android runtime") from ex

        Context = autoclass("android.content.Context")
        return context.getSystemService(Context.WIFI_SERVICE)

    def start_scan(self) -> bool:
        """Run WifiManager.startScan()."""
        if self._wifi_manager is None:
            return False
        return bool(self._wifi_manager.startScan())

    def _result_timestamp_ms(self, result: Any) -> int | None:
        ts = getattr(result, "timestamp", None)
        if ts is None:
            return None
        try:
            ts_value = int(ts)
        except Exception:
            return None

        # Android ScanResult timestamp is usually microseconds since boot.
        if ts_value > 1_000_000_000_000:
            return ts_value // 1000
        return ts_value

    def _wait_for_fresh_scan_results(
        self,
        *,
        min_timestamp_ms: int,
        timeout_sec: float = 2.0,
        poll_interval_sec: float = 0.2,
    ) -> list[Quest3WiFiAccessPoint]:
        deadline = time.monotonic() + timeout_sec
        latest: list[Quest3WiFiAccessPoint] = []

        while time.monotonic() < deadline:
            if self._wifi_manager is None:
                return []

            raw_results = self._wifi_manager.getScanResults()
            newest_timestamp_ms = 0
            latest = []
            for item in raw_results:
                newest_timestamp_ms = max(
                    newest_timestamp_ms,
                    self._result_timestamp_ms(item) or 0,
                )
                latest.append(
                    Quest3WiFiAccessPoint(
                        bssid=str(getattr(item, "BSSID", "")),
                        ssid=str(getattr(item, "SSID", "")),
                        rssi=int(getattr(item, "level", 0)),
                        frequency=int(getattr(item, "frequency", 0)),
                    )
                )

            if newest_timestamp_ms >= min_timestamp_ms and latest:
                return latest

            time.sleep(poll_interval_sec)

        return latest

    def _latest_scan_timestamp_ms(self) -> int:
        if self._wifi_manager is None:
            return 0

        newest_timestamp_ms = 0
        for item in self._wifi_manager.getScanResults():
            newest_timestamp_ms = max(
                newest_timestamp_ms,
                self._result_timestamp_ms(item) or 0,
            )
        return newest_timestamp_ms

    def get_scan_results(self) -> list[Quest3WiFiAccessPoint]:
        """Read WifiManager.getScanResults() into serializable AP records."""
        if self._wifi_manager is None:
            return []

        results = self._wifi_manager.getScanResults()
        output: list[Quest3WiFiAccessPoint] = []
        for item in results:
            output.append(
                Quest3WiFiAccessPoint(
                    bssid=str(getattr(item, "BSSID", "")),
                    ssid=str(getattr(item, "SSID", "")),
                    rssi=int(getattr(item, "level", 0)),
                    frequency=int(getattr(item, "frequency", 0)),
                )
            )
        return output

    def collect_once(self, *, rtt_available: bool, headset_pose: dict | None = None) -> Quest3WiFiData:
        baseline_timestamp_ms = self._latest_scan_timestamp_ms()
        started = self.start_scan()
        if started:
            scan_results = self._wait_for_fresh_scan_results(
                min_timestamp_ms=baseline_timestamp_ms + 1,
                timeout_sec=max(self._config.scan_interval_sec, 1.0),
            )
        else:
            scan_results = self.get_scan_results()

        return Quest3WiFiData.now(
            device_id=self._config.device_id,
            scan_results=scan_results,
            rtt_available=rtt_available,
            headset_pose=headset_pose,
        )
