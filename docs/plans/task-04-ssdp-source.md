# Task 4: SSDP Source — Implementation Plan

> **For Hermes:** Use opencode run with this plan + discover-client-spec.md as context files.

**Goal:** Implement SSDP/UPnP device discovery source using raw asyncio UDP sockets.

**Architecture:** SsdpSource sends M-SEARCH over UDP multicast to 239.255.255.250:1900, listens for NOTIFY and M-SEARCH responses, parses them, and emits "discovery" events. No external dependencies — stdlib asyncio + socket only.

**Tech Stack:** asyncio, socket, stdlib `email.parser` for SSDP header parsing.

---

### Task 4.1: Create SSDP Source

**Objective:** Implement SsdpSource with M-SEARCH scan and periodic refresh.

**Files:**
- Create: `backend/discover_client/sources/ssdp_source.py`

**Step 1: Create file**

```python
"""SSDP / UPnP device discovery source."""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from email.parser import BytesParser
from io import BytesIO
from typing import Any

from discover_client.source import Source, SourceConfig

logger = logging.getLogger(__name__)

# Standard SSDP multicast address and port
SSDP_ADDR = ("239.255.255.250", 1900)

M_SEARCH_TEMPLATE = (
    "M-SEARCH * HTTP/1.1\r\n"
    "HOST: 239.255.255.250:1900\r\n"
    "MAN: \"ssdp:discover\"\r\n"
    "MX: 2\r\n"
    "ST: {search_target}\r\n"
    "\r\n"
).encode("ascii")

# Well-known UPnP service types to scan for
SEARCH_TARGETS = [
    "ssdp:all",
    "upnp:rootdevice",
    "urn:schemas-upnp-org:device:MediaRenderer:1",
    "urn:schemas-upnp-org:device:MediaServer:1",
    "urn:schemas-upnp-org:device:InternetGatewayDevice:1",
    "urn:schemas-upnp-org:service:WANIPConnection:1",
]


def _parse_ssdp_response(data: bytes, addr: tuple[str, int]) -> dict[str, Any] | None:
    """Parse an SSDP response into a discovery payload dict."""
    # Split headers from possible body
    parts = data.split(b"\r\n\r\n", 1)
    header_block = parts[0]

    # SSDP responses start with HTTP/1.1 200 OK or NOTIFY
    if not header_block.startswith(b"HTTP/") and not header_block.startswith(b"NOTIFY"):
        return None

    try:
        msg = BytesParser().parsebytes(header_block)
    except Exception:
        return None

    return {
        "host": addr[0],
        "port": addr[1],
        "location": msg.get("Location", ""),
        "server": msg.get("Server", ""),
        "st": msg.get("ST", msg.get("NT", "")),
        "usn": msg.get("USN", ""),
        "nt": msg.get("NT", ""),
        "nts": msg.get("NTS", ""),
    }


class SsdpSource(Source):
    """Discovers UPnP/SSDP devices on the local network."""

    def __init__(self, config: SourceConfig, emit) -> None:
        super().__init__(config, emit)
        self._scan_interval: int = int(config.settings.get("scan_interval_s", 60))
        self._search_targets: list[str] = config.settings.get(
            "search_targets", SEARCH_TARGETS
        )

        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: _SsdpProtocol | None = None
        self._running = False
        self._sweep_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._running = True

        loop = asyncio.get_running_loop()

        try:
            self._transport, self._protocol = await loop.create_datagram_endpoint(
                lambda: _SsdpProtocol(self._on_response),
                local_addr=("0.0.0.0", 0),
                family=socket.AF_INET,
                allow_broadcast=True,
            )
        except Exception as exc:
            self.emit("error", {"msg": f"Failed to create SSDP socket: {exc}"})
            return

        self.emit("status", {"msg": "scanning"})

        # Send initial M-SEARCH burst
        await self._send_msearch()
        self._sweep_task = asyncio.ensure_future(self._periodic_sweep())

    async def stop(self) -> None:
        self._running = False
        if self._sweep_task:
            self._sweep_task.cancel()
            self._sweep_task = None
        if self._transport:
            self._transport.close()
            self._transport = None
        self._protocol = None
        self.emit("status", {"msg": "stopped"})

    async def _send_msearch(self) -> None:
        """Send M-SEARCH for all configured search targets."""
        if self._transport is None:
            return
        for st in self._search_targets:
            msg = M_SEARCH_TEMPLATE.format(search_target=st)
            self._transport.sendto(msg, SSDP_ADDR)

    async def _periodic_sweep(self) -> None:
        """Periodically re-send M-SEARCH to discover new/updated devices."""
        while self._running:
            await asyncio.sleep(self._scan_interval)
            if not self._running:
                break
            await self._send_msearch()

    def _on_response(self, data: bytes, addr: tuple[str, int]) -> None:
        """Callback from the UDP protocol when a response arrives."""
        payload = _parse_ssdp_response(data, addr)
        if payload is None:
            return
        self.emit("discovery", payload)


class _SsdpProtocol(asyncio.DatagramProtocol):
    """asyncio UDP protocol for SSDP responses."""

    def __init__(self, callback) -> None:
        super().__init__()
        self._callback = callback

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._callback(data, addr)

    def error_received(self, exc: Exception) -> None:
        logger.warning("SSDP socket error: %s", exc)

    def connection_lost(self, exc: Exception | None) -> None:
        if exc:
            logger.warning("SSDP connection lost: %s", exc)
```

