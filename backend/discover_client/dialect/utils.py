"""Shared helpers for dialect recognizers."""

from typing import Any

COMMAND_SUFFIXES = {
    "set": "set",
    "command": "command",
    "cmnd": "command",
    "ctrl": "control",
    "control": "control",
    "switch": "switch",
    "toggle": "toggle",
    "relay": "switch",
}
TELEMETRY_SUFFIXES = {"state", "status", "data", "telemetry", "sensor", "reading"}
ENUM_VALUES = {"on", "off", "toggle", "true", "false", "open", "close"}


def _topic_suffix(topic: str) -> str:
    return topic.rsplit("/", 1)[-1].strip().lower() if topic else ""


def _extract_accepted_values(payload: Any) -> list[str]:
    if isinstance(payload, str):
        val = payload.strip().upper()
        return [val] if val.lower() in ENUM_VALUES else []
    return []


def _coerce_value(payload: Any) -> Any:
    if isinstance(payload, str):
        stripped = payload.strip()
        try:
            return float(stripped)
        except ValueError:
            return stripped
    if isinstance(payload, (int, float)) and not isinstance(payload, bool):
        return float(payload)
    return payload


def _bare_value_confidence(topic: str, payload: Any) -> float:
    base = 0.10
    suffix = _topic_suffix(topic)
    if suffix in {"power", "state", "temperature", "humidity", "brightness", "set", "toggle"}:
        base += 0.30
    if isinstance(payload, str) and payload.strip().upper() in {"ON", "OFF", "TRUE", "FALSE", "TOGGLE", "OPEN", "CLOSE"}:
        base += 0.20
    if isinstance(payload, (int, float)) and not isinstance(payload, bool):
        base += 0.20
    if len(topic.split("/")) > 3:
        base += 0.10
    return min(base, 0.50)
