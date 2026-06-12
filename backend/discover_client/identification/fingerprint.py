"""Known device fingerprints used for identification."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DeviceFingerprint:
    device_type: str
    category: str
    mqtt_topic_patterns: list[str] = field(default_factory=list)
    mqtt_payload_keys: set[str] = field(default_factory=set)
    mdns_service_types: list[str] = field(default_factory=list)
    mdns_txt_keys: set[str] = field(default_factory=set)
    ssdp_usn_patterns: list[str] = field(default_factory=list)
    ssdp_server_patterns: list[str] = field(default_factory=list)
    nmap_mac_prefixes: list[str] = field(default_factory=list)
    nmap_os_guesses: list[str] = field(default_factory=list)
    hostname_pattern: str | None = None


FINGERPRINTS: list[DeviceFingerprint] = [
    DeviceFingerprint(
        device_type="Unknown Device",
        category="unknown",
    ),
]
