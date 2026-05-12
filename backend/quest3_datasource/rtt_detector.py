from __future__ import annotations

from typing import Any


class RTTDetector:
    """Detect WiFi RTT (802.11mc) availability via WifiRttManager."""

    def __init__(self, context: Any | None = None) -> None:
        self._context = context

    def is_available(self) -> bool:
        if self._context is None:
            return False

        try:
            from jnius import autoclass  # type: ignore[import-untyped]
        except Exception:
            return False

        Context = autoclass("android.content.Context")
        rtt_manager = self._context.getSystemService(Context.WIFI_RTT_RANGING_SERVICE)
        if rtt_manager is None:
            return False
        return bool(rtt_manager.isAvailable())
