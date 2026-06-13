"""Tests for FlatDictRecognizer."""

from discover_client.dialect.recognizers.flatdict import FlatDictRecognizer
from discover_client.dialect.recognizer import RecognizedOperation, RecognizedSensor


def test_flatdict_matches_dict_payload() -> None:
    rec = FlatDictRecognizer()
    assert rec.match("mock/light-1/state", {"power": "OFF", "brightness": 100}) > 0
    assert rec.match("mock/light-1/set", {"power": "ON"}) > 0


def test_flatdict_rejects_non_dict() -> None:
    rec = FlatDictRecognizer()
    assert rec.match("mock/light-1/set", "ON") == 0


def test_flatdict_rejects_service_prefixes() -> None:
    rec = FlatDictRecognizer()
    assert rec.match("cmnd/light-1/Power", {"power": "ON"}) == 0
    assert rec.match("zigbee2mqtt/bulb/set", {"state": "ON"}) == 0
    assert rec.match("stat/light-1/POWER", {"POWER": "ON"}) == 0


def test_flatdict_extracts_operations_and_sensors() -> None:
    rec = FlatDictRecognizer()
    result = rec.extract("mock/light-1/state", {"power": "OFF", "brightness": 100})

    # Two operations: power and brightness
    ops = {(op.sensor_key, op.action) for op in result.operations}
    assert ops == {("power", "set"), ("brightness", "set")}

    # Two sensors: power (text) and brightness (numeric)
    sensors = {(s.sensor_type, type(s.value).__name__) for s in result.sensor_readings}
    assert sensors == {("power", "str"), ("brightness", "float")}


def test_flatdict_command_topic_creates_set_action() -> None:
    rec = FlatDictRecognizer()
    result = rec.extract("mock/light-1/set", {"power": "ON"})
    assert result.operations[0].action == "set"
    assert result.operations[0].accepted_values == ["ON"]


def test_flatdict_skips_metadata_keys() -> None:
    rec = FlatDictRecognizer()
    result = rec.extract("mock/light-1/state", {"_announce": True, "power": "OFF"})
    sensor_keys = {s.sensor_type for s in result.sensor_readings}
    assert "_announce" not in sensor_keys
    assert "power" in sensor_keys
