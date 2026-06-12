# Discover Client — Architecture Spec

**Status:** draft  
**Date:** 2026-06-12

## Overview

Discover Client is the data-source aggregation layer for the Smart Room backend. It is **not** a device-discovery or data-cleaning module. Its single responsibility: connect to user-selected sources, pull raw data from them, and emit structured events downstream for later modules to process.

## Design Constraints

- **Stateless about meaning.** Discover Client does not parse device models, extract operations, or classify data. It passes opaque payloads forward.
- **Peer architecture.** Every source is a first-class citizen implementing the same interface. No master/slave, no special-cased routing per source type.
- **User opt-in.** Sources are individually enabled/disabled. Every source has a configuration block — contents vary by source type, but the config model is uniform.
- **Fault-isolated.** A misbehaving source must not crash or block other sources.
- **Async-native.** Built on asyncio. Fits the existing FastAPI + aiohttp stack.

## Source Types

All sources share the same config model. What differs is the contents of their `settings` block.

| Source | Key Settings |
|--------|--------------|
| MQTT Broker | host, port, username/password (optional), topic whitelist/blacklist |
| Home Assistant | base URL, long-lived access token |
| mDNS | scan interval, service types to watch |
| SSDP | scan interval |
| *(future)* Zigbee2MQTT | host, port |
| *(future)* Custom HTTP poller | URL, method, headers, interval |

Sources that appear "zero-config" to the user (mDNS, SSDP) still have a settings block — it just defaults to reasonable values and the user-facing config page can be left blank.

## Directory Layout

```
backend/
  discover_client/
    __init__.py          # public API: create_client(), start(), stop()
    client.py            # DiscoverClient orchestrator
    source.py            # Source abstract base class + data model
    config.py            # per-source config schema & validation
    sources/
      __init__.py
      mqtt_source.py
      home_assistant_source.py
      mdns_source.py
      ssdp_source.py
    output.py            # OutputQueue / event bus (fan-out to downstream)
```

## Core Abstractions

### 1. DiscoverClient (orchestrator)

```python
class DiscoverClient:
    """Owns the set of active sources and their lifecycle."""

    async def start(self, enabled: list[SourceConfig]) -> None: ...
    async def stop(self) -> None: ...
    async def add_source(self, config: SourceConfig) -> None: ...
    async def remove_source(self, source_id: str) -> None: ...
    def subscribe(self, callback: Callable[[SourceEvent], None]) -> None: ...
```

- `start()` instantiates and activates all enabled sources.
- `stop()` gracefully shuts down all sources.
- Sources can be added/removed at runtime.
- Downstream consumers subscribe via callback or `async for` iteration over an output stream.

### 2. Source (abstract base)

```python
class Source(ABC):
    """One data source. Manages its own connection and event emission."""

    source_id: str          # unique, assigned from config
    source_type: str        # e.g. "mqtt", "home_assistant", "mdns"
    enabled: bool

    @abstractmethod
    async def start(self) -> None: ...
    @abstractmethod
    async def stop(self) -> None: ...

    # Called by concrete sources to push events out
    def emit(self, event: SourceEvent) -> None: ...
```

Each concrete source:
- Is constructed with its config and a reference to an output channel (the `emit` callback or queue).
- Handles its own reconnection logic. The orchestrator only calls `start()` / `stop()`.
- Must not raise unhandled exceptions out of `start()`; errors are surfaced as error-level `SourceEvent`s.

### 3. SourceEvent (unified output envelope)

```python
@dataclass
class SourceEvent:
    source_id: str              # which source produced this
    source_type: str            # "mqtt", "mdns", etc.
    timestamp: float            # time.time() when received
    event_type: str             # "data", "discovery", "error", "status"
    payload: dict[str, Any]     # opaque; structure depends on source_type
```

`event_type` semantics:
- `"data"` — a data point from a connected source (MQTT message, HA state change, etc.)
- `"discovery"` — a new device/service found (mDNS/SSDP announcement)
- `"error"` — source-level error (connection lost, auth failure)
- `"status"` — lifecycle event (source started, stopped, reconnecting)

The `payload` dict is source-defined. Examples:

**MQTT data event:**
```json
{
  "source_id": "mqtt-lab",
  "source_type": "mqtt",
  "timestamp": 1718123456.789,
  "event_type": "data",
  "payload": {
    "topic": "govee/H5179/a1b2c3d4e5f6/temperature",
    "value": "{\"value\": 21.3, \"unit\": \"C\"}",
    "qos": 0
  }
}
```

**mDNS discovery event:**
```json
{
  "source_id": "mdns-lan",
  "source_type": "mdns",
  "timestamp": 1718123456.789,
  "event_type": "discovery",
  "payload": {
    "service_type": "_mqtt._tcp.local.",
    "host": "mosquitto.local",
    "addresses": ["192.168.1.100"],
    "port": 1883,
    "properties": {}
  }
}
```

### 4. SourceConfig (user-facing configuration)

