"""Recognizer for single-topic + dict payload (mock_light format)."""

from typing import Any

from discover_client.dialect.recognizer import DialectRecognizer, RecognizerOutput, RecognizedOperation, RecognizedSensor
from discover_client.dialect.registry import register_recognizer
from discover_client.dialect.utils import _extract_accepted_values, _coerce_value, _topic_suffix, COMMAND_SUFFIXES

_SERVICE_PREFIXES = {"cmnd/", "stat/", "tele/", "zigbee2mqtt/", "homeassistant/"}
_METADATA_KEYS = {"_announce", "timestamp", "device", "type", "mac", "event"}


@register_recognizer("flatdict")
class FlatDictRecognizer(DialectRecognizer):
    SPECIFICITY = 40

    def match(self, topic: str, payload: Any) -> float:
        if not isinstance(payload, dict):
            return 0.0
        if any(topic.startswith(p) for p in _SERVICE_PREFIXES):
            return 0.0
        return 0.80  # high confidence for dict payloads

    def extract(self, topic: str, payload: Any) -> RecognizerOutput:
        operations = []
        sensors = []
        suffix = _topic_suffix(topic)
        action = COMMAND_SUFFIXES.get(suffix, "set")

        for key, val in payload.items():
            if key in _METADATA_KEYS:
                continue
            if isinstance(val, bool):
                continue

            operations.append(RecognizedOperation(
                topic=topic,
                action=action,
                sensor_key=key,
                accepted_values=_extract_accepted_values(val),
                is_enum=isinstance(val, str) and _extract_accepted_values(val) != [],
            ))

            sensors.append(RecognizedSensor(
                sensor_type=key,
                value=_coerce_value(val),
            ))

        return RecognizerOutput(
            operations=operations,
            sensor_readings=sensors,
            dialect_hint="flatdict",
        )
