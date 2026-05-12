from __future__ import annotations

import json
from datetime import UTC
from typing import Any

try:
    import paho.mqtt.client as mqtt
except ModuleNotFoundError:  # pragma: no cover
    mqtt = None

from wifi_positioning.tracker.models import DeviceState


class MqttPositionPublisher:
    def __init__(
        self,
        host: str,
        port: int = 1883,
        client_id: str = "smart-room-positioning",
        client: Any | None = None,
    ):
        if client is not None:
            self._client = client
        else:
            if mqtt is None:
                raise RuntimeError("paho-mqtt is required when no custom client is provided")
            self._client = mqtt.Client(client_id=client_id)
        self._client.connect_async(host=host, port=port)
        self._client.loop_start()

    @staticmethod
    def _normalize_mac(device_mac: str) -> str:
        return device_mac.replace(":", "").lower()

    def publish_discovery(self, device_mac: str, device_name: str) -> None:
        normalized = self._normalize_mac(device_mac)
        state_topic = f"smart_room/device_tracker/{device_mac}/state"
        config_topic = f"homeassistant/device_tracker/smart_room_{normalized}/config"
        payload = {
            "name": device_name,
            "unique_id": f"smart_room_{normalized}",
            "state_topic": state_topic,
            "json_attributes_topic": state_topic,
            "payload_home": "home",
            "payload_not_home": "not_home",
            "value_template": "{{ value_json.location_name }}",
            "device": {
                "identifiers": [f"smart_room_{normalized}"],
                "name": device_name,
                "manufacturer": "Smart Room",
            },
        }
        self._client.publish(config_topic, json.dumps(payload), qos=1, retain=True)

    def publish_position(self, device_state: DeviceState) -> None:
        topic = f"smart_room/device_tracker/{device_state.mac}/state"
        payload = {
            "x": device_state.x,
            "y": device_state.y,
            "direction": device_state.direction,
            "distance": device_state.distance,
            "confidence": device_state.confidence,
            "timestamp": device_state.timestamp.astimezone(UTC).isoformat(),
            "location_name": device_state.location_name,
            "zone": device_state.zone,
        }
        self._client.publish(topic, json.dumps(payload), qos=0, retain=False)

    def publish_zone_change(self, device_mac: str, old_zone: str | None, new_zone: str | None) -> None:
        topic = f"smart_room/device_tracker/{device_mac}/zone_change"
        payload = {
            "old_zone": old_zone,
            "new_zone": new_zone,
        }
        self._client.publish(topic, json.dumps(payload), qos=0, retain=False)
