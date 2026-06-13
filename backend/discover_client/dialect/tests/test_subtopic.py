"""Tests for SubTopicRecognizer."""

from discover_client.dialect.recognizers.subtopic import SubTopicRecognizer


def test_subtopic_matches_bare_value_on_deep_topic() -> None:
    rec = SubTopicRecognizer()
    assert rec.match("mock/light-1/set/power", "ON") > 0


def test_subtopic_rejects_dict_payload() -> None:
    rec = SubTopicRecognizer()
    assert rec.match("mock/light-1/set/power", {"power": "ON"}) == 0


def test_subtopic_rejects_short_topic() -> None:
    rec = SubTopicRecognizer()
    assert rec.match("mock/set", "ON") == 0


def test_subtopic_extracts_sensor_from_last_segment() -> None:
    rec = SubTopicRecognizer()
    result = rec.extract("mock/light-1/set/power", "ON")
    assert result.operations[0].sensor_key == "power"
    assert result.operations[0].accepted_values == ["ON"]


def test_subtopic_extracts_brightness_numeric() -> None:
    rec = SubTopicRecognizer()
    result = rec.extract("mock/light-1/set/brightness", "50")
    assert result.sensor_readings[0].sensor_type == "brightness"
    assert result.sensor_readings[0].value == 50.0