```python
@dataclass
class SourceConfig:
    source_id: str              # user-chosen name, e.g. "mqtt-lab", "mdns-lan"
    source_type: str            # must match a registered Source class
    enabled: bool
    settings: dict[str, Any]    # type-specific; validated per source_type
```

Per-type settings schema:

**MQTT:**
```json
{
  "host": "192.168.1.100",
  "port": 1883,
  "username": null,
  "password": null,
  "topic_whitelist": ["govee/#", "zigbee2mqtt/#"],
  "topic_blacklist": ["#/debug", "#/raw"]
}
```

- If `topic_whitelist` is non-empty, only matching topics are forwarded as events.
- If `topic_whitelist` is empty (default), all topics are forwarded, minus any matched by `topic_blacklist`.
- Both lists support MQTT wildcards (`+` for single level, `#` for multi-level).

**Home Assistant:**
```json
{
  "base_url": "http://192.168.1.200:8123",
  "token": "eyJhbGciOi..."
}
```

**mDNS:**
```json
{
  "scan_interval_s": 30,
  "service_types": ["_mqtt._tcp.local.", "_home-assistant._tcp.local.", "_http._tcp.local."]
}
```

- `scan_interval_s` controls how often a full scan cycle runs. Discovery of new services is event-driven (zeroconf callbacks); the interval governs periodic full sweeps.
- `service_types` limits which mDNS services are watched. Empty list means "all".

**SSDP:**
```json
{
  "scan_interval_s": 60
}
```

Settings are validated per source type at config load time, before source construction.

## Lifecycle

```
  Config loaded
       │
       ▼
  DiscoverClient.start(enabled_configs)
       │
       ├─► MQTT_Source.start()        ──► connect → subscribe → emit events
       ├─► HA_Source.start()          ──► connect → subscribe → emit events
       ├─► mDNS_Source.start()        ──► start scanner → emit events
       └─► SSDP_Source.start()        ──► start scanner → emit events
                                              │
                          ┌───────────────────┘
                          ▼
                   OutputQueue / callback
                          │
                          ▼
                   Downstream module(s)
```

- Sources start concurrently; slow sources (e.g. mDNS waiting for responses) do not block fast ones (MQTT connecting).
- On `stop()`, all sources receive shutdown signal, get a grace period, then are cancelled.
- On source failure (connection drop), the source logs an error event and attempts reconnection with backoff internally. The orchestrator is not involved.

## Output Mechanism

Discover Client exposes output via an **async queue** and/or **callback subscription**:

```python
# Option A: iterate
client = DiscoverClient()
await client.start(configs)
async for event in client.events():
    downstream.handle(event)

# Option B: subscribe callback
def handle(event: SourceEvent):
    # push to downstream module
    pass
client = DiscoverClient()
client.subscribe(handle)
await client.start(configs)
```

