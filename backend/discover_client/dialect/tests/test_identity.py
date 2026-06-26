"""Tests for device identity extraction per dialect."""

import pytest

from discover_client.dialect.identity import extract_device_id


def test_extract_device_id_tasmota() -> None:
    """Tasmota: segments[1] is the device id."""
    assert extract_device_id("tasmota", "cmnd/light-1/Power") == "light-1"


def test_extract_device_id_zigbee2mqtt() -> None:
    """Zigbee2MQTT: segments[1] is the device id."""
    assert extract_device_id("zigbee2mqtt", "zigbee2mqtt/bulb/set") == "bulb"


def test_extract_device_id_subtopic() -> None:
    """SubTopic: everything except the last 2 segments is the device id."""
    assert extract_device_id("subtopic", "mock/light-1/set/power") == "mock/light-1"


def test_extract_device_id_flatdict() -> None:
    """FlatDict: everything except the last segment is the device id."""
    assert extract_device_id("flatdict", "mock/light-1/set") == "mock/light-1"


def test_extract_device_id_barevalue() -> None:
    """BareValue: the terminal property is removed from the entity path."""
    assert extract_device_id("barevalue", "some/topic") == "some"
    assert (
        extract_device_id("barevalue", "UCL/OPSEBO/206/Room/TPS/value")
        == "UCL/OPSEBO/206/Room/TPS"
    )


def test_extract_device_id_unknown_dialect() -> None:
    """Unknown dialect: pass-through the entire topic."""
    assert extract_device_id("unknown_dialect", "foo/bar/baz") == "foo/bar/baz"
