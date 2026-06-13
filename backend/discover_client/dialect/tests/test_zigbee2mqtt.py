"""Tests for Zigbee2MQTTRecognizer."""

from discover_client.dialect.recognizers.zigbee2mqtt import Zigbee2MQTTRecognizer


def test_zigbee2mqtt_matches_prefix() -> None:
    rec = Zigbee2MQTTRecognizer()
    assert rec.match("zigbee2mqtt/bulb", {"state": "ON"}) > 0


def test_zigbee2mqtt_rejects_non_z2m() -> None:
    rec = Zigbee2MQTTRecognizer()
    assert rec.match("mock/light-1/set", {"state": "ON"}) == 0


def test_zigbee2mqtt_extracts_friendly_name() -> None:
    rec = Zigbee2MQTTRecognizer()
    result = rec.extract("zigbee2mqtt/bulb", {"state": "ON"})
    assert result.device_id_hint == "bulb"


def test_zigbee2mqtt_skips_bridge_topics() -> None:
    rec = Zigbee2MQTTRecognizer()
    assert rec.match("zigbee2mqtt/bridge/config", {"state": "ON"}) == 0


def test_zigbee2mqtt_nested_payload() -> None:
    rec = Zigbee2MQTTRecognizer()
    result = rec.extract("zigbee2mqtt/bulb", {"color": {"x": 0.5, "y": 0.5}})
    # flat key "color" with dict value — extracted as operation, skipped as sensor
    op_keys = {op.sensor_key for op in result.operations}
    assert "color" in op_keys
    sensor_keys = {s.sensor_type for s in result.sensor_readings}
    assert "color" not in sensor_keys
