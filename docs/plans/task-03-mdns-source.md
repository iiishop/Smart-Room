# Task 3: mDNS Source — Implementation Plan

> **For Hermes:** Use opencode run with this plan + discover-client-spec.md as context files.

**Goal:** Implement mDNS service discovery source using zeroconf. Emits "discovery" events when services are found.

**Architecture:** MdnsSource wraps zeroconf's `ServiceBrowser` + `AsyncZeroconf`. Zeroconf callbacks fire on add/remove; the source translates them into SourceEvents and emits through the thread-safe bridge. Periodic full sweeps at `scan_interval_s`.

**Tech Stack:** zeroconf >= 0.130, asyncio.

**New dependency:** `zeroconf>=0.130` must be added to `backend/pyproject.toml`.

---

### Task 3.1: Add zeroconf dependency

**Objective:** Add zeroconf to pyproject.toml dependencies.

**Files:**
- Modify: `backend/pyproject.toml`

**Step 1: Patch**

Add `"zeroconf>=0.130",` to the `dependencies` list in `[project]`.

**Step 2: Install**

Run: `cd backend && uv sync`
Expected: zeroconf installed without errors.

---

### Task 3.2: Create mDNS Source

**Objective:** Implement MdnsSource that discovers mDNS services.

**Files:**
- Create: `backend/discover_client/sources/mdns_source.py`

**Step 1: Create file**

```python
"""mDNS service discovery source using zeroconf."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from zeroconf import (
    AsyncServiceBrowser,
    AsyncServiceInfo,
    AsyncZeroconf,
    ServiceStateChange,
)
from zeroconf.asyncio import AsyncZeroconf as ZeroconfClass

from discover_client.source import Source, SourceConfig, SourceEvent

logger = logging.getLogger(__name__)


def _service_info_to_payload(
    service_type: str, name: str, info: AsyncServiceInfo | None
) -> dict[str, Any]:
    """Convert a zeroconf service to a discovery event payload."""
    payload: dict[str, Any] = {
        "service_type": service_type,
        "name": name,
        "host": None,
        "addresses": [],
        "port": None,
        "properties": {},
    }
    if info is not None:
        payload["host"] = info.server
        if info.parsed_addresses():
            payload["addresses"] = info.parsed_addresses()
        payload["port"] = info.port
        if info.properties:
            payload["properties"] = {
                k.decode("utf-8", errors="replace") if isinstance(k, bytes) else k:
                v.decode("utf-8", errors="replace") if isinstance(v, bytes) else v
                for k, v in info.properties.items()
            }
    return payload


class MdnsSource(Source):
    """Discovers mDNS services and emits discovery events.

    Thread-safety: zeroconf callbacks may fire from its internal event loop.
    This class schedules emits onto the asyncio loop via call_soon_threadsafe.
    """

    def __init__(self, config: SourceConfig, emit) -> None:
        super().__init__(config, emit)
        self._scan_interval: int = int(config.settings.get("scan_interval_s", 30))
        self._service_types: list[str] = config.settings.get(
            "service_types",
            ["_mqtt._tcp.local.", "_home-assistant._tcp.local.", "_http._tcp.local."],
        )
        self._zeroconf: AsyncZeroconf | None = None
        self._browsers: list[AsyncServiceBrowser] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False
        self._sweep_task: asyncio.Task | None = None

    def emit(self, event_type: str, payload: dict | None = None) -> None:
        """Thread-safe emit."""
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

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._running = True

        try:
            self._zeroconf = AsyncZeroconf()
        except Exception as exc:
            self.emit("error", {"msg": f"Failed to initialize zeroconf: {exc}"})
            return

        # Start browsers for each service type
        for stype in self._service_types:
            browser = AsyncServiceBrowser(
                self._zeroconf.zeroconf,
                stype,
                handlers=[self._on_service_state_change],
            )
            self._browsers.append(browser)

        self.emit("status", {"msg": "scanning", "service_types": self._service_types})

        # Start periodic sweep
        self._sweep_task = asyncio.ensure_future(self._periodic_sweep())

    async def stop(self) -> None:
        self._running = False
        if self._sweep_task:
            self._sweep_task.cancel()
            self._sweep_task = None
        for browser in self._browsers:
            await browser.async_cancel()
        self._browsers.clear()
        if self._zeroconf:
            await self._zeroconf.async_close()
            self._zeroconf = None
        self.emit("status", {"msg": "stopped"})

    async def _periodic_sweep(self) -> None:
        """Periodic full sweep — queries all cached services for updated info."""
        while self._running:
            await asyncio.sleep(self._scan_interval)
            if not self._running or not self._zeroconf:
                break
            try:
                for stype in self._service_types:
                    infos = self._zeroconf.zeroconf.cache.entries_with_name(stype)
                    for name in infos:
                        info = AsyncServiceInfo(stype, name)
                        await info.async_request(self._zeroconf.zeroconf, 3000)
                        payload = _service_info_to_payload(stype, name, info)
                        self.emit("discovery", payload)
            except Exception as exc:
                self.emit("error", {"msg": f"Sweep error: {exc}"})

    def _on_service_state_change(
        self,
        zeroconf,
        service_type: str,
        name: str,
        state_change: ServiceStateChange,
    ) -> None:
        _ = zeroconf
        if state_change is ServiceStateChange.Added:
            self.emit(
                "discovery",
                _service_info_to_payload(service_type, name, None),
            )
        elif state_change is ServiceStateChange.Removed:
            self.emit(
                "discovery",
                {"service_type": service_type, "name": name, "removed": True},
            )
```

