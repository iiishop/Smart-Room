"""Explicit MQTT device metadata from standardized discovery conventions."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from discover_client.identification.device import Device
from discover_client.identification.tokens import extract_identity_tokens


@dataclass
class MqttDeviceMetadata:
    source_id: str
    identity: str
    topic_prefix: str
    convention: str
    name: str = ""
    manufacturer: str = ""
    model: str = ""
    description: str = ""
    identifiers: set[str] = field(default_factory=set)
    capabilities: set[str] = field(default_factory=set)
    state_topics: set[str] = field(default_factory=set)
    command_topics: set[str] = field(default_factory=set)
    command_values: dict[str, set[str]] = field(default_factory=dict)
    match_prefixes: set[str] = field(default_factory=set)

    @property
    def identity_tokens(self) -> set[str]:
        scoped = {
            f"{self.source_id}|{_normalize_identifier(value)}"
            for value in self.identifiers | {self.identity}
            if _normalize_identifier(value)
        }
        strong: set[str] = set()
        for value in self.identifiers:
            strong.update(extract_identity_tokens(value))
        return scoped | strong


class MqttMetadataIndex:
    """Index retained discovery metadata without guessing from topic keywords."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], MqttDeviceMetadata] = {}
        self._topics: dict[tuple[str, str], tuple[str, str]] = {}
        self._prefixes: dict[tuple[str, str], tuple[str, str]] = {}
        self._updated_records: list[MqttDeviceMetadata] = []

    def ingest(self, source_id: str, topic: str, payload: Any) -> bool:
        if _is_home_assistant_config(topic):
            if isinstance(payload, dict) and payload:
                self._store(_home_assistant_metadata(source_id, topic, payload))
            return True

        if topic.casefold().endswith("/bridge/devices") and isinstance(payload, list):
            for metadata in _zigbee2mqtt_metadata(source_id, topic, payload):
                self._store(metadata)
            return True

        if _is_tasmota_discovery_config(topic):
            if isinstance(payload, dict) and payload:
                self._store(_tasmota_metadata(source_id, topic, payload))
            return True

        if _is_tasmota_discovery_sensors(topic):
            return True

        return False

    def drain_updates(self) -> list[MqttDeviceMetadata]:
        updates = list(self._updated_records)
        self._updated_records.clear()
        return updates

    def lookup(self, source_id: str, topic: str) -> MqttDeviceMetadata | None:
        key = self._topics.get((source_id, topic))
        if key is not None:
            return self._records.get(key)

        candidates = [
            (prefix, record_key)
            for (candidate_source, prefix), record_key in self._prefixes.items()
            if candidate_source == source_id
            and (topic == prefix or topic.startswith(prefix + "/"))
        ]
        if not candidates:
            return None
        _, record_key = max(candidates, key=lambda item: len(item[0]))
        return self._records.get(record_key)

    def enrich_device(self, device: Device, metadata: MqttDeviceMetadata) -> None:
        if metadata.name:
            device.explicit_names.add(metadata.name)
        if metadata.manufacturer:
            device.explicit_manufacturers.add(metadata.manufacturer)
        if metadata.model:
            device.explicit_models.add(metadata.model)
        if metadata.description:
            device.explicit_descriptions.add(metadata.description)
        device.metadata_capabilities.update(metadata.capabilities)
        device.metadata_identifiers.update(metadata.identifiers)
        device.metadata_sources.add(metadata.convention)
        device.identity_tokens.update(metadata.identity_tokens)
        device.mqtt_command_topics.update(metadata.command_topics)
        for topic, values in metadata.command_values.items():
            device.mqtt_command_values.setdefault(topic, set()).update(values)
        reason = (
            f"{metadata.convention} explicitly maps MQTT topics to device "
            f"identifier {metadata.identity}"
        )
        if reason not in device.identity_reasons:
            device.identity_reasons.append(reason)

    def _store(self, metadata: MqttDeviceMetadata) -> None:
        key = (metadata.source_id, metadata.identity)
        current = self._records.get(key)
        if current is None:
            current = metadata
            self._records[key] = current
        else:
            _merge_metadata(current, metadata)

        for topic in current.state_topics | current.command_topics:
            self._topics[(current.source_id, topic)] = key
        if current.topic_prefix:
            self._prefixes[(current.source_id, current.topic_prefix)] = key
        for prefix in current.match_prefixes:
            if prefix:
                self._prefixes[(current.source_id, prefix)] = key
        self._updated_records.append(current)


def _is_home_assistant_config(topic: str) -> bool:
    parts = [part for part in topic.split("/") if part]
    return len(parts) >= 4 and parts[0].casefold() == "homeassistant" and parts[-1].casefold() == "config"


def _is_tasmota_discovery_config(topic: str) -> bool:
    parts = [part for part in topic.split("/") if part]
    return (
        len(parts) == 4
        and parts[0].casefold() == "tasmota"
        and parts[1].casefold() == "discovery"
        and parts[3].casefold() == "config"
    )


