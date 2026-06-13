"""Tests for dialect aggregator."""

from discover_client.dialect.aggregator import aggregate


def test_flatdict_aggregated() -> None:
    result = aggregate("mock/light-1/state", {"power": "OFF", "brightness": 100})
    assert result is not None
    assert result.primary_dialect == "flatdict"
    assert result.device_id == "mock/light-1"
    assert len(result.operations) == 2
    assert len(result.sensor_readings) == 2
    op_keys = {(op.topic, op.action, op.sensor_key) for op in result.operations}
    assert ("mock/light-1/state", "set", "power") in op_keys
    assert ("mock/light-1/state", "set", "brightness") in op_keys


def test_tasmota_aggregated() -> None:
    result = aggregate("cmnd/light-1/Power", "ON")
    assert result is not None
    assert result.primary_dialect == "tasmota"
    assert result.device_id == "light-1"
    assert len(result.operations) == 1
    assert result.operations[0].sensor_key == "power"
    assert len(result.sensor_readings) == 1


def test_subtopic_aggregated() -> None:
    result = aggregate("mock/light-1/set/power", "ON")
    assert result is not None
    assert result.primary_dialect == "subtopic"
    assert result.operations[0].sensor_key == "power"
    assert result.operations[0].accepted_values == ["ON"]


def test_zigbee2mqtt_aggregated_normalized() -> None:
    """Zigbee2MQTT 'state' → canonical 'power'."""
    result = aggregate("zigbee2mqtt/bulb/set", {"state": "ON"})
    assert result is not None
    assert result.primary_dialect == "zigbee2mqtt"
    sensor_keys = {s.sensor_type for s in result.sensor_readings}
    assert "power" in sensor_keys  # "state" → canonical "power"


def test_barevalue_aggregated() -> None:
    result = aggregate("some/unknown/topic", 25.0)
    assert result is not None
    assert result.primary_dialect == "barevalue"
    assert result.sensor_readings[0].sensor_type == "value"


def test_barevalue_never_wins_over_specialized() -> None:
    """BareValue has SPECIFICITY=10 and capped score 0.50. Specialized recognizers should always win."""
    result = aggregate("mock/light-1/set", {"power": "ON"})
    assert result is not None
    assert result.primary_dialect != "barevalue"  # FlatDict should win
    assert result.primary_dialect == "flatdict"
