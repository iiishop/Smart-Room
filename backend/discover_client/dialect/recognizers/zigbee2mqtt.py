"""Recognizer for Zigbee2MQTT format — TODO."""

from typing import Any

from discover_client.dialect.recognizer import DialectRecognizer, RecognizerOutput


class Zigbee2MQTTRecognizer(DialectRecognizer):
    SPECIFICITY = 80

    def match(self, topic: str, payload: Any) -> float:
        return 0.0  # stub

    def extract(self, topic: str, payload: Any) -> RecognizerOutput:
        return RecognizerOutput()
