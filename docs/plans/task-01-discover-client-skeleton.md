# Task 1: Discover Client Skeleton — OpenCode Prompt

**Task:** Create the `backend/discover_client/` package skeleton with core abstractions (Source ABC, SourceEvent, SourceConfig, OutputQueue, TOML config loader).

**Context:**

This is part of the Smart Room AR Dashboard project — a Quest 3 + backend system that streams device data to floating AR panels. Discover Client is the data-source aggregation layer: users configure sources (MQTT broker, mDNS, SSDP, etc.), and the client pulls raw data from all enabled sources and emits structured events downstream.

The architecture spec is at `docs/discover-client-spec.md`. Key abstractions:

1. **Source (ABC)** — every data source implements `start()` / `stop()` and calls `emit(event)` to push events.
2. **SourceEvent** — a dataclass with `source_id`, `source_type`, `timestamp`, `event_type`, `payload`.
3. **SourceConfig** — a dataclass with `source_id`, `source_type`, `enabled`, `settings`.
4. **OutputQueue** — an `asyncio.Queue` that sources push events into; downstream consumers pull from it.
5. **Config loader** — reads `discover_client/config.toml`, validates each `[[sources]]` block against its `source_type` schema, returns `list[SourceConfig]`.

**What to create:**

### File 1: `backend/discover_client/__init__.py`

```python
"""Discover Client — data-source aggregation layer for Smart Room."""

from discover_client.client import DiscoverClient
from discover_client.source import Source, SourceEvent, SourceConfig
from discover_client.output import OutputQueue
from discover_client.config import load_config, SourceTypeSchema, SCHEMAS

__all__ = [
    "DiscoverClient",
    "Source",
    "SourceEvent",
    "SourceConfig",
    "OutputQueue",
    "load_config",
    "SourceTypeSchema",
    "SCHEMAS",
]
```

### File 2: `backend/discover_client/source.py`

```python
"""Core abstractions: Source ABC, SourceEvent, SourceConfig."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class SourceEvent:
    """Unified output envelope for all source types.

    Attributes:
        source_id: Which source produced this (e.g. "mqtt-lab").
        source_type: Source type key (e.g. "mqtt", "mdns").
        timestamp: time.time() when the event was captured.
        event_type: One of "data", "discovery", "error", "status".
        payload: Opaque dict; structure depends on source_type.
    """

    source_id: str
    source_type: str
    timestamp: float
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceConfig:
    """User-facing configuration for one source.

    Attributes:
        source_id: User-chosen name (e.g. "mqtt-lab").
        source_type: Must match a registered Source class (e.g. "mqtt").
        enabled: Whether this source should be started.
        settings: Type-specific dict; validated per source_type.
    """

    source_id: str
    source_type: str
    enabled: bool = True
    settings: dict[str, Any] = field(default_factory=dict)


class Source(ABC):
    """Abstract base for all data sources.

    Each concrete source manages its own connection and event emission.
    The orchestrator only calls start() / stop().
    """

    def __init__(self, config: SourceConfig, emit: Callable[[SourceEvent], None]) -> None:
        self.config = config
        self.source_id = config.source_id
        self.source_type = config.source_type
        self._emit = emit

    @abstractmethod
    async def start(self) -> None:
        """Activate this source. Must not raise unhandled exceptions."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully shut down this source."""
        ...

    def emit(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        """Push a SourceEvent through the output channel."""
        import time

        self._emit(
            SourceEvent(
                source_id=self.source_id,
                source_type=self.source_type,
                timestamp=time.time(),
                event_type=event_type,
                payload=payload or {},
            )
        )
```

### File 3: `backend/discover_client/output.py`

```python
"""Output mechanism for Discover Client events."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from discover_client.source import SourceEvent


class OutputQueue:
    """An unbounded async queue that sources push events into.

    Downstream modules pull events via async iteration or callback.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[SourceEvent] = asyncio.Queue()

    def put(self, event: SourceEvent) -> None:
        """Push an event into the queue (non-blocking)."""
        self._queue.put_nowait(event)

    async def get(self) -> SourceEvent:
        """Await the next event."""
        return await self._queue.get()

    async def __aiter__(self) -> AsyncIterator[SourceEvent]:
        """Iterate over events as they arrive."""
        while True:
            yield await self.get()

    @property
    def depth(self) -> int:
        """Current number of un-consumed events."""
        return self._queue.qsize()
```

### File 4: `backend/discover_client/config.py`

