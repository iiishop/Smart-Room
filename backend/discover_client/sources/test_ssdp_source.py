"""Unit tests for SSDP response parsing and SsdpSource lifecycle."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from discover_client.output import OutputQueue
from discover_client.source import SourceConfig
from discover_client.sources.ssdp_source import SsdpSource, _parse_ssdp_message


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


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.closed = False

    def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
        self.sent.append((data, addr))

    def close(self) -> None:
        self.closed = True


class FakeLoop:
    def __init__(self) -> None:
        self.transport = FakeTransport()

    async def create_datagram_endpoint(self, factory, sock=None):
        _ = sock
        protocol = factory()
        return self.transport, protocol


def test_parse_notify() -> None:
    result = _parse_ssdp_message(SAMPLE_NOTIFY, ("192.168.1.1", 1900))
    assert result is not None
    assert result["host"] == "192.168.1.1"
    assert result["location"] == "http://192.168.1.1:5000/rootDesc.xml"
    assert result["nt"] == "upnp:rootdevice"
    assert result["nts"] == "ssdp:alive"
    assert result["usn"] == "uuid:1234-5678::upnp:rootdevice"
    assert result["server"] == "Linux/3.14 UPnP/1.0 MyDevice/1.0"
    print("OK notify")


def test_parse_msearch_response() -> None:
    result = _parse_ssdp_message(SAMPLE_MSEARCH_RESPONSE, ("192.168.1.50", 49152))
    assert result is not None
    assert result["st"] == "urn:schemas-upnp-org:device:MediaRenderer:1"
    assert result["location"] == "http://192.168.1.50:49152/description.xml"
    print("OK response")


def test_ignore_garbage() -> None:
    result = _parse_ssdp_message(GARBAGE, ("1.2.3.4", 9999))
    assert result is None
    print("OK garbage")


def test_emit_no_loop() -> None:
    async def check() -> None:
        queue = OutputQueue()
        config = SourceConfig(source_id="test-ssdp", source_type="ssdp", settings={})
        source = SsdpSource(config, emit=queue.put)
        source.emit("status", {"msg": "test"})

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event.source_id == "test-ssdp"
        assert event.event_type == "status"
        print("OK emit")

    asyncio.run(check())


def test_config_defaults() -> None:
    config = SourceConfig(source_id="ssdp-default", source_type="ssdp", settings={})
    source = SsdpSource(config, emit=lambda event: None)
    assert source._scan_interval == 60
    assert len(source._search_targets) >= 3
    assert "ssdp:all" in source._search_targets
    print("OK defaults")


def test_start_stop_lifecycle() -> None:
    async def check() -> None:
        queue = OutputQueue()
        config = SourceConfig(
            source_id="test-ssdp",
            source_type="ssdp",
            settings={"scan_interval_s": 60, "search_targets": ["ssdp:all"]},
        )
        fake_loop = FakeLoop()

        with patch(
            "discover_client.sources.ssdp_source.asyncio.get_running_loop",
            return_value=fake_loop,
        ), patch(
            "discover_client.sources.ssdp_source._create_ssdp_socket",
            return_value=object(),
        ):
            source = SsdpSource(config, emit=queue.put)
            await source.start()

            start_event = await asyncio.wait_for(queue.get(), timeout=1.0)
            assert start_event.event_type == "status"
            assert start_event.payload["msg"] == "scanning"
            assert fake_loop.transport.sent

            await source.stop()

            stop_event = await asyncio.wait_for(queue.get(), timeout=1.0)
            assert stop_event.event_type == "status"
            assert stop_event.payload["msg"] == "stopped"
            assert fake_loop.transport.closed
            print("OK lifecycle")

    asyncio.run(check())


if __name__ == "__main__":
    test_parse_notify()
    test_parse_msearch_response()
    test_ignore_garbage()
    test_emit_no_loop()
    test_config_defaults()
    test_start_stop_lifecycle()
    print()
    print("=== ALL UNIT TESTS PASSED ===")
