from __future__ import annotations

import json
import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from wifi_positioning.ha_integration.device_tracker_service import HADeviceTrackerService
from wifi_positioning.ha_integration.mqtt_publisher import MqttPositionPublisher
from wifi_positioning.tracker.models import DeviceState


class _FakeMqttClient:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, int, bool]] = []

    def connect_async(self, *_args, **_kwargs) -> None:
        return None

    def loop_start(self) -> None:
        return None

    def publish(self, topic: str, payload: str, qos: int, retain: bool = False):
        self.published.append((topic, payload, qos, retain))
        return None


def test_mqtt_discovery_and_position_payload_format() -> None:
    fake = _FakeMqttClient()
    publisher = MqttPositionPublisher(host="localhost", client=fake)
    device = DeviceState(
        mac="aa:bb:cc:dd:ee:ff",
        name="Phone",
        x=1.2,
        y=2.4,
        direction=90.0,
        distance=3.1,
        confidence=0.95,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        zone="living_room",
        present=True,
    )

    publisher.publish_discovery(device_mac=device.mac, device_name=device.name)
    publisher.publish_position(device)
    publisher.publish_zone_change(device_mac=device.mac, old_zone="hallway", new_zone="living_room")

    discovery_topic, discovery_payload, discovery_qos, discovery_retain = fake.published[0]
    assert discovery_topic == "homeassistant/device_tracker/smart_room_aabbccddeeff/config"
    assert discovery_qos == 1
    assert discovery_retain is True

    cfg = json.loads(discovery_payload)
    assert cfg["state_topic"] == "smart_room/device_tracker/aa:bb:cc:dd:ee:ff/state"

    state_topic, state_payload, state_qos, state_retain = fake.published[1]
    assert state_topic == "smart_room/device_tracker/aa:bb:cc:dd:ee:ff/state"
    assert state_qos == 0
    assert state_retain is False

    state = json.loads(state_payload)
    assert state["x"] == 1.2
    assert state["y"] == 2.4
    assert state["direction"] == 90.0
    assert state["distance"] == 3.1
    assert state["confidence"] == 0.95
    assert "timestamp" in state


def test_ha_device_tracker_service_calls_device_tracker_see() -> None:
    rest_client = AsyncMock()
    rest_client.call_service = AsyncMock(return_value=True)
    service = HADeviceTrackerService(rest_client=rest_client)

    ok = asyncio.run(
        service.see_device(
            device_mac="aa:bb:cc:dd:ee:ff",
            location_name="living_room",
            gps=(10.0, 20.0),
            attributes={"x": 1.2, "y": 2.4},
        )
    )

    assert ok is True
    rest_client.call_service.assert_awaited_once()
    args = rest_client.call_service.await_args
    assert args.kwargs["domain"] == "device_tracker"
    assert args.kwargs["service"] == "see"
    assert args.kwargs["service_data"]["mac"] == "aa:bb:cc:dd:ee:ff"
