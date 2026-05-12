from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class APPosition:
    x: float
    y: float
    z: float = 0.0


@dataclass
class PositionEstimate:
    device_id: str
    x: float | None
    y: float | None
    distance: float
    angle_deg: float
    confidence: float
    timestamp: float
