"""Recognizer for deep-topic + bare value format (e.g., mock/light-1/set/power ← "ON")."""

from typing import Any

from discover_client.dialect.recognizer import (
    DialectRecognizer,
    RecognizedOperation,
    RecognizedSensor,
    RecognizerOutput,
)
from discover_client.dialect.utils import (
    COMMAND_SUFFIXES,
    _coerce_value,
    _extract_accepted_values,
)


class SubTopicRecognizer(DialectRecognizer):
    SPECIFICITY = 50

    def match(self, topic: str, payload: Any) -> float:
        if isinstance(payload, dict):
            return 0.0
        segments = topic.split("/")
        if len(segments) < 3:
            return 0.0
        suffix = segments[-2].lower()
        return 0.85 if suffix in COMMAND_SUFFIXES else 0.0

    def extract(self, topic: str, payload: Any) -> RecognizerOutput:
        segments = topic.split("/")
        sensor_key = segments[-1].lower()
        action = COMMAND_SUFFIXES.get(segments[-2].lower(), "command")
        return RecognizerOutput(
            operations=[
                RecognizedOperation(
                    topic=topic,
                    action=action,
                    sensor_key=sensor_key,
                    accepted_values=_extract_accepted_values(payload),
                    is_enum=isinstance(payload, str),
                )
            ],
            sensor_readings=[
                RecognizedSensor(
                    sensor_type=sensor_key,
                    value=_coerce_value(payload),
                )
            ],
            dialect_hint="subtopic",
        )
