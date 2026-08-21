"""Recognizer for Zigbee2MQTT format (zigbee2mqtt/<friendly_name>/...)."""

from typing import Any

from discover_client.dialect.recognizer import (
    DialectRecognizer,
    RecognizedOperation,
    RecognizedSensor,
    RecognizerOutput,
)
from discover_client.dialect.registry import register_recognizer
from discover_client.dialect.utils import _coerce_value, _extract_accepted_values


@register_recognizer("zigbee2mqtt")
class Zigbee2MQTTRecognizer(DialectRecognizer):
    SPECIFICITY = 80

    def match(self, topic: str, payload: Any) -> float:
        if not topic.startswith("zigbee2mqtt/"):
            return 0.0
        if "/bridge/" in topic:
            return 0.0
        return 0.85

    def extract(self, topic: str, payload: Any) -> RecognizerOutput:
        segments = topic.split("/")
        friendly_name = segments[1] if len(segments) > 1 else "unknown"
        is_command_topic = len(segments) >= 3 and segments[2].lower() == "set"

        ops: list[RecognizedOperation] = []
        sensors: list[RecognizedSensor] = []

        if isinstance(payload, dict):
            for k, v in payload.items():
                # Always create an operation per key
                if is_command_topic:
                    ops.append(
                        RecognizedOperation(
                            topic=topic,
                            action="set",
                            sensor_key=k,
                            accepted_values=_extract_accepted_values(v),
                            is_enum=isinstance(v, str) and bool(_extract_accepted_values(v)),
                        )
                    )
                # Only create a sensor for non-dict values
                if not is_command_topic and not isinstance(v, dict):
                    sensors.append(
                        RecognizedSensor(
                            sensor_type=k,
                            value=_coerce_value(v),
                        )
                    )
        elif is_command_topic:
            sensor_key = segments[3] if len(segments) > 3 else None
            ops.append(
                RecognizedOperation(
                    topic=topic,
                    action="set",
                    sensor_key=sensor_key,
                    accepted_values=_extract_accepted_values(payload),
                    is_enum=isinstance(payload, str),
                )
            )

        return RecognizerOutput(
            operations=ops,
            sensor_readings=sensors,
            dialect_hint="zigbee2mqtt",
            device_id_hint=friendly_name,
        )
