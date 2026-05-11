"""Adapted tests for DEA-45 core modules integrated with master API."""

from ha_client.core.event_bus import EventBus, EventType
from ha_client.models.entity import EntityState, EntityDomain
from ha_client.models.device import create_device, Light, Switch, Sensor, Device


def test_event_bus():
    bus = EventBus()
    results = []

    def on_state_changed(**data):
        results.append(data)

    bus.subscribe(EventType.STATE_CHANGED, on_state_changed)
    bus.emit(EventType.STATE_CHANGED, entity_id="light.test", state="on")
    assert len(results) == 1, f"Expected 1, got {len(results)}"
    assert results[0]["entity_id"] == "light.test"

    bus.unsubscribe(EventType.STATE_CHANGED, on_state_changed)
    bus.emit(EventType.STATE_CHANGED, entity_id="light.test2", state="off")
    assert len(results) == 1

    print("EventBus: OK")


def test_entity_domain_classify():
    assert EntityDomain.classify("light.living_room") == EntityDomain.LIGHT
    assert EntityDomain.classify("switch.tv") == EntityDomain.SWITCH
    assert EntityDomain.classify("sensor.temp") == EntityDomain.SENSOR
    assert EntityDomain.classify("binary_sensor.motion") == EntityDomain.BINARY_SENSOR
    assert EntityDomain.classify("climate.thermostat") == EntityDomain.CLIMATE
    assert EntityDomain.classify("unknown.thing") == EntityDomain.UNKNOWN
    print("EntityDomain.classify: OK")


def test_entity_state():
    data = {
        "entity_id": "light.living_room",
        "state": "on",
        "attributes": {"friendly_name": "Living Room Light", "brightness": 200},
        "last_changed": "2024-01-01T12:00:00+00:00",
        "last_updated": "2024-01-01T12:00:01+00:00",
    }
    es = EntityState.from_ha_json(data)
    assert es.entity_id == "light.living_room"
    assert es.state == "on"
    assert es.friendly_name == "Living Room Light"
    assert es.domain == EntityDomain.LIGHT
    print("EntityState: OK")


def test_device_factory():
    data = {
        "entity_id": "light.living_room",
        "state": "on",
        "attributes": {
            "friendly_name": "Living Room Light",
            "brightness": 200,
            "rgb_color": [255, 136, 0],
        },
    }
    es = EntityState.from_ha_json(data)
    light = create_device(es)
    assert isinstance(light, Light)
    assert light.is_on
    assert light.brightness == 200
    assert light.rgb_color == (255, 136, 0)
    print("Device factory (Light): OK")

    switch_es = EntityState.from_ha_json({
        "entity_id": "switch.tv",
        "state": "off",
        "attributes": {"friendly_name": "TV"},
    })
    switch = create_device(switch_es)
    assert isinstance(switch, Switch)
    assert not switch.is_on
    print("Device factory (Switch): OK")

    sensor_es = EntityState.from_ha_json({
        "entity_id": "sensor.temp",
        "state": "22.5",
        "attributes": {
            "friendly_name": "Temp",
            "unit_of_measurement": "\u00b0C",
        },
    })
    sensor = create_device(sensor_es)
    assert isinstance(sensor, Sensor)
    assert sensor.state == "22.5"
    assert sensor.unit_of_measurement == "\u00b0C"
    print("Device factory (Sensor): OK")


def test_fallback_device():
    es = EntityState.from_ha_json({
        "entity_id": "cover.garage",
        "state": "closed",
        "attributes": {"friendly_name": "Garage Door"},
    })
    dev = create_device(es)
    assert isinstance(dev, Device)
    assert not isinstance(dev, (Light, Switch, Sensor))
    print("Device factory (fallback): OK")


def test_device_update_state():
    es = EntityState.from_ha_json({
        "entity_id": "light.test",
        "state": "on",
        "attributes": {"friendly_name": "Test Light", "brightness": 128},
    })
    dev = create_device(es)
    assert dev.state == "on"

    new_es = EntityState.from_ha_json({
        "entity_id": "light.test",
        "state": "off",
        "attributes": {"friendly_name": "Test Light", "brightness": 0},
    })
    dev.update_state(new_es)
    assert dev.state == "off"
    assert dev.attributes.get("brightness") == 0
    print("Device.update_state: OK")


def test_config_import():
    from ha_client.config.settings import HAConfig, load_config, create_default_config
    config = HAConfig(url="http://test:8123", token="abc123")
    assert config.url == "http://test:8123"
    assert config.token == "abc123"
    print("HAConfig: OK")
    return config


def test_exceptions():
    from ha_client.api.exceptions import (
        HAError,
        HAConnectionError,
        HAAuthError,
        HAResponseError,
        HAServiceError,
    )
    assert issubclass(HAConnectionError, HAError)
    assert issubclass(HAAuthError, HAError)
    assert issubclass(HAResponseError, HAError)
    assert issubclass(HAServiceError, HAError)
    print("Exceptions: OK")


if __name__ == "__main__":
    test_event_bus()
    test_entity_domain_classify()
    test_entity_state()
    test_device_factory()
    test_fallback_device()
    test_device_update_state()
    test_config_import()
    test_exceptions()
    print("\nAll tests passed!")
