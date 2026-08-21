"""Recognizer for bare value format — universal fallback."""

from typing import Any

from discover_client.dialect.recognizer import (
    DialectRecognizer,
    RecognizedOperation,
    RecognizedSensor,
    RecognizerOutput,
)
from discover_client.dialect.registry import register_recognizer
from discover_client.dialect.utils import _bare_value_confidence, _coerce_value


@register_recognizer("barevalue")
class BareValueRecognizer(DialectRecognizer):
    SPECIFICITY = 10  # lowest — always loses to specialized recognizers

    def match(self, topic: str, payload: Any) -> float:
        return min(0.50, _bare_value_confidence(topic, payload))

    def extract(self, topic: str, payload: Any) -> RecognizerOutput:
        return RecognizerOutput(
            operations=[],
            sensor_readings=[
                RecognizedSensor(
                    sensor_type="value",
                    value=_coerce_value(payload),
                )
            ],
            dialect_hint="barevalue",
        )
