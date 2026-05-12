from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class RssiSource(str, Enum):
    AP_MONITOR = "ap_monitor"
    DEVICE_REPORT = "device_report"


@dataclass(slots=True)
class RssiReading:
    source: RssiSource
    ap_bssid: str
    device_mac: str | None
    rssi: float
    frequency: int | None
    timestamp: datetime


@dataclass(slots=True)
class DevicePosition:
    device_mac: str
    x: float
    y: float
    z: float
    timestamp: datetime


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