**Step 2: Register**

Append to `backend/discover_client/sources/__init__.py`:

```python
from discover_client.sources.mdns_source import MdnsSource

register("mdns", MdnsSource)
```

**Step 3: Verify import and registration**

Run: `cd backend && uv run python -c "from discover_client.sources.mdns_source import MdnsSource; from discover_client.sources import get, registered_types; assert get('mdns') is MdnsSource; print(f'MdnsSource registered OK. Types: {registered_types()}')"`
Expected: `MdnsSource registered OK. Types: ['mqtt', 'mdns']`

---

### Task 3.3: Unit test

**Objective:** Verify MdnsSource lifecycle (start/stop) and event emission shape using zeroconf's test utilities.

**Files:**
- Create: `backend/discover_client/sources/test_mdns_source.py`

**Step 1: Create test**

```python
"""Unit test for MdnsSource using zeroconf test infrastructure."""

import asyncio

from discover_client.source import SourceConfig
from discover_client.sources.mdns_source import MdnsSource, _service_info_to_payload
from discover_client.output import OutputQueue
from zeroconf import ServiceInfo


def test_payload_structure():
    """Verify discovery event payload format without needing network."""
    info = ServiceInfo(
        type_="_mqtt._tcp.local.",
        name="mosquitto._mqtt._tcp.local.",
        addresses=[b"\xc0\xa8\x01\x64"],  # 192.168.1.100
        port=1883,
        properties={b"version": b"2.0"},
        server="mosquitto.local.",
    )
    payload = _service_info_to_payload(
        "_mqtt._tcp.local.", "mosquitto._mqtt._tcp.local.", info
    )
    assert payload["service_type"] == "_mqtt._tcp.local."
    assert payload["port"] == 1883
    assert "192.168.1.100" in payload["addresses"]
    assert payload["host"] == "mosquitto.local."
    assert payload["properties"]["version"] == "2.0"
    print("✓ payload structure correct")

    # Without info (discovery event before resolve)
    payload2 = _service_info_to_payload(
        "_http._tcp.local.", "printer._http._tcp.local.", None
    )
    assert payload2["host"] is None
    assert payload2["addresses"] == []
    assert payload2["port"] is None
    print("✓ partial payload (pre-resolve) correct")


def test_emit_thread_safety():
    """Verify emit works without event loop (fallback path)."""
    queue = OutputQueue()
    config = SourceConfig(
        source_id="test-mdns",
        source_type="mdns",
        settings={},
    )
    source = MdnsSource(config, emit=queue.put)
    source.emit("status", {"msg": "test"})

    async def check():
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event.source_id == "test-mdns"
        assert event.event_type == "status"
        assert event.payload["msg"] == "test"
        print("✓ thread-safe emit works (no-loop fallback)")

    asyncio.run(check())


if __name__ == "__main__":
    test_payload_structure()
    test_emit_thread_safety()
    print()
    print("=== ALL UNIT TESTS PASSED ===")
```

**Step 2: Run test**

Run: `cd backend && uv run python discover_client/sources/test_mdns_source.py`
Expected: `=== ALL UNIT TESTS PASSED ===`

---

### Task 3.4: Update package exports

**Objective:** No changes needed — `registered_types()` already returns all registered types.

**Verify:** `cd backend && uv run python -c "from discover_client import registered_types; assert set(registered_types()) == {'mqtt', 'mdns'}; print('OK')"`
Expected: `OK`
