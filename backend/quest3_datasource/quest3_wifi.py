from __future__ import annotations

from typing import Any

from .models import Quest3Config, Quest3WiFiAccessPoint, Quest3WiFiData


class Quest3WiFiCollector:
    """Collect WiFi scan data from Android WifiManager.

    Quest 3 deployment:
    1) APK path: package this module in a Chaquopy-based Android app.
    2) ADB path: push this package to an on-device Python runtime and start it there.
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
        self.start_scan()
        scan_results = self.get_scan_results()
        return Quest3WiFiData.now(
            device_id=self._config.device_id,
            scan_results=scan_results,
            rtt_available=rtt_available,
            headset_pose=headset_pose,
        )
