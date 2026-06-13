"""DialectRecognizer ABC and output types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RecognizedOperation:
    topic: str
    action: str
    sensor_key: str | None = None
    accepted_values: list[str] = field(default_factory=list)
    payload_example: Any = None
    is_enum: bool = False


@dataclass
class RecognizedSensor:
    sensor_type: str
    value: Any
    unit: str = ""


@dataclass
class RecognizerOutput:
    operations: list[RecognizedOperation] = field(default_factory=list)
    sensor_readings: list[RecognizedSensor] = field(default_factory=list)
    message_type: str = "data"
    dialect_hint: str | None = None
    device_id_hint: str | None = None


class DialectRecognizer(ABC):
    SPECIFICITY: int = 50

    @abstractmethod
    def match(self, topic: str, payload: Any) -> float:
        ...

    @abstractmethod
    def extract(self, topic: str, payload: Any) -> RecognizerOutput:
        ...
