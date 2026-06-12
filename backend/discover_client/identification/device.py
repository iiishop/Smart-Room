"""Tracked device state and competing identification hypotheses."""

from __future__ import annotations

from dataclasses import dataclass, field

from discover_client.identification.fingerprint import DeviceFingerprint


@dataclass
class DeviceHypothesis:
    fingerprint: DeviceFingerprint
    probability: float = 0.0


@dataclass
class Device:
    device_id: str
    hypotheses: list[DeviceHypothesis] = field(default_factory=list)
    total_evidence_count: int = 0
    last_seen: float = 0.0
    ip_addresses: set[str] = field(default_factory=set)
    hostnames: set[str] = field(default_factory=set)
    mac_prefixes: set[str] = field(default_factory=set)
