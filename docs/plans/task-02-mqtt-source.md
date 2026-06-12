# Task 2: MQTT Source + DiscoverClient Flesh-Out — Implementation Plan

> **For Hermes:** Use opencode run with this plan + discover-client-spec.md as context files.

**Goal:** Implement MQTT source with topic filtering and reconnection, plus flesh out DiscoverClient orchestrator from stub.

**Architecture:** MqttSource wraps paho-mqtt with a thread-safe emit bridge (`loop.call_soon_threadsafe`). DiscoverClient manages source lifecycle: instantiate from config, start concurrently, stop gracefully. Source registry maps source_type strings to Source subclasses.

**Tech Stack:** paho-mqtt >= 2.0.0, asyncio, stdlib fnmatch for MQTT wildcard matching.

---

## Pre-check: fix last task's leftover

The `DiscoverClient` stub was created in Task 1 because `__init__.py` imported it but the plan forgot `client.py`. Now we flesh it out for real. No cleanup needed — we'll overwrite it.

---

### Task 2.1: Create source registry (`sources/__init__.py`)

**Objective:** Map source_type strings to Source subclasses so DiscoverClient can instantiate sources by type.

**Files:**
- Create: `backend/discover_client/sources/__init__.py`

**Step 1: Create file**

```python
"""Source registry — maps source_type strings to Source subclasses."""

from typing import Type

from discover_client.source import Source

_registry: dict[str, Type[Source]] = {}


def register(source_type: str, cls: Type[Source]) -> None:
    """Register a Source subclass for a given source_type string."""
    if source_type in _registry:
        raise ValueError(f"Source type '{source_type}' is already registered")
    _registry[source_type] = cls


def get(source_type: str) -> Type[Source]:
    """Look up a Source subclass by source_type string."""
    if source_type not in _registry:
        raise KeyError(f"Unknown source type: '{source_type}'")
    return _registry[source_type]


def registered_types() -> list[str]:
    """Return all registered source_type keys."""
    return list(_registry.keys())
```

**Step 2: Verify file was created**

Run: `cd backend && uv run python -c "from discover_client.sources import register, get, registered_types; print('sources/__init__.py OK')"`
Expected: `sources/__init__.py OK`

---

### Task 2.2: Create MQTT Source

**Objective:** Implement MqttSource — connect to broker, subscribe with topic filter, emit data events, handle reconnection.

**Files:**
- Create: `backend/discover_client/sources/mqtt_source.py`

**Step 1: Create file**

```python
"""MQTT data source — connects to a broker and emits messages as events."""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import time

import paho.mqtt.client as mqtt

from discover_client.source import Source, SourceConfig, SourceEvent

logger = logging.getLogger(__name__)


def _topic_matches(topic: str, pattern: str) -> bool:
    """Check if an MQTT topic matches a wildcard pattern.

    Supports MQTT wildcards: '+' matches one level, '#' matches any suffix.
    Uses fnmatch for the heavy lifting after normalizing separators.
    """
    # fnmatch uses '*' not '#', and '/' not always. Normalize:
    # MQTT '#' → fnmatch '**' (multi-level)
    # MQTT '+' → fnmatch '*' (single level)
    # MQTT '/' stays as separator
    fnmatch_pattern = pattern.replace("#", "**").replace("+", "*")
    return fnmatch.fnmatch(topic, fnmatch_pattern)


def _matches_any(topic: str, patterns: list[str]) -> bool:
    """Return True if topic matches at least one pattern in the list."""
    return any(_topic_matches(topic, p) for p in patterns)


class MqttSource(Source):
    """Publishes MQTT messages as SourceEvents via paho-mqtt.

    Thread-safety: paho callbacks run in its own network thread.
    This class overrides emit() to use loop.call_soon_threadsafe,
    ensuring events are safely enqueued into the asyncio OutputQueue.
    """

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
        self._reconnect_delay = 1.0  # seconds, grows with backoff

    # ── thread-safe emit ──────────────────────────────────────────

    def emit(self, event_type: str, payload: dict | None = None) -> None:
        """Thread-safe: schedules the emit onto the asyncio event loop."""
        event = SourceEvent(
            source_id=self.source_id,
            source_type=self.source_type,
            timestamp=time.time(),
            event_type=event_type,
            payload=payload or {},
        )
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._emit, event)
        else:
            self._emit(event)

    # ── lifecycle ─────────────────────────────────────────────────

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
        except Exception as e:
            self.emit("error", {"msg": f"Failed to connect: {e}"})
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

    # ── paho callbacks (run in paho's network thread) ─────────────

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            self.emit("error", {"msg": f"Connection refused (rc={reason_code})"})
            return

        # Subscribe based on whitelist / fallback
        if self._whitelist:
            for topic in self._whitelist:
                client.subscribe(topic)
        else:
            client.subscribe("#")

        self._reconnect_delay = 1.0  # reset backoff
        self.emit("status", {"msg": "connected", "host": self._host})

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        # Apply blacklist
        if self._blacklist and _matches_any(topic, self._blacklist):
            return
        # Apply whitelist (already filtered by subscription, but double-check)
        if self._whitelist and not _matches_any(topic, self._whitelist):
            return

        self.emit("data", {
            "topic": topic,
            "value": msg.payload.decode("utf-8", errors="replace"),
            "qos": msg.qos,
        })

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            self.emit("error", {"msg": f"Disconnected (rc={reason_code})"})
        if self._running and self._loop is not None:
            self._schedule_reconnect()

    # ── reconnection ──────────────────────────────────────────────

    def _schedule_reconnect(self) -> None:
        if self._reconnect_task and not self._reconnect_task.done():
            return  # already scheduled

        self.emit("status", {"msg": f"Reconnecting in {self._reconnect_delay:.0f}s"})
        self._reconnect_task = asyncio.ensure_future(
            self._reconnect(), loop=self._loop
        )

    async def _reconnect(self) -> None:
        await asyncio.sleep(self._reconnect_delay)
        try:
            await self._loop.run_in_executor(
                None,
                lambda: self._client.reconnect(),
            )
        except Exception as e:
            self.emit("error", {"msg": f"Reconnect failed: {e}"})
            # Exponential backoff: double delay, cap at 60s
            self._reconnect_delay = min(self._reconnect_delay * 2, 60.0)
            if self._running:
                self._schedule_reconnect()
        else:
            self._reconnect_delay = 1.0
```

