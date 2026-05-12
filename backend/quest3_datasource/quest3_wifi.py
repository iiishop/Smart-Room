from __future__ import annotations

import time
from threading import Event
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

    Scan behavior:
    - Android API >= 29: skip startScan(), read getScanResults() directly
      (platform throttles scan freshness).
    - Android API < 29: trigger startScan() and wait for
      SCAN_RESULTS_AVAILABLE_ACTION via BroadcastReceiver.
    """

    def __init__(self, config: Quest3Config, context: Any | None = None) -> None:
        self._config = config
        self._context = context
        self._scan_ready = Event()
        self._receiver = None
        self._api_level = self._detect_api_level()
        self._wifi_manager = self._build_wifi_manager(context)
        self._register_scan_receiver()

    def _detect_api_level(self) -> int:
        if self._context is None:
            return 0
        try:
            from jnius import autoclass  # type: ignore[import-untyped]
        except Exception:
            return 0

        BuildVersion = autoclass("android.os.Build$VERSION")
        return int(getattr(BuildVersion, "SDK_INT", 0))

    def _build_wifi_manager(self, context: Any | None):
        if context is None:
            return None
        try:
            from jnius import autoclass  # type: ignore[import-untyped]
        except Exception as ex:  # pragma: no cover - unavailable on desktop dev
            raise RuntimeError("PyJNIus is required in Android runtime") from ex

        Context = autoclass("android.content.Context")
        return context.getSystemService(Context.WIFI_SERVICE)

    def _register_scan_receiver(self) -> None:
        if self._context is None:
            return

        try:
            from jnius import PythonJavaClass, autoclass, java_method  # type: ignore[import-untyped]
        except Exception:
            return

        IntentFilter = autoclass("android.content.IntentFilter")
        action = "android.net.wifi.SCAN_RESULTS"

        collector = self

        class _ScanResultReceiver(PythonJavaClass):
            __javainterfaces__ = ["android/content/BroadcastReceiver"]
            __javacontext__ = "app"

            @java_method("(Landroid/content/Context;Landroid/content/Intent;)V")
            def onReceive(self, context, intent):
                collector._scan_ready.set()

        self._receiver = _ScanResultReceiver()
        intent_filter = IntentFilter()
        intent_filter.addAction(action)
        self._context.registerReceiver(self._receiver, intent_filter)

    def start_scan(self) -> bool:
        """Run WifiManager.startScan()."""
        if self._wifi_manager is None:
            return False
        if self._api_level >= 29:
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

    def collect_once(
        self,
        *,
        rtt_available: bool,
        headset_pose: dict | None = None,
        timeout: float = 5.0,
    ) -> Quest3WiFiData:
        if self._context is None:
            self.start_scan()
            time.sleep(3.0)
            scan_results = self.get_scan_results()
        elif self._api_level >= 29:
            scan_results = self.get_scan_results()
        else:
            self._scan_ready.clear()
            started = self.start_scan()
            if started and self._receiver is not None:
                self._scan_ready.wait(timeout=max(timeout, 0.1))
            else:
                time.sleep(min(timeout, 3.0))
            scan_results = self.get_scan_results()

        return Quest3WiFiData.now(
            device_id=self._config.device_id,
            scan_results=scan_results,
            rtt_available=rtt_available,
            headset_pose=headset_pose,
        )

    def close(self) -> None:
        if self._context is None or self._receiver is None:
            return
        try:
            self._context.unregisterReceiver(self._receiver)
        except Exception:
            pass
