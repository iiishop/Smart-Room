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


# Auto-load all recognizers on import (resilient to incremental builds)
import importlib as _importlib

_RECOGNIZER_MODULES = ("flatdict", "subtopic", "tasmota", "zigbee2mqtt", "barevalue")
for _mod in _RECOGNIZER_MODULES:
    try:
        _importlib.import_module(f"discover_client.dialect.recognizers.{_mod}")
    except ImportError:
        pass  # module not yet implemented — skip gracefully

__all__ = [
    "DialectRecognizer",
    "RECOGNIZERS",
    "register_recognizer",
    "RecognizedOperation",
    "RecognizedSensor",
    "RecognizerOutput",
]
