"""Recent per-device sensor readings extracted from MQTT data events."""

from __future__ import annotations

from dataclasses import dataclass
import time


@dataclass
class SensorReading:
    sensor_type: str
    value: float
    unit: str
    timestamp: float
    text_value: str | None = None


class DataSnapshot:
    """Per-device recent sensor readings."""

    _TOPIC_FALLBACK_SEGMENTS = {"data", "state", "status", "telemetry", "sensor", "sensors"}

    def __init__(self, retention_s: float = 60) -> None:
        self._retention_s = retention_s
        self._readings: dict[str, dict[str, list[SensorReading]]] = {}
        self._latest_timestamp = 0.0

    def ingest(self, device_id: str, event: dict) -> list[SensorReading] | None:
        timestamp = float(event.get("timestamp", time.time()))
        self._latest_timestamp = max(self._latest_timestamp, timestamp)
        produced: list[SensorReading] = []
        processed_keys: set[str] = set()

        # Numeric extraction (e.g. brightness: 100)
        extraction = _extract_numeric_reading(event.get("value"))
        if extraction is not None:
            value, unit, payload_hint = extraction
            topic = str(event.get("topic", "") or "")
            sensor_type = _infer_sensor_type(topic, payload_hint, self._TOPIC_FALLBACK_SEGMENTS)
            if sensor_type:
                reading = SensorReading(sensor_type=sensor_type, value=value, unit=unit, timestamp=timestamp)
                device_readings = self._readings.setdefault(device_id, {})
                device_readings.setdefault(sensor_type, []).append(reading)
                produced.append(reading)
                processed_keys.add(sensor_type.lower())

        # Text / enum extraction (e.g. power: "OFF")
        text_readings = _extract_text_readings(event.get("value"))
        if text_readings:
            for sensor_key, text_val in text_readings:
                if sensor_key.lower() in processed_keys:
                    continue
                reading = SensorReading(sensor_type=sensor_key, value=0.0, unit="", timestamp=timestamp, text_value=text_val)
                device_readings = self._readings.setdefault(device_id, {})
                device_readings.setdefault(sensor_key, []).append(reading)
                produced.append(reading)

        if produced:
            self._prune_device(device_id, now=timestamp)
            return produced
        return None

    def get_latest(self, device_id: str) -> dict[str, SensorReading]:
        self._prune_all()
        device_readings = self._readings.get(device_id, {})
        return {
            sensor_type: readings[-1]
            for sensor_type, readings in device_readings.items()
            if readings
        }

    def get_all(self) -> dict[str, dict[str, list[SensorReading]]]:
        self._prune_all()
        return {
            device_id: {
                sensor_type: list(readings)
                for sensor_type, readings in sensor_readings.items()
                if readings
            }
            for device_id, sensor_readings in self._readings.items()
            if sensor_readings
        }

    def _prune_all(self) -> None:
        self._prune_with_now(self._latest_timestamp or time.time())

    def _prune_with_now(self, now: float) -> None:
        for device_id in list(self._readings):
            self._prune_device(device_id, now)

    def _prune_device(self, device_id: str, now: float) -> None:
        device_readings = self._readings.get(device_id)
        if device_readings is None:
            return

        cutoff = now - self._retention_s
        for sensor_type in list(device_readings):
            retained = [reading for reading in device_readings[sensor_type] if reading.timestamp >= cutoff]
            if retained:
                device_readings[sensor_type] = retained
            else:
                del device_readings[sensor_type]

        if not device_readings:
            del self._readings[device_id]


def _extract_numeric_reading(payload: object) -> tuple[float, str, str | None] | None:
    if isinstance(payload, bool) or payload is None:
        return None
    if isinstance(payload, (int, float)):
        return float(payload), "", None
    if isinstance(payload, str):
        try:
            return float(payload.strip()), "", None
        except ValueError:
            return None
    if not isinstance(payload, dict):
        return None

    value = payload.get("value")
    if not isinstance(value, bool) and isinstance(value, (int, float)):
        unit = payload.get("unit")
        return float(value), str(unit) if isinstance(unit, str) else "", "value"

    numeric_items = [
        (key, raw_value)
        for key, raw_value in payload.items()
        if not isinstance(raw_value, bool) and isinstance(raw_value, (int, float))
    ]
    if len(numeric_items) == 1:
        key, raw_value = numeric_items[0]
        unit = payload.get("unit")
        return float(raw_value), str(unit) if isinstance(unit, str) else "", str(key)

    nested = _first_numeric_leaf(payload)
    if nested is not None:
        key, raw_value = nested
        return float(raw_value), "", key

    return None


def _extract_text_readings(payload: object) -> list[tuple[str, str]] | None:
    """Extract string-valued keys from a dict payload (e.g. power: 'OFF').

    Data-driven metadata detection: if the dict has a 'value' key (common
    IoT payload envelope), sibling keys like 'unit' are describing the
    measurement, not the device — skip them.  Otherwise treat all string
    values as potential sensor readings.
    """
    if not isinstance(payload, dict):
        return None
    # If payload uses a 'value' envelope, sibling keys are metadata
    has_value_key = "value" in payload
    results = []
    for key, val in payload.items():
        if has_value_key and key.lower() != "value":
            continue
        if isinstance(val, str) and val.strip():
            results.append((str(key), val.strip()))
    return results if results else None


def _first_numeric_leaf(payload: dict) -> tuple[str, float] | None:
    for key, value in payload.items():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return str(key), float(value)
        if isinstance(value, dict):
            nested = _first_numeric_leaf(value)
            if nested is not None:
                return nested
    return None


def _infer_sensor_type(topic: str, payload_hint: str | None, fallback_segments: set[str]) -> str | None:
    topic_segments = [segment.strip().lower() for segment in topic.split("/") if segment.strip()]
    if topic_segments:
        candidate = topic_segments[-1]
        if candidate not in fallback_segments:
            return candidate

    if payload_hint:
        normalized = str(payload_hint).strip().lower()
        if normalized and normalized != "value":
            return normalized

    return None
