"""Unit tests for MdnsSource."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from zeroconf import ServiceInfo

from discover_client.output import OutputQueue
from discover_client.source import SourceConfig
from discover_client.sources.mdns_source import MdnsSource, _service_info_to_payload


class FakeAsyncZeroconf:
    def __init__(self) -> None:
        self.zeroconf = object()
        self.closed = False

    async def async_close(self) -> None:
        self.closed = True


class FakeAsyncServiceBrowser:
    def __init__(self, zeroconf, service_type, handlers) -> None:
        self.zeroconf = zeroconf
        self.service_type = service_type
        self.handlers = handlers
        self.cancelled = False

    async def async_cancel(self) -> None:
        self.cancelled = True


def test_payload_structure() -> None:
    """Verify discovery event payload format without network access."""
    info = ServiceInfo(
        type_="_mqtt._tcp.local.",
        name="mosquitto._mqtt._tcp.local.",
        addresses=[b"\xc0\xa8\x01\x64"],
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
    print("\u2713 payload structure correct")

    payload2 = _service_info_to_payload(
        "_http._tcp.local.", "printer._http._tcp.local.", None
    )
    assert payload2["host"] is None
    assert payload2["addresses"] == []
    assert payload2["port"] is None
    print("\u2713 partial payload (pre-resolve) correct")


def test_emit_thread_safety() -> None:
    """Verify emit works without an active event loop."""

    async def check() -> None:
        queue = OutputQueue()
        config = SourceConfig(source_id="test-mdns", source_type="mdns", settings={})
        source = MdnsSource(config, emit=queue.put)
        source.emit("status", {"msg": "test"})

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event.source_id == "test-mdns"
        assert event.event_type == "status"
        assert event.payload["msg"] == "test"
        print("\u2713 thread-safe emit works (no-loop fallback)")

    asyncio.run(check())


def test_start_stop_lifecycle() -> None:
    """Verify start and stop manage zeroconf resources and emit status events."""

    async def check() -> None:
        queue = OutputQueue()
        config = SourceConfig(
            source_id="test-mdns",
            source_type="mdns",
            settings={
                "scan_interval_s": 30,
                "service_types": ["_mqtt._tcp.local.", "_http._tcp.local."],
            },
        )

        with patch(
            "discover_client.sources.mdns_source.AsyncZeroconf",
            FakeAsyncZeroconf,
        ), patch(
            "discover_client.sources.mdns_source.AsyncServiceBrowser",
            FakeAsyncServiceBrowser,
        ):
            source = MdnsSource(config, emit=queue.put)
            await source.start()

            start_event = await asyncio.wait_for(queue.get(), timeout=1.0)
            assert start_event.event_type == "status"
            assert start_event.payload["msg"] == "scanning"
            assert start_event.payload["service_types"] == [
                "_mqtt._tcp.local.",
                "_http._tcp.local.",
            ]
            assert set(source._browsers.keys()) == {
                "_mqtt._tcp.local.",
                "_http._tcp.local.",
            }

            fake_zeroconf = source._zeroconf
            await source.stop()

            stop_event = await asyncio.wait_for(queue.get(), timeout=1.0)
            assert stop_event.event_type == "status"
            assert stop_event.payload["msg"] == "stopped"
            assert fake_zeroconf is not None and fake_zeroconf.closed
            print("\u2713 lifecycle start/stop works")

    asyncio.run(check())


if __name__ == "__main__":
    test_payload_structure()
    test_emit_thread_safety()
    test_start_stop_lifecycle()
    print()
    print("=== ALL UNIT TESTS PASSED ===")
