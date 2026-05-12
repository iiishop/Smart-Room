from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class RssiReading:
    bssid: str
    rssi: float
    frequency: int = 2412
    timestamp: float | None = None


@dataclass(slots=True)
class RawPosition:
    x: float
    y: float
    confidence: float
    direction: float | None = None
    source: str = "trilateration"
    estimated_distance: float | None = None