The output queue is unbounded (backpressure is the downstream's concern). If the queue grows beyond a configurable warning threshold, the client emits a status event.

## First-Batch Implementation Scope

**v0.1 sources to implement:**
1. MQTT Source — connect to broker, subscribe to topic filter, emit `"data"` events per message
2. mDNS Source — scan for `_mqtt._tcp`, `_home-assistant._tcp`, `_http._tcp`; emit `"discovery"` events
3. SSDP Source — scan for UPnP devices; emit `"discovery"` events

**Deferred to later:**
- Home Assistant Source
- Hot-reload config (add/remove sources at runtime via API)

## Error Handling Policy

| Scenario | Behaviour |
|----------|----------|
| Source fails to start (bad config) | Emit `"error"` event, skip source, continue starting others |
| Source loses connection mid-run | Emit `"error"` event, begin exponential backoff reconnect (1s→2s→4s→...→max 60s). Emit `"status"` event on reconnect. |
| Source raises unhandled exception | Catch at Source boundary, emit `"error"` event, mark source as `faulted`. Do NOT restart automatically — user must intervene. |
| Output queue consumer too slow | Emit `"status"` event with queue depth. Drop oldest events if queue exceeds hard limit (configurable). |

## Dependencies (Python)

Already in `pyproject.toml`:
- `paho-mqtt>=2.0.0` — MQTT source
- `aiohttp>=3.8.0` — async HTTP for HA source (future)

New dependencies needed:
- `zeroconf>=0.130` — mDNS service discovery (pure Python, no system deps)
- *(SSDP can be implemented with stdlib `asyncio` + UDP sockets, no extra dep)*

## Testing Strategy

- **Unit tests per source:** mock the external library (e.g. mock `paho.mqtt.client`), verify correct event emission.
- **Integration tests:** spin up a real mosquitto broker (Docker or local) and a Discover Client with MQTT source, publish messages, assert events arrive.
- **Test mDNS/SSDP:** use `zeroconf` test utilities to register fake services, verify discovery events.

## Configuration Storage

Source configurations are stored in a **TOML** file at `backend/discover_client/config.toml`.

```toml
# discover_client/config.toml

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

The `config.py` module reads this file on startup and validates each `[[sources]]` block against its `source_type` schema before constructing `SourceConfig` objects.

## GUI (PySide6 + qt-material)

The configuration panel is built with PySide6 + qt-material for theming. Uses `qt_material.apply_stylesheet(app, theme='dark_teal.xml')` for a Material Design dark theme — no hand-written QSS needed.

### Event loop model

Same pattern as quest3server: `threading.Thread` for asyncio (Discover Client), main thread for Qt event loop. The worker emits Qt signals back to the main thread to update UI state.

```
Main thread:  QApplication + MainWindow (Qt event loop)
Worker thread: DiscoverClient.start() (asyncio event loop)
```

### Window layout

```
┌──────────────────────────────────────────────────────────────┐
│  [Config] [Monitor]                         [▶ Start] [■ Stop] │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─ Config Tab ───────────────────────────────────────────┐ │
│  │ ┌──────────┬──────────────────────────────────────────┐│ │
│  │ │ Sources  │  Settings: mqtt-lab                      ││ │
│  │ │          │                                          ││ │
│  │ │ ┌──────┐ │  Host      [192.168.1.100          ]     ││ │
│  │ │ │ MQTT │ │  Port      [1883              ▲▼  ]     ││ │
│  │ │ │  ●   │ │  Username  [                       ]     ││ │
│  │ │ └──────┘ │  Password  [●●●●●●●●              ]     ││ │
│  │ │ ┌──────┐ │  Whitelist ┌──────────────────────┐     ││ │
│  │ │ │ mDNS │ │            │govee/#               │     ││ │
│  │ │ │  ●   │ │            │zigbee2mqtt/#         │     ││ │
│  │ │ └──────┘ │            └──────────────────────┘     ││ │
│  │ │ ┌──────┐ │  Blacklist ┌──────────────────────┐     ││ │
│  │ │ │ SSDP │ │            │                      │     ││ │
│  │ │ │  ○   │ │            └──────────────────────┘     ││ │
│  │ │ └──────┘ │                              [💾 Save]  ││ │
│  │ │ [+ Add]  │                                          ││ │
│  │ └──────────┴──────────────────────────────────────────┘│ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
│  ┌─ Monitor Tab ──────────────────────────────────────────┐ │
│  │ ┌─────────────────────────────────────────────────────┐│ │
│  │ │ mqtt-lab  ● connected  msgs: 142  rate: 2.0/s      ││ │
│  │ │ ┌────────┬─────────────────────────────────────────┐││ │
│  │ │ │ 12:34  │ govee/H5179/.../temperature  21.3 °C    │││ │
│  │ │ │ 12:34  │ govee/H5179/.../humidity     60.7 %     │││ │
│  │ │ │ 12:33  │ govee/H5179/.../temperature  21.2 °C    │││ │
│  │ │ └────────┴─────────────────────────────────────────┘││ │
│  │ ├─────────────────────────────────────────────────────┤│ │
│  │ │ mdns-lan  ● scanning  services: 3  events: 12      ││ │
│  │ │ ┌────────┬─────────────────────────────────────────┐││ │
│  │ │ │ 12:32  │ + _matter._tcp  58044F9ADC05.local:5540 │││ │
│  │ │ │ 12:31  │ + _matter._tcp  BED59ECF...             │││ │
│  │ │ └────────┴─────────────────────────────────────────┘││ │
│  │ ├─────────────────────────────────────────────────────┤│ │
│  │ │ ssdp-lan  ○ disabled                               ││ │
│  │ └─────────────────────────────────────────────────────┘│ │
│  └────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

### Tabs

**Config Tab** — Two-panel layout:

| Panel | Content |
|-------|---------|
| Left: Source list (250px) | Logo area at top, scrollable source cards. Each card: icon (type), source_id, status dot (●/○), click to select. Bottom: [+ Add] button. |
| Right: Auto-generated form | Dynamically built from `config.py` SCHEMAS for the selected source_type. Field types inferred from schema: str → QLineEdit, int → QSpinBox, list → QTextEdit (one item per line), password/token → QLineEdit with echoMode=Password. Save button writes to config.toml. |

**Monitor Tab** — Per-source data panels:

| Column | Content |
|--------|---------|
| Source header | source_id, status indicator, message count, event rate (events/sec) |
| Event table | QTableWidget showing time, event_type, compact payload summary. Auto-scrolls. Max 200 rows per source. |
| Disabled sources | Shown as collapsed/inactive row with "disabled" label. |

### Auto-form field mapping

```python
FIELD_FOR_KEY: dict[str, type | tuple] = {
    # Generic patterns (checked first)
    "host": QLineEdit,
    "port": (QSpinBox, 1, 65535),
    "username": QLineEdit,
    "password": (QLineEdit, "password"),
    "token": (QLineEdit, "password"),
    "base_url": QLineEdit,
    # List fields → multi-line text
    "topic_whitelist": QTextEdit,
    "topic_blacklist": QTextEdit,
    "service_types": QTextEdit,
    "search_targets": QTextEdit,
    # Integer fields
    "scan_interval_s": (QSpinBox, 1, 3600),
}
```

Keys not in this map default to QLineEdit for strings, QSpinBox for ints.

### Dependencies

- `qt-material>=2.17` — already in pyproject.toml
