from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(slots=True)
class Quest3Config:
    device_id: str
    backend_host: str
    backend_udp_port: int = 9876
    backend_http_endpoint: str | None = None
    scan_interval_sec: float = 1.0


@dataclass(slots=True)
class Quest3WiFiAccessPoint:
    bssid: str
    ssid: str
    rssi: int
    frequency: int


@dataclass(slots=True)
class Quest3WiFiData:
    device_id: str
    timestamp: str
    scan_results: list[Quest3WiFiAccessPoint] = field(default_factory=list)
    rtt_available: bool = False
    headset_pose: dict[str, Any] | None = None

    @classmethod
    def now(
        cls,
        *,
        device_id: str,
        scan_results: list[Quest3WiFiAccessPoint],
        rtt_available: bool,
        headset_pose: dict[str, Any] | None = None,
    ) -> "Quest3WiFiData":
        return cls(
            device_id=device_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            scan_results=scan_results,
            rtt_available=rtt_available,
            headset_pose=headset_pose,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "timestamp": self.timestamp,
            "scan_results": [asdict(ap) for ap in self.scan_results],
            "rtt_available": self.rtt_available,
            "headset_pose": self.headset_pose,
        }
