"""MQTT data source - connects to a broker and emits messages as events."""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import time

import paho.mqtt.client as mqtt

from discover_client.source import Source, SourceConfig, SourceEvent

logger = logging.getLogger(__name__)


def _topic_matches(topic: str, pattern: str) -> bool:
    """Check if an MQTT topic matches a wildcard pattern."""
    fnmatch_pattern = pattern.replace("#", "**").replace("+", "*")
    return fnmatch.fnmatch(topic, fnmatch_pattern)


def _matches_any(topic: str, patterns: list[str]) -> bool:
    """Return True if topic matches at least one pattern in the list."""
    return any(_topic_matches(topic, pattern) for pattern in patterns)


class MqttSource(Source):
    """Publishes MQTT messages as SourceEvents via paho-mqtt."""

    def __init__(self, config: SourceConfig, emit) -> None:
        super().__init__(config, emit)
        settings = config.settings

        self._host: str = settings["host"]
        self._port: int = int(settings["port"])
        self._username: str | None = settings.get("username")
        self._password: str | None = settings.get("password")
        self._whitelist: list[str] = settings.get("topic_whitelist", [])
        self._blacklist: list[str] = settings.get("topic_blacklist", [])

        self._client = mqtt.Client(
            client_id=f"discover-{config.source_id}",
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )
        if self._username:
            self._client.username_pw_set(self._username, self._password)

        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False
        self._reconnect_task: asyncio.Task | None = None
        self._reconnect_delay = 1.0

    def emit(self, event_type: str, payload: dict | None = None) -> None:
        """Thread-safe: schedule event delivery onto the asyncio event loop."""
        event = SourceEvent(
            source_id=self.source_id,
            source_type=self.source_type,
            timestamp=time.time(),
            event_type=event_type,
            payload=payload or {},
        )
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._emit, event)
            return

        self._emit(event)

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._running = True

        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect

        try:
            await self._loop.run_in_executor(
                None,
                lambda: self._client.connect(self._host, self._port, keepalive=60),
            )
        except Exception as exc:
            self.emit("error", {"msg": f"Failed to connect: {exc}"})
            return

        self._client.loop_start()

    async def stop(self) -> None:
        self._running = False
        if self._reconnect_task:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        self._client.loop_stop()
        self._client.disconnect()
        self.emit("status", {"msg": "stopped"})

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        _ = userdata, flags, properties
        if reason_code != 0:
            self.emit("error", {"msg": f"Connection refused (rc={reason_code})"})
            return

        if self._whitelist:
            for topic in self._whitelist:
                client.subscribe(topic)
        else:
            client.subscribe("#")

        self._reconnect_delay = 1.0
        self.emit("status", {"msg": "connected", "host": self._host})

    def _on_message(self, client, userdata, msg):
        _ = client, userdata
        topic = msg.topic
        if self._blacklist and _matches_any(topic, self._blacklist):
            return
        if self._whitelist and not _matches_any(topic, self._whitelist):
            return

        self.emit(
            "data",
            {
                "topic": topic,
                "value": msg.payload.decode("utf-8", errors="replace"),
                "qos": msg.qos,
            },
        )

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        _ = client, userdata, flags, properties
        if reason_code != 0:
            self.emit("error", {"msg": f"Disconnected (rc={reason_code})"})
        if self._running and self._loop is not None:
            self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        if self._reconnect_task and not self._reconnect_task.done():
            return

        self.emit("status", {"msg": f"Reconnecting in {self._reconnect_delay:.0f}s"})
        self._reconnect_task = asyncio.ensure_future(self._reconnect(), loop=self._loop)

    async def _reconnect(self) -> None:
        assert self._loop is not None
        await asyncio.sleep(self._reconnect_delay)
        try:
            await self._loop.run_in_executor(None, self._client.reconnect)
        except Exception as exc:
            self.emit("error", {"msg": f"Reconnect failed: {exc}"})
            self._reconnect_delay = min(self._reconnect_delay * 2, 60.0)
            if self._running:
                self._schedule_reconnect()
        else:
            self._reconnect_delay = 1.0
