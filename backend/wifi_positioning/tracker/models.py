from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class SmoothedPosition:
    mac: str
    x: float
    y: float
    direction: float
    distance: float
    confidence: float
    timestamp: datetime
    name: str | None = None
    zone: str | None = None


@dataclass
class DeviceState:
    mac: str
    name: str
    x: float
    y: float
    direction: float
    distance: float
    confidence: float
    timestamp: datetime
    zone: str | None = None
    present: bool = True

    @property
    def location_name(self) -> str:
        if not self.present:
            return "not_home"
        return self.zone or "home"
