from __future__ import annotations

import json
import logging
import time
from threading import Lock
from typing import Optional

from .types import DeviceBearing, DevicePosition

logger = logging.getLogger(__name__)


class MqttPublisher:
    POS_TOPIC = "/wifi/localization/{device_id}/pos"
    BEARING_TOPIC = "/wifi/localization/{device_id}/bearing"

    def __init__(self):
        self._client: Optional[object] = None
        self._broker_host: str = "localhost"
        self._broker_port: int = 1883
        self._connected: bool = False
        self._lock: Lock = Lock()
        self._client_id: str = f"smart-room-localization-{int(time.time())}"

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    def connect(self, host: str = "localhost", port: int = 1883, username: Optional[str] = None, password: Optional[str] = None) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            logger.error("paho-mqtt is not installed. Install with: pip install paho-mqtt")
            return

        self._broker_host = host
        self._broker_port = port

        client = mqtt.Client(client_id=self._client_id, protocol=mqtt.MQTTv311)
        if username and password:
            client.username_pw_set(username, password)

        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect

        try:
            client.connect(host, port, keepalive=60)
            client.loop_start()
            self._client = client
            logger.info(f"MQTT publisher connecting to {host}:{port}")
        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker at {host}:{port}: {e}")

    def disconnect(self) -> None:
        with self._lock:
            if self._client is not None:
                try:
                    self._client.loop_stop()
                    self._client.disconnect()
                except Exception as e:
                    logger.warning(f"Error during MQTT disconnect: {e}")
                finally:
                    self._client = None
                    self._connected = False

    def publish_position(self, device: DevicePosition) -> bool:
        topic = self.POS_TOPIC.format(device_id=device.device_id)
        payload = json.dumps({
            "device_id": device.device_id,
            "x": device.world_position.x,
            "y": device.world_position.y,
            "z": device.world_position.z,
            "confidence": device.confidence,
            "timestamp_ms": device.timestamp_ms,
        })
        return self._publish(topic, payload)

    def publish_bearing(self, bearing: DeviceBearing) -> bool:
        topic = self.BEARING_TOPIC.format(device_id=bearing.device_id)
        payload = json.dumps({
            "device_id": bearing.device_id,
            "azimuth_deg": bearing.azimuth_deg,
            "elevation_deg": bearing.elevation_deg,
            "distance_m": bearing.distance_m,
            "confidence": bearing.confidence,
            "timestamp_ms": bearing.timestamp_ms,
        })
        return self._publish(topic, payload)

    def publish_all(
        self,
        positions: list[DevicePosition],
        bearings: list[DeviceBearing],
    ) -> tuple[int, int]:
        pos_ok = sum(1 for p in positions if self.publish_position(p))
        bearing_ok = sum(1 for b in bearings if self.publish_bearing(b))
        return pos_ok, bearing_ok

    def _publish(self, topic: str, payload: str, qos: int = 1) -> bool:
        with self._lock:
            if self._client is None or not self._connected:
                return False

        try:
            result = self._client.publish(topic, payload, qos=qos)
            if result.rc == 0:
                return True
            logger.warning(f"MQTT publish to {topic} returned rc={result.rc}")
            return False
        except Exception as e:
            logger.error(f"MQTT publish to {topic} failed: {e}")
            return False

    def _on_connect(self, client, userdata, flags, rc):
        with self._lock:
            if rc == 0:
                self._connected = True
                logger.info(f"MQTT publisher connected to {self._broker_host}:{self._broker_port}")
            else:
                self._connected = False
                logger.warning(f"MQTT connection failed with rc={rc}")

    def _on_disconnect(self, client, userdata, rc):
        with self._lock:
            self._connected = False
            if rc != 0:
                logger.warning(f"MQTT unexpected disconnect, rc={rc}")