def _is_tasmota_discovery_sensors(topic: str) -> bool:
    parts = [part for part in topic.split("/") if part]
    return (
        len(parts) == 4
        and parts[0].casefold() == "tasmota"
        and parts[1].casefold() == "discovery"
        and parts[3].casefold() == "sensors"
    )


def _home_assistant_metadata(
    source_id: str,
    topic: str,
    payload: dict[str, Any],
) -> MqttDeviceMetadata:
    parts = [part for part in topic.split("/") if part]
    component = parts[1] if len(parts) > 1 else ""
    object_id = "/".join(parts[2:-1])
    device = _first_dict(payload, "device", "dev")
    identifiers = set(_string_values(_first_value(device, "identifiers", "ids")))
    for connection in _first_value(device, "connections", "cns") or []:
        if isinstance(connection, (list, tuple)) and len(connection) >= 2:
            identifiers.add(str(connection[1]))

    unique_id = str(_first_value(payload, "unique_id", "uniq_id") or "").strip()
    if unique_id:
        identifiers.add(unique_id)
    identity = next(iter(sorted(identifiers)), "") or object_id or topic
    state_topics = set(
        _string_values(
            [
                _first_value(payload, "state_topic", "stat_t"),
                _first_value(payload, "json_attributes_topic", "json_attr_t"),
            ]
        )
    )
    command_topics = set(
        _string_values([_first_value(payload, "command_topic", "cmd_t")])
    )
    command_values = {
        command_topic: set(
            _string_values(
                [
                    _first_value(payload, "payload_on", "pl_on"),
                    _first_value(payload, "payload_off", "pl_off"),
                    _first_value(payload, "payload_open", "pl_open"),
                    _first_value(payload, "payload_close", "pl_cls"),
                ]
            )
        )
        for command_topic in command_topics
    }
    capability_values = [
        component,
        _first_value(payload, "device_class", "dev_cla"),
        _first_value(payload, "name"),
    ]
    return MqttDeviceMetadata(
        source_id=source_id,
        identity=identity,
        topic_prefix=f"homeassistant-device/{_normalize_identifier(identity)}",
        convention="Home Assistant MQTT discovery",
        name=str(_first_value(device, "name") or _first_value(payload, "name") or "").strip(),
        manufacturer=str(_first_value(device, "manufacturer", "mf") or "").strip(),
        model=str(_first_value(device, "model", "mdl") or "").strip(),
        description=str(_first_value(device, "hw_version", "hw") or "").strip(),
        identifiers=identifiers,
        capabilities=set(_string_values(capability_values)),
        state_topics=state_topics,
        command_topics=command_topics,
        command_values=command_values,
    )


def _zigbee2mqtt_metadata(
    source_id: str,
    topic: str,
    payload: list[Any],
) -> list[MqttDeviceMetadata]:
    base = topic[: -len("/bridge/devices")].strip("/")
    records: list[MqttDeviceMetadata] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "").casefold() == "coordinator":
            continue
        friendly_name = str(item.get("friendly_name") or "").strip()
        ieee_address = str(item.get("ieee_address") or "").strip()
        if not friendly_name and not ieee_address:
            continue
        definition = item.get("definition") if isinstance(item.get("definition"), dict) else {}
        prefix = f"{base}/{friendly_name}" if friendly_name else base
        capabilities, writable, values = _zigbee_exposes(definition.get("exposes"))
        command_topics = {f"{prefix}/set"} if writable else set()
        records.append(
            MqttDeviceMetadata(
                source_id=source_id,
                identity=ieee_address or friendly_name,
                topic_prefix=prefix,
                convention="Zigbee2MQTT bridge metadata",
                name=friendly_name,
                manufacturer=str(definition.get("vendor") or "").strip(),
                model=str(definition.get("model") or "").strip(),
                description=str(definition.get("description") or "").strip(),
                identifiers={value for value in (ieee_address, friendly_name) if value},
                capabilities=capabilities,
                state_topics={prefix},
                command_topics=command_topics,
                command_values={f"{prefix}/set": values} if writable else {},
            )
        )
    return records


