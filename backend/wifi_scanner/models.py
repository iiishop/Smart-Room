from dataclasses import dataclass


@dataclass(slots=True)
class RSSISample:
    bssid: str
    ssid: str
    rssi: int
    frequency: float
    timestamp: float
