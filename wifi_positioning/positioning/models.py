from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class RssiReading:
    bssid: str
    rssi: float
    frequency: int = 2412
    timestamp: float | None = None
    device_mac: str = ""


@dataclass(slots=True)
class RawPosition:
    x: float
    y: float
    confidence: float
    direction: float | None = None
    source: str = "trilateration"
    estimated_distance: float | None = None


@dataclass(slots=True)
class SmoothedPosition:
    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0
    covariance: list[float] | None = None
    confidence: float = 0.5
    timestamp: float = 0.0
    source: str = "ekf"

    def position_uncertainty(self) -> float:
        if self.covariance and len(self.covariance) >= 4:
            return float((self.covariance[0] + self.covariance[3]) ** 0.5)
        return 1.0
