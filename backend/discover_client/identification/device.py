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
    mac_addresses: set[str] = field(default_factory=set)
    mac_prefixes: set[str] = field(default_factory=set)
    vendor: str | None = None
    os_guess: str | None = None
    service_types: set[str] = field(default_factory=set)
    ssdp_usns: set[str] = field(default_factory=set)
    payload_keys: set[str] = field(default_factory=set)
    topic_prefixes: set[str] = field(default_factory=set)
    mqtt_identities: set[str] = field(default_factory=set)
    mqtt_client_ids: set[str] = field(default_factory=set)
    mqtt_entity_prefixes: set[str] = field(default_factory=set)
    mqtt_entity_identities: set[str] = field(default_factory=set)
    mqtt_channels: set[str] = field(default_factory=set)
    member_device_ids: set[str] = field(default_factory=set)
    identity_reasons: list[str] = field(default_factory=list)
    identity_tokens: set[str] = field(default_factory=set)
    source_ids: set[str] = field(default_factory=set)
    explicit_names: set[str] = field(default_factory=set)
    explicit_manufacturers: set[str] = field(default_factory=set)
    explicit_models: set[str] = field(default_factory=set)
    explicit_descriptions: set[str] = field(default_factory=set)
    metadata_capabilities: set[str] = field(default_factory=set)
    metadata_identifiers: set[str] = field(default_factory=set)
    metadata_sources: set[str] = field(default_factory=set)
    mqtt_command_topics: set[str] = field(default_factory=set)
    mqtt_command_values: dict[str, set[str]] = field(default_factory=dict)
