"""Tests for BareValueRecognizer."""

from discover_client.dialect.recognizers.barevalue import BareValueRecognizer


def test_barevalue_always_matches() -> None:
    """Any topic + payload should produce >0 confidence."""
    rec = BareValueRecognizer()
    assert rec.match("any/topic", "ON") > 0
    assert rec.match("unknown", 42) > 0
    assert rec.match("foo/bar/baz", True) > 0


def test_barevalue_caps_confidence() -> None:
    """Even for recognizable topics, confidence caps at 0.50."""
    rec = BareValueRecognizer()
    assert rec.match("device/light/set/power", "ON") <= 0.50
    assert rec.match("sensor/temperature", 25.0) <= 0.50


def test_barevalue_extracts_enum_payload() -> None:
    """Enum string payload: sensor_key=None, sensor_readings[0].value='ON'."""
    rec = BareValueRecognizer()
    result = rec.extract("any/topic", "ON")
    assert result.operations == []
    assert len(result.sensor_readings) == 1
    assert result.sensor_readings[0].sensor_type == "value"
    assert result.sensor_readings[0].value == "ON"


def test_barevalue_extracts_numeric_payload() -> None:
    """Numeric payload: sensor_readings[0].value == 100.0."""
    rec = BareValueRecognizer()
    result = rec.extract("any/topic", 100)
    assert len(result.sensor_readings) == 1
    assert result.sensor_readings[0].sensor_type == "value"
    assert result.sensor_readings[0].value == 100.0