**Step 2: Register in sources/__init__.py**

Append to `backend/discover_client/sources/__init__.py`:

```python
# Auto-register built-in sources on import
from discover_client.sources.mqtt_source import MqttSource

register("mqtt", MqttSource)
```

**Step 3: Verify import**

Run: `cd backend && uv run python -c "from discover_client.sources.mqtt_source import MqttSource; from discover_client.sources import get; assert get('mqtt') is MqttSource; print('MqttSource registered OK')"`
Expected: `MqttSource registered OK`

---

### Task 2.3: Flesh out DiscoverClient

**Objective:** Replace the stub DiscoverClient with real source lifecycle management.

**Files:**
- Modify: `backend/discover_client/client.py`

**Step 1: Replace client.py**

```python
"""Discover Client orchestrator."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

from discover_client.output import OutputQueue
from discover_client.source import Source, SourceConfig, SourceEvent
from discover_client.sources import get as get_source_class

logger = logging.getLogger(__name__)


class DiscoverClient:
    """Owns the set of active sources and their lifecycle."""

    def __init__(self, output: OutputQueue | None = None) -> None:
        self.output = output or OutputQueue()
        self._subscribers: list[Callable[[SourceEvent], None]] = []
        self._sources: dict[str, Source] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    async def start(self, enabled: list[SourceConfig]) -> None:
        """Instantiate and activate all enabled sources concurrently."""
        tasks = []
        for config in enabled:
            if not config.enabled:
                continue
            tasks.append(self._start_one(config))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for config, result in zip(
                [c for c in enabled if c.enabled], results
            ):
                if isinstance(result, Exception):
                    logger.error(
                        "Failed to start source '%s': %s", config.source_id, result
                    )

    async def _start_one(self, config: SourceConfig) -> None:
        cls = get_source_class(config.source_type)
        source = cls(config, emit=self._on_event)
        self._sources[config.source_id] = source
        await source.start()

    async def stop(self) -> None:
        """Gracefully shut down all active sources."""
        stops = []
        for source in self._sources.values():
            stops.append(source.stop())
        if stops:
            await asyncio.gather(*stops, return_exceptions=True)
        self._sources.clear()

    async def add_source(self, config: SourceConfig) -> None:
        """Register and start a source at runtime."""
        await self._start_one(config)

    async def remove_source(self, source_id: str) -> None:
        """Stop and remove a source by id."""
        source = self._sources.pop(source_id, None)
        if source:
            await source.stop()

    def subscribe(self, callback: Callable[[SourceEvent], None]) -> None:
        """Register a subscriber callback for all events."""
        self._subscribers.append(callback)

    def _on_event(self, event: SourceEvent) -> None:
        """Route events to the output queue and all subscribers."""
        self.output.put(event)
        for sub in self._subscribers:
            try:
                sub(event)
            except Exception:
                logger.exception("Subscriber callback failed")
```

**Step 2: Verify import and basic instantiation**

Run: `cd backend && uv run python -c "from discover_client.client import DiscoverClient; c = DiscoverClient(); print('DiscoverClient instantiated OK')"`
Expected: `DiscoverClient instantiated OK`

---

### Task 2.4: Update __init__.py exports

**Objective:** Export new classes.

**Files:**
- Modify: `backend/discover_client/__init__.py`

**Step 1: Update**

Replace `__init__.py` content:

