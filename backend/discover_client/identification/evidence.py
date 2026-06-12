"""Structured evidence extracted from raw source events."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SignalEvidence:
    source_id: str
    source_type: str
    mqtt_topic: str | None = None
    mqtt_payload_keys: set[str] | None = None
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
