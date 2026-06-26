"""Build user-facing network device profiles from accumulated discovery evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any

from discover_client.identification.device import Device
from discover_client.operations import OperationsTracker
from discover_client.identification.data_snapshot import DataSnapshot


_CAPABILITY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "temperature": ("temperature", "temp"),
    "humidity": ("humidity", "humid"),
    "pressure": ("pressure", "barometer"),
    "motion": ("motion", "pir", "occupancy"),
    "light": ("light", "lux", "illuminance"),
    "brightness": ("brightness", "dimmer", "level"),
    "power": ("power", "switch", "relay", "toggle", "on", "off"),
    "energy": ("energy", "voltage", "current", "watt"),
    "battery": ("battery",),
    "air_quality": ("co2", "pm25", "pm2.5", "air_quality", "voc"),
    "contact": ("door", "window", "contact", "open", "close"),
    "camera": ("camera", "video", "stream"),
    "display": ("display", "screen", "lcd"),
}

_GENERIC_VENDOR_TOKENS = {
    "cmnd",
    "discover",
    "homeassistant",
    "lab",
    "mock",
    "mqtt",
    "sensor",
    "sensors",
    "stat",
    "tele",
    "zigbee2mqtt",
}

_DEVICE_TYPE_LABELS = {
    "temperature_humidity_sensor": "temperature and humidity sensor",
    "environment_sensor": "environment sensor",
    "motion_sensor": "motion sensor",
    "contact_sensor": "contact sensor",
    "smart_light": "smart light",
    "smart_switch": "smart switch",
    "energy_monitor": "energy monitor",
    "camera": "camera",
    "network_device": "network device",
}


@dataclass
class DiscoveredDeviceProfile:
    canonical_device_id: str
    runtime_device_id: str
    display_name: str
    summary: str
    vendor: str | None = None
    model_candidates: list[str] = field(default_factory=list)
    device_type: str = "network_device"
    capabilities: list[str] = field(default_factory=list)
    protocols: list[str] = field(default_factory=list)
    identifiers: dict[str, list[str]] = field(default_factory=dict)
    connections: dict[str, list[str]] = field(default_factory=dict)
    data: dict[str, dict[str, Any]] = field(default_factory=dict)
    operations: list[dict[str, Any]] = field(default_factory=list)
    last_seen: float = 0.0
    evidence_count: int = 0
    online: bool = True
    identity: dict[str, Any] = field(default_factory=dict)
    classification: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_device_profile(
    device: Device,
    canonical_device_id: str,
    data_snapshot: DataSnapshot | None = None,
    operations_tracker: OperationsTracker | None = None,
) -> DiscoveredDeviceProfile:
    entity_prefixes = sorted(device.mqtt_entity_prefixes or device.topic_prefixes)
    searchable_values = [
        *entity_prefixes,
        *device.mqtt_channels,
        *device.payload_keys,
        *device.metadata_capabilities,
        *device.service_types,
        *device.hostnames,
        device.vendor or "",
        device.os_guess or "",
    ]
    semantic_terms = _semantic_terms(searchable_values)
    searchable = " ".join(
        [
            *searchable_values,
        ]
    ).lower()
    capabilities = sorted(
        capability
        for capability, keywords in _CAPABILITY_KEYWORDS.items()
        if any(_keyword_matches(keyword, semantic_terms, searchable) for keyword in keywords)
    )
    vendor = _infer_vendor(device)
    models = _infer_models(device, vendor)
    device_type = _infer_device_type(capabilities)
    protocols = _infer_protocols(device)
    member_ids = sorted(device.member_device_ids or {device.device_id})
    data = _latest_data(member_ids, data_snapshot)
    operations = _operations(member_ids, operations_tracker)
    operations.extend(_metadata_operations(device, operations))
    display_name = _display_name(device, vendor, models, device_type)
    summary = _summary(display_name, capabilities, protocols)

    return DiscoveredDeviceProfile(
        canonical_device_id=canonical_device_id,
        runtime_device_id=device.device_id,
        display_name=display_name,
        summary=summary,
        vendor=vendor,
        model_candidates=models,
        device_type=device_type,
        capabilities=capabilities,
        protocols=protocols,
        identifiers={
            "mqtt_topic_prefix": sorted(device.topic_prefixes),
            "mqtt_identity": sorted(device.mqtt_identities),
            "mqtt_entity_prefix": entity_prefixes,
            "mqtt_entity_identity": sorted(device.mqtt_entity_identities),
            "mqtt_channel": sorted(device.mqtt_channels),
            "hostname": sorted(device.hostnames),
            "ssdp_usn": sorted(device.ssdp_usns),
            "strong_token": sorted(device.identity_tokens),
            "metadata_identifier": sorted(device.metadata_identifiers),
        },
        connections={
            "ip": sorted(device.ip_addresses),
            "mac": sorted(device.mac_addresses),
        },
        data=data,
        operations=operations,
        last_seen=device.last_seen,
        evidence_count=device.total_evidence_count,
        online=True,
        identity={
            "member_runtime_device_ids": member_ids,
            "observed_topic_count": len(device.mqtt_identities),
            "entity_count": len(device.mqtt_entity_identities),
            "channel_count": len(device.mqtt_channels),
            "reasons": list(device.identity_reasons),
            "separation_policy": (
                "Different MQTT entity prefixes remain separate unless they share "
                "an explicit identifier, MAC/hostname evidence, or another strong cross-source signal."
            ),
        },
        classification={
            "method": (
                "explicit MQTT discovery metadata"
                if device.metadata_sources
                else "structural MQTT schema plus semantic fallback"
            ),
            "metadata_sources": sorted(device.metadata_sources),
            "confidence": 0.95 if device.metadata_sources else 0.60,
        },
    )


def identity_aliases(device: Device) -> list[tuple[str, str]]:
    aliases: list[tuple[str, str]] = []
    aliases.extend(("mac", value.upper().replace("-", ":")) for value in sorted(device.mac_addresses))
    aliases.extend(("ssdp_usn", value.strip().lower()) for value in sorted(device.ssdp_usns) if value.strip())
    aliases.extend(("strong_token", value) for value in sorted(device.identity_tokens) if value)
    aliases.extend(
        ("mqtt_entity", value.strip().lower())
        for value in sorted(device.mqtt_entity_identities)
        if value.strip()
    )
    # Retain channel-level aliases so an upgraded registry can merge records
    # created by older versions into the new physical-entity record.
    aliases.extend(
        ("mqtt_identity", value.strip().lower())
        for value in sorted(device.mqtt_identities)
        if value.strip()
    )
    aliases.extend(
        ("hostname", value.strip().lower().rstrip("."))
        for value in sorted(device.hostnames)
        if value.strip()
    )
    return aliases


def _infer_vendor(device: Device) -> str | None:
    if device.explicit_manufacturers:
        return sorted(device.explicit_manufacturers)[0]
    if device.vendor and device.vendor.strip():
        return device.vendor.strip()
    for prefix in sorted(device.mqtt_entity_prefixes or device.topic_prefixes):
        first = prefix.split("/", 1)[0].strip()
        if not first or first.lower() in _GENERIC_VENDOR_TOKENS or _looks_like_identifier(first):
            continue
        return _pretty_token(first)
    return None


def _infer_models(device: Device, vendor: str | None) -> list[str]:
    candidates: list[str] = sorted(device.explicit_models)
    vendor_key = (vendor or "").lower().replace(" ", "")
    for prefix in sorted(device.mqtt_entity_prefixes or device.topic_prefixes):
        for token in re.split(r"[/_.:-]+", prefix):
            clean = token.strip()
            compact = re.sub(r"[^a-z0-9]", "", clean.lower())
            if not clean or compact == vendor_key or _looks_like_identifier(clean):
                continue
            if any(char.isalpha() for char in clean) and any(char.isdigit() for char in clean):
                candidates.append(clean.upper())
    return list(dict.fromkeys(candidates))[:4]


def _infer_device_type(capabilities: list[str]) -> str:
    values = set(capabilities)
    if {"temperature", "humidity"}.issubset(values):
        return "temperature_humidity_sensor"
    if values.intersection({"temperature", "humidity", "pressure", "air_quality"}):
        return "environment_sensor"
    if "motion" in values:
        return "motion_sensor"
    if "contact" in values:
        return "contact_sensor"
    if "camera" in values:
        return "camera"
    if "brightness" in values or ("light" in values and "power" in values):
        return "smart_light"
    if "energy" in values:
        return "energy_monitor"
    if "power" in values:
        return "smart_switch"
    return "network_device"


def _infer_protocols(device: Device) -> list[str]:
    protocols: set[str] = set()
    if device.topic_prefixes:
        protocols.add("MQTT")
    if device.service_types:
        protocols.add("mDNS")
    if device.ssdp_usns:
        protocols.add("SSDP/UPnP")
    if device.mac_addresses or device.os_guess:
        protocols.add("IP")
    return sorted(protocols)


def _latest_data(device_ids: list[str], snapshot: DataSnapshot | None) -> dict[str, dict[str, Any]]:
    if snapshot is None:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for device_id in device_ids:
        for name, reading in snapshot.get_latest(device_id).items():
            current = result.get(name)
            if current is not None and float(current.get("timestamp") or 0.0) > reading.timestamp:
                continue
            result[name] = {
                "value": reading.text_value if reading.text_value is not None else reading.value,
                "unit": reading.unit,
                "timestamp": reading.timestamp,
                "runtime_device_id": device_id,
            }
    return result


def _operations(device_ids: list[str], tracker: OperationsTracker | None) -> list[dict[str, Any]]:
    if tracker is None:
        return []
    operations: dict[tuple[str, str, str], dict[str, Any]] = {}
    for device_id in device_ids:
        for item in tracker.get_capabilities(device_id):
            key = (item.topic, item.action, item.sensor_key or "")
            operations[key] = {
                "topic": item.topic,
                "action": item.action,
                "sensor_key": item.sensor_key,
                "accepted_values": list(item.accepted_values),
                "confidence": item.confidence,
                "first_seen": item.first_seen,
                "last_seen": item.last_seen,
                "runtime_device_id": device_id,
            }
    return list(operations.values())


def _display_name(device: Device, vendor: str | None, models: list[str], device_type: str) -> str:
    if device.explicit_names:
        return sorted(device.explicit_names)[0]
    type_label = _DEVICE_TYPE_LABELS.get(device_type, "network device")
    entity_label = _entity_label(device, vendor)
    parts = [value for value in (vendor, models[0] if models else None, type_label) if value]
    if models:
        return " ".join(parts)
    if vendor and entity_label:
        return f"{vendor} {entity_label} {type_label}"
    if entity_label:
        return f"{entity_label} {type_label}"
    if vendor:
        return f"{vendor} {type_label}"
    if device.hostnames:
        return f"{sorted(device.hostnames)[0].rstrip('.')} ({type_label})"
    if device.topic_prefixes:
        return f"{sorted(device.topic_prefixes)[0]} ({type_label})"
    if device.ip_addresses:
        return f"{sorted(device.ip_addresses)[0]} ({type_label})"
    return type_label.capitalize()


def _entity_label(device: Device, vendor: str | None) -> str:
    prefixes = sorted(device.mqtt_entity_prefixes or device.topic_prefixes)
    if not prefixes:
        return ""
    parts = [part.strip() for part in prefixes[0].split("/") if part.strip()]
    if not parts:
        return ""
    vendor_key = re.sub(r"[^a-z0-9]", "", (vendor or "").casefold())
    filtered = [
        part
        for part in parts
        if re.sub(r"[^a-z0-9]", "", part.casefold()) != vendor_key
        and part.casefold() not in {"discover", "discovery", "op", "ops", "opsebo", "stat", "tele"}
    ]
    if not filtered:
        return ""
    tail = filtered[-3:]
    return " ".join(_pretty_token(part) for part in tail)


def _summary(display_name: str, capabilities: list[str], protocols: list[str]) -> str:
    details: list[str] = []
    if capabilities:
        details.append("capabilities: " + ", ".join(capabilities))
    if protocols:
        details.append("protocols: " + ", ".join(protocols))
    return display_name if not details else f"{display_name}; " + "; ".join(details)


def _metadata_operations(
    device: Device,
    observed: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    existing_topics = {str(item.get("topic") or "") for item in observed}
    result: list[dict[str, Any]] = []
    for topic in sorted(device.mqtt_command_topics):
        if topic in existing_topics:
            continue
        result.append(
            {
                "topic": topic,
                "action": "set",
                "sensor_key": "",
                "accepted_values": sorted(device.mqtt_command_values.get(topic, set())),
                "confidence": 0.98,
                "first_seen": 0.0,
                "last_seen": device.last_seen,
                "runtime_device_id": device.device_id,
                "source": "explicit MQTT discovery metadata",
            }
        )
    return result


def _looks_like_identifier(value: str) -> bool:
    compact = re.sub(r"[^a-fA-F0-9]", "", value)
    if len(compact) >= 8 and len(compact) >= len(value) - 2:
        return True
    return value.isdigit() or len(value) > 24


def _pretty_token(value: str) -> str:
    if value.isupper():
        return value
    return value.replace("_", " ").replace("-", " ").title()


def _semantic_terms(values: list[str]) -> set[str]:
    terms: set[str] = set()
    for value in values:
        spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(value or ""))
        for term in re.split(r"[^A-Za-z0-9]+", spaced.lower()):
            if term:
                terms.add(term)
    return terms


def _keyword_matches(keyword: str, terms: set[str], searchable: str) -> bool:
    normalized = keyword.lower()
    if normalized in terms:
        return True
    if len(normalized) >= 5 and normalized in searchable:
        return True
    return False
