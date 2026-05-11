from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class DeviceStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class WifiDeviceInfo(BaseModel):
    mac: str = Field(description="MAC address in lowercase hex with colons, e.g. aa:bb:cc:dd:ee:ff")
    ip: str = Field(default="", description="Last known IP address")
    rssi: Optional[float] = Field(default=None, description="RSSI signal strength in dBm")
    last_seen: str = Field(default="", description="ISO 8601 timestamp of last RSSI reading")
    device_name: str = Field(default="", description="Friendly name or hostname")
    status: DeviceStatus = Field(default=DeviceStatus.UNKNOWN)
    ha_entity_id: str = Field(default="", description="Home Assistant entity_id if sourced from HA")


class WifiDeviceList(BaseModel):
    devices: list[WifiDeviceInfo]
    count: int
    timestamp_utc: str


class RssiSample(BaseModel):
    mac: str
    rssi: float
    timestamp_utc: str
