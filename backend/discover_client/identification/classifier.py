"""Heuristic MQTT topic classifier."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

COMMAND_SUFFIXES = {"set", "command", "cmnd", "ctrl", "control"}
TELEMETRY_SUFFIXES = {"state", "status", "data", "telemetry"}
SENSOR_WORDS = {
    "temperature",
    "humidity",
    "pressure",
    "light",
    "motion",
    "voltage",
    "current",
    "power",
    "co2",
    "pm25",
}
ENUM_VALUES = {"on", "off", "toggle", "true", "false", "open", "close"}


@dataclass
class Classification:
    label: str
    confidence: float
    evidence: list[str]


class TopicClassifier:
    def classify(self, topic: str, payload: dict | str | None = None) -> Classification:
        matches: list[Classification] = []
        suffix = _topic_suffix(topic)

        if suffix in COMMAND_SUFFIXES:
            matches.append(Classification("command", 0.85, [f"topic suffix '{suffix}' indicates command"]))
        if _contains_enum_value(payload):
            matches.append(Classification("command", 0.90, ["payload contains enum-like command value"]))
        if suffix in SENSOR_WORDS:
            matches.append(Classification("telemetry", 0.80, [f"topic suffix '{suffix}' is a sensor word"]))
        if suffix in TELEMETRY_SUFFIXES:
            matches.append(Classification("telemetry", 0.70, [f"topic suffix '{suffix}' indicates telemetry"]))
        if _has_numeric_value_with_unit(payload):
            matches.append(Classification("telemetry", 0.75, ["payload contains numeric value with unit"]))

        if not matches:
            return Classification("unknown", 0.30, ["fallback: no command or telemetry rule matched"])

        best = max(matches, key=lambda item: item.confidence)
        return Classification(
            label=best.label,
            confidence=best.confidence,
            evidence=[reason for match in matches for reason in match.evidence],
        )


def _topic_suffix(topic: str | None) -> str:
    if not topic:
        return ""
    return topic.rsplit("/", 1)[-1].strip().lower()


def _contains_enum_value(payload: Any) -> bool:
    if isinstance(payload, str):
        return payload.strip().lower() in ENUM_VALUES
    if isinstance(payload, dict):
        return any(_contains_enum_value(value) for value in payload.values())
    return False


def _has_numeric_value_with_unit(payload: Any) -> bool:
    if not isinstance(payload, dict) or "unit" not in payload:
        return False
    return any(isinstance(value, (int, float)) and not isinstance(value, bool) for value in payload.values())
