"""Tests for TasmotaRecognizer."""

from discover_client.dialect.recognizers.tasmota import TasmotaRecognizer


def test_tasmota_matches_cmnd_prefix() -> None:
    rec = TasmotaRecognizer()
    assert rec.match("cmnd/light-1/Power", "ON") > 0


def test_tasmota_matches_stat_prefix() -> None:
    rec = TasmotaRecognizer()
    assert rec.match("stat/light-1/POWER", {"POWER": "ON"}) > 0


def test_tasmota_rejects_non_tasmota() -> None:
    rec = TasmotaRecognizer()
    assert rec.match("mock/light-1/set", "ON") == 0


def test_tasmota_extracts_power_command() -> None:
    rec = TasmotaRecognizer()
    result = rec.extract("cmnd/light-1/Power", "ON")
    assert result.operations[0].sensor_key == "power"
    assert result.operations[0].accepted_values == ["ON"]


def test_tasmota_dual_output_command_echo() -> None:
    """cmnd/Power <- "ON" should produce BOTH operation AND sensor reading."""
    rec = TasmotaRecognizer()
    result = rec.extract("cmnd/light-1/Power", "ON")
    assert len(result.operations) == 1
    assert len(result.sensor_readings) == 1
    assert result.sensor_readings[0].sensor_type == "power"
    assert result.sensor_readings[0].value == "ON"


def test_tasmota_dict_payload() -> None:
    rec = TasmotaRecognizer()
    result = rec.extract("stat/light-1/STATE", {"POWER": "ON", "Dimmer": 50})
    sensor_keys = {s.sensor_type for s in result.sensor_readings}
    assert "power" in sensor_keys
    assert "dimmer" in sensor_keys
    assert result.operations == []


def test_tasmota_skips_result_topic() -> None:
    rec = TasmotaRecognizer()
    result = rec.extract("stat/light-1/RESULT", {"POWER": "ON"})
    # RESULT topics are command echoes, not real state — produce only sensor
    assert len(result.operations) == 0
