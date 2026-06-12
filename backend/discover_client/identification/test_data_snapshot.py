from discover_client.identification.data_snapshot import DataSnapshot


def test_data_snapshot_extracts_latest_sensor_readings() -> None:
    ds = DataSnapshot()

    readings = ds.ingest(
        "device-1",
        {
            "topic": "govee/H5179/a1b2c3d4e5f6/temperature",
            "value": {"unit": "C", "value": 23.5},
            "timestamp": 100.0,
        },
    )
    assert readings is not None
    assert len(readings) == 1
    assert readings[0].sensor_type == "temperature"
    assert readings[0].value == 23.5
    assert readings[0].unit == "C"

    ds.ingest(
        "device-1",
        {
            "topic": "govee/H5179/a1b2c3d4e5f6/humidity",
            "value": {"unit": "%", "value": 60.3},
            "timestamp": 101.0,
        },
    )

    latest = ds.get_latest("device-1")
    assert latest["temperature"].value == 23.5
    assert latest["humidity"].value == 60.3

    all_data = ds.get_all()
    assert "device-1" in all_data
    assert len(all_data["device-1"]) == 2


def test_data_snapshot_supports_alternative_payload_shapes() -> None:
    ds = DataSnapshot()

    readings = ds.ingest(
        "device-2",
        {
            "topic": "zigbee/0x00158d/temperature",
            "value": 25.0,
            "timestamp": 200.0,
        },
    )
    assert readings is not None
    assert readings[0].value == 25.0

    nested = ds.ingest(
        "device-3",
        {
            "topic": "acme/sensor/state",
            "value": {"data": {"temp_c": 21.2}},
            "timestamp": 300.0,
        },
    )
    assert nested is not None
    assert nested[0].sensor_type == "temp_c"
    assert nested[0].value == 21.2


def test_data_snapshot_extracts_text_values() -> None:
    ds = DataSnapshot()

    readings = ds.ingest(
        "light-1",
        {
            "topic": "mock/light-1/state",
            "value": {"power": "OFF", "brightness": 100},
            "timestamp": 100.0,
        },
    )
    assert readings is not None
    assert len(readings) == 2

    # Numeric extraction: brightness
    brightness = [r for r in readings if r.sensor_type == "brightness"][0]
    assert brightness.value == 100.0
    assert brightness.text_value is None

    # Text extraction: power
    power = [r for r in readings if r.sensor_type == "power"][0]
    assert power.text_value == "OFF"
    assert power.value == 0.0

    # Verify both show up in get_latest
    latest = ds.get_latest("light-1")
    assert set(latest.keys()) == {"power", "brightness"}


def test_data_snapshot_prunes_expired_readings() -> None:
    ds = DataSnapshot(retention_s=10)

    ds.ingest(
        "device-1",
        {
            "topic": "govee/H5179/a1b2c3d4e5f6/temperature",
            "value": {"unit": "C", "value": 20.0},
            "timestamp": 100.0,
        },
    )
    ds.ingest(
        "device-1",
        {
            "topic": "govee/H5179/a1b2c3d4e5f6/temperature",
            "value": {"unit": "C", "value": 21.0},
            "timestamp": 111.0,
        },
    )

    latest = ds.get_latest("device-1")
    assert latest["temperature"].value == 21.0
    assert len(ds.get_all()["device-1"]["temperature"]) == 1
