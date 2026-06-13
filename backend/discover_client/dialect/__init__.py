"""MQTT dialect recognition — pluggable recognizer registry.

Each dialect is a DialectRecognizer subclass registered via @register_recognizer.
The aggregator scores all, normalizes keys, deduplicates, fans out to
OperationsTracker and DataSnapshot.
"""

from discover_client.dialect.recognizer import (
    DialectRecognizer,
    RecognizedOperation,
    RecognizedSensor,
    RecognizerOutput,
)
from discover_client.dialect.registry import RECOGNIZERS, register_recognizer

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
