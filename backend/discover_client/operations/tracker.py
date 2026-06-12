"""Track MQTT command-like topics as device operations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from discover_client.identification.evidence import SignalEvidence

COMMAND_SUFFIX_CONFIDENCE = 0.85
TELEMETRY_SUFFIX_CONFIDENCE = 0.70
SENSOR_WORD_CONFIDENCE = 0.80
ENUM_PAYLOAD_CONFIDENCE = 0.90
UNIT_PAYLOAD_CONFIDENCE = 0.75
UNKNOWN_CONFIDENCE = 0.30

COMMAND_SUFFIXES = {
    "set": "set",
    "command": "command",
    "cmnd": "command",
    "ctrl": "control",
    "control": "control",
    "switch": "switch",
    "toggle": "toggle",
    "power": "power",
    "relay": "switch",
}
TELEMETRY_SUFFIXES = {"state", "status", "data", "telemetry", "sensor", "reading"}
SENSOR_WORDS = {"temperature", "humidity", "pressure", "light", "motion"}
ENUM_VALUES = {"on", "off", "toggle", "true", "false", "open", "close"}


@dataclass
class OperationCapability:
    device_id: str
    topic: str
    action: str
    accepted_values: list[str]
    confidence: float
    first_seen: float
    last_seen: float


class OperationsTracker:
    def __init__(self) -> None:
        self._capabilities: dict[str, dict[str, OperationCapability]] = {}

    def ingest(self, device_id: str, evidence: SignalEvidence) -> OperationCapability | None:
        if evidence.source_type != "mqtt" or not evidence.mqtt_topic:
            return None

        category, confidence = _classify(evidence)
        if category != "command":
            return None

        action = _extract_action(evidence.mqtt_topic)
        values = _extract_values(evidence.mqtt_payload)
        by_topic = self._capabilities.setdefault(device_id, {})
        existing = by_topic.get(evidence.mqtt_topic)

        if existing is None:
            capability = OperationCapability(
                device_id=device_id,
                topic=evidence.mqtt_topic,
                action=action,
                accepted_values=sorted(values),
                confidence=confidence,
                first_seen=evidence.timestamp,
                last_seen=evidence.timestamp,
            )
            by_topic[evidence.mqtt_topic] = capability
            return replace(capability)

        merged_values = sorted(set(existing.accepted_values).union(values))
        changed = False
        if merged_values != existing.accepted_values:
            existing.accepted_values = merged_values
            changed = True
        if confidence > existing.confidence:
            existing.confidence = confidence
            changed = True
        if action != existing.action:
            existing.action = action
            changed = True
        if evidence.timestamp != existing.last_seen:
            existing.last_seen = evidence.timestamp
            changed = True

        return replace(existing) if changed else None

    def get_capabilities(self, device_id: str) -> list[OperationCapability]:
        caps = self._capabilities.get(device_id, {})
        return [replace(capability) for _, capability in sorted(caps.items())]

    def get_all(self) -> dict[str, list[OperationCapability]]:
        return {
            device_id: self.get_capabilities(device_id)
            for device_id in sorted(self._capabilities)
        }


def _classify(evidence: SignalEvidence) -> tuple[str, float]:
    if evidence.source_type != "mqtt":
        return "unknown", UNKNOWN_CONFIDENCE

    topic_suffix = _topic_suffix(evidence.mqtt_topic)
    scores: list[tuple[str, float]] = []

    if topic_suffix in COMMAND_SUFFIXES:
        scores.append(("command", COMMAND_SUFFIX_CONFIDENCE))
    if topic_suffix in TELEMETRY_SUFFIXES:
        scores.append(("telemetry", TELEMETRY_SUFFIX_CONFIDENCE))
    if any(word in (evidence.mqtt_topic or "").lower().split("/") for word in SENSOR_WORDS):
        scores.append(("telemetry", SENSOR_WORD_CONFIDENCE))
    if _extract_values(evidence.mqtt_payload):
        scores.append(("command", ENUM_PAYLOAD_CONFIDENCE))
    if _has_numeric_value_with_unit(evidence.mqtt_payload):
        scores.append(("telemetry", UNIT_PAYLOAD_CONFIDENCE))

    if not scores:
        return "unknown", UNKNOWN_CONFIDENCE
    return max(scores, key=lambda item: item[1])


def _extract_action(topic: str) -> str:
    return COMMAND_SUFFIXES.get(_topic_suffix(topic), "command")


def _topic_suffix(topic: str | None) -> str:
    if not topic:
        return ""
    return topic.rsplit("/", 1)[-1].strip().lower()


def _extract_values(payload: Any) -> set[str]:
    values: set[str] = set()
    if isinstance(payload, str):
        normalized = _normalize_enum(payload)
        if normalized is not None:
            values.add(normalized)
        return values
    if isinstance(payload, dict):
        for value in payload.values():
            values.update(_extract_values(value))
    return values


def _normalize_enum(value: str) -> str | None:
    text = value.strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered not in ENUM_VALUES:
        return None
    return text.upper() if lowered in {"on", "off", "toggle"} else lowered


def _has_numeric_value_with_unit(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if "unit" not in payload:
        return False
    for value in payload.values():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
    return False