**Step 2: Register**

Append to `backend/discover_client/sources/__init__.py`:

```python
from discover_client.sources.ssdp_source import SsdpSource

register("ssdp", SsdpSource)
```

**Step 3: Verify import and registration**

Run: `cd backend && uv run python -c "from discover_client.sources.ssdp_source import SsdpSource; from discover_client.sources import get, registered_types; assert get('ssdp') is SsdpSource; print(f'SsdpSource registered OK. Types: {registered_types()}')"`
Expected: `SsdpSource registered OK. Types: ['mqtt', 'mdns', 'ssdp']`

---

### Task 4.2: Unit test

**Objective:** Verify SSDP response parsing works correctly with real SSDP packet samples.

**Files:**
- Create: `backend/discover_client/sources/test_ssdp_source.py`

**Step 1: Create test**

```python
"""Unit tests for SSDP response parsing and SsdpSource lifecycle."""

import asyncio
from discover_client.source import SourceConfig
from discover_client.sources.ssdp_source import SsdpSource, _parse_ssdp_response
from discover_client.output import OutputQueue


# Real SSDP response from a typical UPnP device
SAMPLE_NOTIFY = (
    b"NOTIFY * HTTP/1.1\r\n"
    b"HOST: 239.255.255.250:1900\r\n"
    b"CACHE-CONTROL: max-age=1800\r\n"
    b"LOCATION: http://192.168.1.1:5000/rootDesc.xml\r\n"
    b"NT: upnp:rootdevice\r\n"
    b"NTS: ssdp:alive\r\n"
    b"SERVER: Linux/3.14 UPnP/1.0 MyDevice/1.0\r\n"
    b"USN: uuid:1234-5678::upnp:rootdevice\r\n"
    b"\r\n"
)

SAMPLE_MSEARCH_RESPONSE = (
    b"HTTP/1.1 200 OK\r\n"
    b"CACHE-CONTROL: max-age=1800\r\n"
    b"LOCATION: http://192.168.1.50:49152/description.xml\r\n"
    b"ST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n"
    b"USN: uuid:a1b2c3d4::urn:schemas-upnp-org:device:MediaRenderer:1\r\n"
    b"SERVER: Sonos/57.0 UPnP/1.0\r\n"
    b"\r\n"
)

GARBAGE = b"this is not ssdp\r\n\r\n"


def test_parse_notify():
    result = _parse_ssdp_response(SAMPLE_NOTIFY, ("192.168.1.1", 1900))
    assert result is not None
    assert result["host"] == "192.168.1.1"
    assert result["location"] == "http://192.168.1.1:5000/rootDesc.xml"
    assert result["nt"] == "upnp:rootdevice"
    assert result["nts"] == "ssdp:alive"
    assert result["usn"] == "uuid:1234-5678::upnp:rootdevice"
    assert result["server"] == "Linux/3.14 UPnP/1.0 MyDevice/1.0"
    print("✓ NOTIFY parsed correctly")


def test_parse_msearch_response():
    result = _parse_ssdp_response(SAMPLE_MSEARCH_RESPONSE, ("192.168.1.50", 49152))
    assert result is not None
    assert result["st"] == "urn:schemas-upnp-org:device:MediaRenderer:1"
    assert result["location"] == "http://192.168.1.50:49152/description.xml"
    print("✓ M-SEARCH response parsed correctly")


def test_ignore_garbage():
    result = _parse_ssdp_response(GARBAGE, ("1.2.3.4", 9999))
    assert result is None
    print("✓ Garbage data correctly ignored")


def test_emit_no_loop():
    queue = OutputQueue()
    config = SourceConfig(source_id="test-ssdp", source_type="ssdp", settings={})
    source = SsdpSource(config, emit=queue.put)
    source.emit("status", {"msg": "test"})

    async def check():
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event.source_id == "test-ssdp"
        assert event.event_type == "status"
        print("✓ emit works (no-loop fallback)")

    asyncio.run(check())


def test_config_defaults():
    config = SourceConfig(source_id="ssdp-default", source_type="ssdp", settings={})
    source = SsdpSource(config, emit=lambda e: None)
    assert source._scan_interval == 60
    assert len(source._search_targets) >= 3
    assert "ssdp:all" in source._search_targets
    print("✓ default config values correct")


if __name__ == "__main__":
    test_parse_notify()
    test_parse_msearch_response()
    test_ignore_garbage()
    test_emit_no_loop()
    test_config_defaults()
    print()
    print("=== ALL UNIT TESTS PASSED ===")
```

**Step 2: Run test**

Run: `cd backend && uv run python discover_client/sources/test_ssdp_source.py`
Expected: `=== ALL UNIT TESTS PASSED ===`

---

### Task 4.3: Update package exports verification

Run: `cd backend && uv run python -c "from discover_client import registered_types; assert set(registered_types()) == {'mqtt', 'mdns', 'ssdp'}; print('OK')"`
Expected: `OK`