```python
"""TOML configuration loader for Discover Client sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from discover_client.source import SourceConfig

# We'll use tomli (stdlib tomllib on 3.11+) for TOML parsing.
# On Python < 3.11, tomllib is unavailable; use tomli as fallback.
try:
    import tomllib  # Python >= 3.11
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass
class SourceTypeSchema:
    """Validation schema for a source type's settings."""

    required: list[str] = field(default_factory=list)
    defaults: dict[str, Any] = field(default_factory=dict)

    def validate(self, settings: dict[str, Any]) -> dict[str, Any]:
        """Merge defaults and check required keys. Returns validated settings."""
        merged = {**self.defaults, **settings}
        for key in self.required:
            if key not in merged or merged[key] is None:
                raise ValueError(
                    f"Missing required setting '{key}' for source type"
                )
        return merged


# ── Registered schemas per source_type ──────────────────────────────

SCHEMAS: dict[str, SourceTypeSchema] = {
    "mqtt": SourceTypeSchema(
        required=["host", "port"],
        defaults={
            "host": "localhost",
            "port": 1883,
            "username": None,
            "password": None,
            "topic_whitelist": [],
            "topic_blacklist": [],
        },
    ),
    "mdns": SourceTypeSchema(
        required=[],
        defaults={
            "scan_interval_s": 30,
            "service_types": [
                "_mqtt._tcp.local.",
                "_home-assistant._tcp.local.",
                "_http._tcp.local.",
            ],
        },
    ),
    "ssdp": SourceTypeSchema(
        required=[],
        defaults={
            "scan_interval_s": 60,
        },
    ),
    "home_assistant": SourceTypeSchema(
        required=["base_url", "token"],
        defaults={
            "base_url": "",
            "token": "",
        },
    ),
}


def load_config(path: str | Path | None = None) -> list[SourceConfig]:
    """Load and validate source configurations from a TOML file.

    Args:
        path: Path to config.toml. Defaults to discover_client/config.toml
              relative to this module's directory.

    Returns:
        List of validated SourceConfig objects.

    Raises:
        FileNotFoundError: If the config file doesn't exist.
        ValueError: If any source block fails validation.
    """
    if path is None:
        path = Path(__file__).resolve().parent / "config.toml"
    else:
        path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, "rb") as f:
        data = tomllib.load(f)

    sources_raw = data.get("sources", [])
    if not isinstance(sources_raw, list):
        raise ValueError("TOML must contain a [[sources]] array")

    configs: list[SourceConfig] = []
    for i, block in enumerate(sources_raw):
        source_id = block.get("source_id", f"source-{i}")
        source_type = block.get("source_type", "")
        enabled = block.get("enabled", True)
        raw_settings = block.get("settings", {})

        if not source_type:
            raise ValueError(f"source_id='{source_id}' is missing source_type")

        schema = SCHEMAS.get(source_type)
        if schema is None:
            raise ValueError(
                f"Unknown source_type='{source_type}' for source_id='{source_id}'"
            )

        settings = schema.validate(raw_settings)
        configs.append(
            SourceConfig(
                source_id=source_id,
                source_type=source_type,
                enabled=enabled,
                settings=settings,
            )
        )

    return configs
```

### File 5: `backend/discover_client/config.toml` (example)

```toml
# discover_client/config.toml — source configurations

[[sources]]
source_id = "mqtt-lab"
source_type = "mqtt"
enabled = true

[sources.settings]
host = "192.168.1.100"
port = 1883
topic_whitelist = ["govee/#", "zigbee2mqtt/#"]
topic_blacklist = []

[[sources]]
source_id = "mdns-lan"
source_type = "mdns"
enabled = true

[sources.settings]
scan_interval_s = 30
service_types = ["_mqtt._tcp.local.", "_home-assistant._tcp.local.", "_http._tcp.local."]

[[sources]]
source_id = "ssdp-lan"
source_type = "ssdp"
enabled = false

[sources.settings]
scan_interval_s = 60
```

### File 6: Update `backend/pyproject.toml`

The `[tool.setuptools.packages.find]` section currently only includes `quest3server`. Add `discover_client`:

```toml
[tool.setuptools.packages.find]
include = ["quest3server*", "discover_client*"]
```

### File 7: Add `tomli` dependency for Python <3.11 compatibility

In `[project]`, add to dependencies:
```
"tomli>=2.0.0; python_version < '3.11'",
```

### Verification

After creating all files:
1. `cd backend && uv run python -c "from discover_client import Source, SourceEvent, SourceConfig, OutputQueue, load_config, SCHEMAS; print('imports OK')"` — should print "imports OK"
2. `cd backend && uv run python -c "from discover_client.config import load_config; configs = load_config(); print(f'Loaded {len(configs)} configs'); [print(f'  {c.source_id} ({c.source_type}) enabled={c.enabled}') for c in configs]"` — should print 3 loaded configs
3. `cd backend && uv run python -c "from discover_client.output import OutputQueue; q = OutputQueue(); print(f'Queue depth: {q.depth}')"` — should print "Queue depth: 0"
