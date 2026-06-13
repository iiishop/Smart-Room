"""MQTT dialect recognition — pluggable recognizer registry."""

from discover_client.dialect.recognizer import (
    DialectRecognizer,
    RecognizedOperation,
    RecognizedSensor,
    RecognizerOutput,
)

RECOGNIZERS: dict[str, type[DialectRecognizer]] = {}


def register_recognizer(name: str):
    def decorator(cls: type[DialectRecognizer]):
        RECOGNIZERS[name] = cls
        return cls
    return decorator


# Auto-load all recognizers on import
from discover_client.dialect.recognizers import flatdict, subtopic, tasmota, zigbee2mqtt, barevalue  # noqa: E402, F401

__all__ = [
    "DialectRecognizer",
    "RECOGNIZERS",
    "register_recognizer",
    "RecognizedOperation",
    "RecognizedSensor",
    "RecognizerOutput",
]
