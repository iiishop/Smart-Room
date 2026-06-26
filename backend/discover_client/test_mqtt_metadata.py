from discover_client.identification.device import Device
from discover_client.mqtt_metadata import MqttMetadataIndex


def test_home_assistant_discovery_groups_state_topics_by_device_identifier() -> None:
    index = MqttMetadataIndex()
    payload = {
        "name": "Desk temperature",
        "state_topic": "lab/desk/temperature",
        "unique_id": "desk-temperature",
        "device_class": "temperature",
        "device": {
            "identifiers": ["desk-sensor-001"],
            "name": "Desk sensor",
            "manufacturer": "Acme",
            "model": "Env-1",
        },
    }

    assert index.ingest(
        "mqtt-main",
        "homeassistant/sensor/desk_temperature/config",
        payload,
    )
    metadata = index.lookup("mqtt-main", "lab/desk/temperature")

    assert metadata is not None
    assert metadata.identity == "desk-sensor-001"
    assert metadata.name == "Desk sensor"
    assert metadata.manufacturer == "Acme"
    assert metadata.model == "Env-1"

    device = Device(device_id="device-1")
    index.enrich_device(device, metadata)
    assert device.explicit_names == {"Desk sensor"}
    assert "temperature" in device.metadata_capabilities


def test_zigbee2mqtt_bridge_metadata_exposes_identity_and_writable_operation() -> None:
    index = MqttMetadataIndex()
    payload = [
        {
            "type": "Router",
            "ieee_address": "0x00158d0001abcdef",
            "friendly_name": "office_lamp",
            "definition": {
                "vendor": "IKEA",
                "model": "LED2003G10",
                "description": "Dimmable light",
                "exposes": [
                    {
                        "type": "light",
                        "features": [
                            {
                                "property": "state",
                                "access": 7,
                                "values": ["ON", "OFF"],
                            }
                        ],
                    }
                ],
            },
        }
    ]

    assert index.ingest("mqtt-main", "zigbee2mqtt/bridge/devices", payload)
    metadata = index.lookup("mqtt-main", "zigbee2mqtt/office_lamp/state")

    assert metadata is not None
    assert metadata.identity == "0x00158d0001abcdef"
    assert metadata.command_topics == {"zigbee2mqtt/office_lamp/set"}
    assert metadata.command_values["zigbee2mqtt/office_lamp/set"] == {"ON", "OFF"}


def test_bridge_metadata_does_not_create_a_data_device() -> None:
    index = MqttMetadataIndex()
    assert index.ingest("mqtt-main", "zigbee2mqtt/bridge/devices", [])
    assert index.lookup("mqtt-main", "zigbee2mqtt/bridge/devices") is None


def test_tasmota_discovery_builds_command_topic_from_full_topic_template() -> None:
    index = MqttMetadataIndex()
    payload = {
        "dn": "Gosund-UP111-Prusa3-Tasmota",
        "fn": ["Gosund UP111 Prusa3 Tasmota", None],
        "hn": "gosund-paul-the-prusa-3-6220",
        "mac": "C4DD573EB84C",
        "md": "Gosund UP111",
        "state": ["OFF", "ON", "TOGGLE", "HOLD"],
        "sw": "12.2.0",
        "t": "gosund/paul-the-prusa-3",
        "ft": "UCL/OPS/107/EM/%topic%",
        "tp": ["cmnd", "stat", "tele"],
        "rl": [1, 0, 0, 0],
    }

    assert index.ingest(
        "mqtt-main",
        "tasmota/discovery/C4DD573EB84C/config",
        payload,
    )
    metadata = index.lookup(
        "mqtt-main",
        "UCL/OPS/107/EM/gosund/paul-the-prusa-3/STATE",
    )

    assert metadata is not None
    assert metadata.topic_prefix == "UCL/OPS/107/EM/gosund/paul-the-prusa-3"
    assert metadata.command_topics == {
        "UCL/OPS/107/EM/gosund/paul-the-prusa-3/POWER"
    }
    assert metadata.command_values[
        "UCL/OPS/107/EM/gosund/paul-the-prusa-3/POWER"
    ] == {"OFF", "ON", "TOGGLE", "HOLD"}


def test_tasmota_default_full_topic_uses_cmnd_prefix() -> None:
    index = MqttMetadataIndex()
    index.ingest(
        "mqtt-main",
        "tasmota/discovery/ABCDEF123456/config",
        {
            "mac": "ABCDEF123456",
            "t": "desk-plug",
            "ft": "%prefix%/%topic%/",
            "tp": ["cmnd", "stat", "tele"],
            "rl": [1],
            "state": ["OFF", "ON", "TOGGLE"],
        },
    )

    metadata = index.lookup("mqtt-main", "tele/desk-plug/STATE")

    assert metadata is not None
    assert metadata.topic_prefix == "desk-plug"
    assert metadata.command_topics == {"cmnd/desk-plug/POWER"}


def test_tasmota_discovery_sensor_snapshot_is_consumed_without_device_update() -> None:
    index = MqttMetadataIndex()

    assert index.ingest(
        "mqtt-main",
        "tasmota/discovery/ABCDEF123456/sensors",
        {"sn": {"Time": "2026-06-25T23:00:30", "ENERGY": {"Power": 10}}},
    )
    assert index.drain_updates() == []
