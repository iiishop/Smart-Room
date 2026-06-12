"""Structured evidence extracted from raw source events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SignalEvidence:
    source_id: str
    source_type: str
    mqtt_topic: str | None = None
    mqtt_payload_keys: set[str] | None = None
    mqtt_payload: Any = None
    topic_prefix: str | None = None    # govee/H5179/a1b2c3d4e5f6 (trim last segment)
    mdns_service_type: str | None = None
    mdns_txt_keys: set[str] | None = None
    ssdp_usn: str | None = None
    ssdp_server: str | None = None
    nmap_mac: str | None = None
    nmap_vendor: str | None = None
    nmap_os_guess: str | None = None
    ip_address: str | None = None
    hostname: str | None = None
    mac_prefix: str | None = None
    timestamp: float = 0.0

    def summarize(self) -> str:
        parts: list[str] = []
        if self.ip_address:
            parts.append(f"ip={self.ip_address}")
        if self.hostname:
            parts.append(f"host={self.hostname}")
        if self.nmap_mac:
            parts.append(f"mac={self.nmap_mac}")
        elif self.mac_prefix:
            parts.append(f"mac={self.mac_prefix}")
        if self.nmap_vendor:
            parts.append(f"vendor={self.nmap_vendor}")
        if self.nmap_os_guess:
            parts.append(f"os={self.nmap_os_guess}")
        if self.mqtt_topic:
            parts.append(f"topic={self.mqtt_topic}")
        if self.mqtt_payload_keys:
            keys = ",".join(sorted(self.mqtt_payload_keys))
            parts.append(f"keys={{{keys}}}")
        if self.mdns_service_type:
            parts.append(f"svc={self.mdns_service_type}")
        if self.mdns_txt_keys:
            keys = ",".join(sorted(self.mdns_txt_keys))
            parts.append(f"txt={{{keys}}}")
        if self.ssdp_usn:
            parts.append(f"usn={self.ssdp_usn[:60]}")
        if self.ssdp_server:
            parts.append(f"srv={self.ssdp_server}")
        return "  ".join(parts) if parts else "(no clues)"
