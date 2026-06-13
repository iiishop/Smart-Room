"""Aggregate outputs from all matching dialect recognizers."""

from __future__ import annotations

from dataclasses import dataclass, field

from discover_client.dialect import RECOGNIZERS
from discover_client.dialect.identity import extract_device_id
from discover_client.dialect.normalizer import to_canonical
from discover_client.dialect.recognizer import RecognizedOperation, RecognizedSensor


@dataclass
class AggregatedOutput:
    primary_dialect: str
    device_id: str
    operations: list[RecognizedOperation] = field(default_factory=list)
    sensor_readings: list[RecognizedSensor] = field(default_factory=list)
    dialect_confidence: float = 0.0


def aggregate(topic: str, payload: object) -> AggregatedOutput | None:
    """Score all recognizers, pick the best, and merge non-conflicting contributions."""
    # 1. Score every recognizer
    scored: list[tuple[float, int, str]] = []
    for name, cls in RECOGNIZERS.items():
        rec = cls()
        score = rec.match(topic, payload)
        if score > 0:
            scored.append((score, cls.SPECIFICITY, name))
    if not scored:
        return None

    # 2. Sort by match_score DESC, SPECIFICITY DESC
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    primary_name = scored[0][2]

    # 3. Run primary recognizer first
    primary_cls = RECOGNIZERS[primary_name]
    primary_out = primary_cls().extract(topic, payload)

    # 4. Normalize sensor keys to canonical form
    _normalize_sensor_keys(primary_out.operations, primary_name)
    _normalize_sensor_keys(primary_out.sensor_readings, primary_name)

    # 5. Track what the primary already covers
    seen_ops = {(op.topic, op.action, op.sensor_key) for op in primary_out.operations}
    seen_sensors = {s.sensor_type.lower() for s in primary_out.sensor_readings}

    # 6. Try up to 2 secondary recognizers for non-conflicting additions
    for _, _, sec_name in scored[1:3]:
        sec_cls = RECOGNIZERS[sec_name]
        sec_out = sec_cls().extract(topic, payload)
        _normalize_sensor_keys(sec_out.operations, sec_name)
        _normalize_sensor_keys(sec_out.sensor_readings, sec_name)
        for op in sec_out.operations:
            if op.sensor_key is None:
                continue  # skip generic operations (BareValue fallback)
            key = (op.topic, op.action, op.sensor_key)
            if key not in seen_ops:
                primary_out.operations.append(op)
                seen_ops.add(key)
        for s in sec_out.sensor_readings:
            if s.sensor_type.lower() == "value":
                continue  # skip generic sensor (BareValue fallback)
            if s.sensor_type.lower() not in seen_sensors:
                primary_out.sensor_readings.append(s)
                seen_sensors.add(s.sensor_type.lower())

    device_id = extract_device_id(primary_name, topic)

    return AggregatedOutput(
        primary_dialect=primary_name,
        device_id=device_id,
        operations=primary_out.operations,
        sensor_readings=primary_out.sensor_readings,
        dialect_confidence=scored[0][0],
    )


def _normalize_sensor_keys(items: list, dialect: str) -> None:
    """Normalize sensor_key / sensor_type to canonical form in-place."""
    for item in items:
        raw_key = getattr(item, "sensor_key", None) or getattr(item, "sensor_type", None)
        if raw_key:
            canonical = to_canonical(dialect, raw_key)
            if hasattr(item, "sensor_key"):
                item.sensor_key = canonical
            if hasattr(item, "sensor_type"):
                item.sensor_type = canonical