def _tasmota_metadata(
    source_id: str,
    discovery_topic: str,
    payload: dict[str, Any],
) -> MqttDeviceMetadata:
    parts = [part for part in discovery_topic.split("/") if part]
    discovery_id = parts[2] if len(parts) >= 3 else ""
    topic_name = str(payload.get("t") or discovery_id).strip()
    full_topic = str(payload.get("ft") or "%prefix%/%topic%/").strip()
    prefixes = _string_values(payload.get("tp"))
    command_prefix = prefixes[0] if len(prefixes) >= 1 else "cmnd"
    status_prefix = prefixes[1] if len(prefixes) >= 2 else "stat"
    telemetry_prefix = prefixes[2] if len(prefixes) >= 3 else "tele"
    command_root = _render_tasmota_topic(full_topic, topic_name, command_prefix)
    status_root = _render_tasmota_topic(full_topic, topic_name, status_prefix)
    telemetry_root = _render_tasmota_topic(full_topic, topic_name, telemetry_prefix)
    entity_prefix = _tasmota_entity_prefix(full_topic, topic_name)

    relays = payload.get("rl") if isinstance(payload.get("rl"), list) else []
    relay_indexes = [
        index + 1
        for index, relay in enumerate(relays)
        if isinstance(relay, (int, float)) and relay > 0
    ]
    state_values = set(_string_values(payload.get("state"))) or {"OFF", "ON", "TOGGLE"}
    command_topics: set[str] = set()
    for relay_index in relay_indexes:
        command_name = "POWER" if relay_index == 1 else f"POWER{relay_index}"
        command_topics.add(_join_topic(command_root, command_name))

    friendly_names = _string_values(payload.get("fn"))
    name = next((value for value in friendly_names if value.strip()), "")
    mac = str(payload.get("mac") or discovery_id).strip()
    hostname = str(payload.get("hn") or "").strip()
    identifiers = {value for value in (mac, hostname, discovery_id) if value}
    state_topics = {
        _join_topic(status_root, suffix)
        for suffix in ("RESULT", "POWER", "STATE")
    } | {
        _join_topic(telemetry_root, suffix)
        for suffix in ("STATE", "SENSOR", "LWT")
    }
    return MqttDeviceMetadata(
        source_id=source_id,
        identity=mac or topic_name,
        topic_prefix=entity_prefix,
        convention="Tasmota discovery metadata",
        name=name or str(payload.get("dn") or topic_name).strip(),
        manufacturer="Tasmota",
        model=str(payload.get("md") or "").strip(),
        description=(
            f"Tasmota {payload.get('sw')}" if payload.get("sw") else "Tasmota MQTT device"
        ),
        identifiers=identifiers,
        capabilities={"power"} if command_topics else set(),
        state_topics=state_topics,
        command_topics=command_topics,
        command_values={topic: set(state_values) for topic in command_topics},
        match_prefixes={
            prefix
            for prefix in (command_root, status_root, telemetry_root)
            if prefix
        },
    )


def _zigbee_exposes(value: Any) -> tuple[set[str], bool, set[str]]:
    capabilities: set[str] = set()
    accepted_values: set[str] = set()
    writable = False

    def visit(node: Any) -> None:
        nonlocal writable
        if isinstance(node, list):
            for child in node:
                visit(child)
            return
        if not isinstance(node, dict):
            return
        property_name = str(node.get("property") or node.get("name") or "").strip()
        if property_name:
            capabilities.add(property_name)
        try:
            writable = writable or bool(int(node.get("access") or 0) & 2)
        except (TypeError, ValueError):
            pass
        accepted_values.update(_string_values(node.get("values")))
        visit(node.get("features"))

    visit(value)
    return capabilities, writable, accepted_values


def _merge_metadata(target: MqttDeviceMetadata, source: MqttDeviceMetadata) -> None:
    for field_name in ("name", "manufacturer", "model", "description", "topic_prefix"):
        if not getattr(target, field_name) and getattr(source, field_name):
            setattr(target, field_name, getattr(source, field_name))
    target.identifiers.update(source.identifiers)
    target.capabilities.update(source.capabilities)
    target.state_topics.update(source.state_topics)
    target.command_topics.update(source.command_topics)
    target.match_prefixes.update(source.match_prefixes)
    for topic, values in source.command_values.items():
        target.command_values.setdefault(topic, set()).update(values)


def _first_dict(payload: dict[str, Any], *keys: str) -> dict[str, Any]:
    value = _first_value(payload, *keys)
    return value if isinstance(value, dict) else {}


def _first_value(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _string_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        result: list[str] = []
        for item in value:
            result.extend(_string_values(item))
        return result
    return [str(value)]


def _normalize_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _render_tasmota_topic(full_topic: str, topic_name: str, prefix: str) -> str:
    rendered = (
        str(full_topic or "%prefix%/%topic%/")
        .replace("%prefix%", str(prefix or ""))
        .replace("%topic%", str(topic_name or ""))
    )
    return "/".join(part for part in rendered.split("/") if part)


def _tasmota_entity_prefix(full_topic: str, topic_name: str) -> str:
    rendered = (
        str(full_topic or "%prefix%/%topic%/")
        .replace("%prefix%", "")
        .replace("%topic%", str(topic_name or ""))
    )
    return "/".join(part for part in rendered.split("/") if part)


def _join_topic(prefix: str, suffix: str) -> str:
    return "/".join(
        part.strip("/")
        for part in (prefix, suffix)
        if str(part or "").strip("/")
    )
