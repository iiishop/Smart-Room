"""Recognizer for Tasmota MQTT format (cmnd/stat/tele prefixes)."""

from typing import Any

from discover_client.dialect.recognizer import (
    DialectRecognizer,
    RecognizedOperation,
    RecognizedSensor,
    RecognizerOutput,
)
from discover_client.dialect.registry import register_recognizer
from discover_client.dialect.utils import _coerce_value, _extract_accepted_values


@register_recognizer("tasmota")
class TasmotaRecognizer(DialectRecognizer):
    SPECIFICITY = 90

    def match(self, topic: str, payload: Any) -> float:
        return 0.92 if topic.startswith(("cmnd/", "stat/", "tele/")) else 0.0

    def extract(self, topic: str, payload: Any) -> RecognizerOutput:
        segments = topic.split("/")
        sensor_key = segments[-1] if len(segments) > 1 else "value"
        suffix = segments[-1].upper()
        # RESULT is command echo — no real operation
        is_result = suffix in {"RESULT", "STATUS"}

        ops: list[RecognizedOperation] = []
        sensors: list[RecognizedSensor] = []

        if isinstance(payload, dict):
            for k, v in payload.items():
                if not is_result:
                    ops.append(
                        RecognizedOperation(
                            topic=topic,
                            action="set",
                            sensor_key=k.lower(),
                            accepted_values=_extract_accepted_values(v),
                            is_enum=isinstance(v, str),
                        )
                    )
                sensors.append(
                    RecognizedSensor(
                        sensor_type=k.lower(),
                        value=_coerce_value(v),
                    )
                )
        elif not is_result:
            ops.append(
                RecognizedOperation(
                    topic=topic,
                    action="set",
                    sensor_key=sensor_key.lower(),
                    accepted_values=_extract_accepted_values(payload),
                    is_enum=True,
                )
            )
            sensors.append(
                RecognizedSensor(
                    sensor_type=sensor_key.lower(),
                    value=_coerce_value(payload),
                )
            )

        return RecognizerOutput(
            operations=ops,
            sensor_readings=sensors,
            dialect_hint="tasmota",
        )
