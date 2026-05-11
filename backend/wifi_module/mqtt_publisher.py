from __future__ import annotations

import asyncio
import json
import logging
import threading
import time

from .config import WifiModuleConfig
from .models import RssiSample

logger = logging.getLogger(__name__)


class MqttPublisher:
    def __init__(self, config: WifiModuleConfig):
        self._config = config
        self._client = None
        self._lock = threading.Lock()
        self._connected = False
        self._connect_event = threading.Event()

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    async def start(self) -> bool:
        if not self._config.mqtt_enabled:
            logger.info("MQTT disabled (no broker configured)")
            return False

        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            logger.warning(
                "paho-mqtt not installed. Install with: pip install paho-mqtt"
            )
            return False

        self._client = mqtt.Client(
            client_id=self._config.mqtt_client_id,
            protocol=mqtt.MQTTv311,
        )

        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect

        if self._config.mqtt_username:
            self._client.username_pw_set(
                self._config.mqtt_username, self._config.mqtt_password
            )

        self._connect_event.clear()

        try:
            self._client.connect_async(
                self._config.mqtt_broker_host,
                self._config.mqtt_broker_port,
                keepalive=30,
            )
            self._client.loop_start()
        except Exception as exc:
            logger.error("MQTT connect failed: %s", exc)
            return False

        connected = self._connect_event.wait(timeout=10.0)
        if connected:
            logger.info(
                "MQTT connected to %s:%d",
                self._config.mqtt_broker_host,
                self._config.mqtt_broker_port,
            )
        else:
            logger.warning("MQTT connection timed out")
        return connected

    async def stop(self) -> None:
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
            with self._lock:
                self._connected = False
            logger.info("MQTT publisher stopped")

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            with self._lock:
                self._connected = True
            self._connect_event.set()
            logger.info("MQTT broker connect acknowledged")
        else:
            logger.warning("MQTT connection refused (rc=%d)", rc)

    def _on_disconnect(self, client, userdata, rc):
        with self._lock:
            self._connected = False
        if rc != 0:
            logger.warning("MQTT unexpected disconnect (rc=%d)", rc)

    async def publish_rssi(self, sample: RssiSample) -> bool:
        if not self._client or not self.connected:
            return False

        device_id = sample.mac.replace(":", "_").replace("-", "_")
        topic = (
            f"{self._config.mqtt_topic_prefix}/sensor/{device_id}/rssi"
        )

        payload = json.dumps(
            {
                "rssi": sample.rssi,
                "mac": sample.mac,
                "timestamp": sample.timestamp_utc,
            }
        )

        try:
            result = self._client.publish(
                topic,
                payload,
                qos=self._config.mqtt_qos,
                retain=self._config.mqtt_retain,
            )
            if result.rc == 0:
                logger.debug("Published RSSI %s=%.1f dBm to %s", sample.mac, sample.rssi, topic)
                return True
            else:
                logger.warning(
                    "MQTT publish failed (rc=%d) for %s", result.rc, sample.mac
                )
                return False
        except Exception as exc:
            logger.error("MQTT publish error for %s: %s", sample.mac, exc)
            return False

    async def publish_batch(self, samples: list[RssiSample]) -> int:
        published = 0
        for sample in samples:
            if await self.publish_rssi(sample):
                published += 1
        return published

    async def publish_device_join(self, mac: str) -> bool:
        if not self._client or not self.connected:
            return False

        device_id = mac.replace(":", "_").replace("-", "_")
        topic = f"{self._config.mqtt_topic_prefix}/sensor/{device_id}/status"

        payload = json.dumps(
            {
                "event": "join",
                "mac": mac,
                "timestamp": time.time(),
            }
        )

        try:
            self._client.publish(
                topic,
                payload,
                qos=self._config.mqtt_qos,
                retain=False,
            )
            return True
        except Exception:
            return False

    async def publish_device_leave(self, mac: str) -> bool:
        if not self._client or not self.connected:
            return False

        device_id = mac.replace(":", "_").replace("-", "_")
        topic = f"{self._config.mqtt_topic_prefix}/sensor/{device_id}/status"

        payload = json.dumps(
            {
                "event": "leave",
                "mac": mac,
                "timestamp": time.time(),
            }
        )

        try:
            self._client.publish(
                topic,
                payload,
                qos=self._config.mqtt_qos,
                retain=False,
            )
            return True
        except Exception:
            return False