```python
"""Discover Client — data-source aggregation layer for Smart Room."""

from discover_client.client import DiscoverClient
from discover_client.source import Source, SourceEvent, SourceConfig
from discover_client.output import OutputQueue
from discover_client.config import load_config, SourceTypeSchema, SCHEMAS
from discover_client.sources import registered_types

__all__ = [
    "DiscoverClient",
    "Source",
    "SourceEvent",
    "SourceConfig",
    "OutputQueue",
    "load_config",
    "SourceTypeSchema",
    "SCHEMAS",
    "registered_types",
]
```

**Step 2: Verify**

Run: `cd backend && uv run python -c "from discover_client import DiscoverClient, registered_types; print(f'registered types: {registered_types()}')"`
Expected: `registered types: ['mqtt']`

---

### Task 2.5: Integration smoke test

**Objective:** Verify MQTT source works end-to-end with a real broker.

**Files:**
- Create: `backend/discover_client/sources/test_mqtt_source.py`

**Step 1: Create test**

```python
"""Integration test: MQTT Source → real broker → SourceEvent."""

import asyncio
import subprocess
import sys
import time

import paho.mqtt.client as mqtt

from discover_client.client import DiscoverClient
from discover_client.source import SourceConfig
from discover_client.output import OutputQueue

BROKER_HOST = "127.0.0.1"
BROKER_PORT = 1884  # non-default to avoid clashing with user's broker

TEST_TOPIC = "test/discover/smoke"


def _start_mosquitto():
    """Start a mosquitto broker for testing. Returns the Popen handle."""
    proc = subprocess.Popen(
        ["mosquitto", "-p", str(BROKER_PORT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.5)  # let broker start
    return proc


async def _publish_test_message(payload: str) -> None:
    """Publish a single test message to the broker."""
    pub = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    pub.connect(BROKER_HOST, BROKER_PORT)
    pub.loop_start()
    info = pub.publish(TEST_TOPIC, payload, qos=1)
    info.wait_for_publish(timeout=2.0)
    pub.loop_stop()
    pub.disconnect()


async def main():
    # Check mosquitto is available
    try:
        subprocess.run(["mosquitto", "--help"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("SKIP: mosquitto not found on PATH")
        return

    print("Starting test mosquitto broker...")
    broker = _start_mosquitto()

    try:
        output = OutputQueue()
        client = DiscoverClient(output=output)

        config = SourceConfig(
            source_id="test-mqtt",
            source_type="mqtt",
            enabled=True,
            settings={
                "host": BROKER_HOST,
                "port": BROKER_PORT,
                "topic_whitelist": ["test/#"],
                "topic_blacklist": [],
            },
        )

        print("Starting DiscoverClient...")
        await client.start([config])
        await asyncio.sleep(0.5)  # wait for connection

        # Publish a test message
        print("Publishing test message...")
        await _publish_test_message('{"value": 42, "unit": "C"}')

        # Receive the event
        event = await asyncio.wait_for(output.get(), timeout=3.0)
        print(f"✓ Received event: {event.event_type} from {event.source_id}")

        assert event.source_id == "test-mqtt"
        assert event.source_type == "mqtt"
        assert event.event_type == "data"
        assert event.payload["topic"] == TEST_TOPIC
        assert '"value": 42' in event.payload["value"]
        print(f"  ✓ payload: topic={event.payload['topic']}, value={event.payload['value']}")

        # Verify topic blacklist filtering
        print("Testing blacklist...")
        config2 = SourceConfig(
            source_id="test-mqtt-bl",
            source_type="mqtt",
            enabled=True,
            settings={
                "host": BROKER_HOST,
                "port": BROKER_PORT,
                "topic_whitelist": [],
                "topic_blacklist": ["test/discover/smoke"],
            },
        )
        output2 = OutputQueue()
        client2 = DiscoverClient(output=output2)
        await client2.start([config2])
        await asyncio.sleep(0.3)

        await _publish_test_message("should be filtered")
        # There might be a "connected" status event, skip it
        # The blacklisted message should NOT appear
        # Wait briefly and check no data events arrived
        try:
            while True:
                event = await asyncio.wait_for(output2.get(), timeout=1.0)
                if event.event_type == "data":
                    assert False, f"Blacklisted message leaked through: {event.payload}"
                # status events are expected, keep consuming
        except asyncio.TimeoutError:
            pass  # expected — no data events
        print("  ✓ blacklisted message correctly filtered")

        # Cleanup
        await client.stop()
        await client2.stop()
        print("\n✓ All integration tests passed")

    finally:
        broker.terminate()
        broker.wait()


if __name__ == "__main__":
    asyncio.run(main())
```

**Step 2: Run test**

Run: `cd backend && uv run python discover_client/sources/test_mqtt_source.py`
Expected: Output ending with `✓ All integration tests passed`

Note: requires `mosquitto` on PATH. If missing, the test prints `SKIP` and exits cleanly.

---

### Task 2.6: Cleanup

- Delete the smoke test from Task 1 if it's no longer needed: `backend/discover_client/smoke_test.py`
