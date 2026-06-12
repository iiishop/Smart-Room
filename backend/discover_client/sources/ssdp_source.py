"""SSDP / UPnP device discovery source."""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
from email.parser import BytesHeaderParser
from typing import Any, Callable

from discover_client.source import Source, SourceConfig

logger = logging.getLogger(__name__)

SSDP_HOST = "239.255.255.250"
SSDP_PORT = 1900
SSDP_ADDR = (SSDP_HOST, SSDP_PORT)

SEARCH_TARGETS = [
    "ssdp:all",
    "upnp:rootdevice",
    "urn:schemas-upnp-org:device:MediaRenderer:1",
    "urn:schemas-upnp-org:device:MediaServer:1",
    "urn:schemas-upnp-org:device:InternetGatewayDevice:1",
    "urn:schemas-upnp-org:service:WANIPConnection:1",
]


def _build_msearch(search_target: str) -> bytes:
    return (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {SSDP_HOST}:{SSDP_PORT}\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 2\r\n"
        f"ST: {search_target}\r\n"
        "\r\n"
    ).encode("ascii")


def _parse_ssdp_message(data: bytes, addr: tuple[str, int]) -> dict[str, Any] | None:
    """Parse an SSDP NOTIFY or M-SEARCH response into a discovery payload."""
    header_block = data.split(b"\r\n\r\n", 1)[0]
    lines = header_block.split(b"\r\n")
    if not lines:
        return None

    start_line = lines[0]
    if not start_line.startswith(b"HTTP/") and not start_line.startswith(b"NOTIFY "):
        return None

    try:
        headers = BytesHeaderParser().parsebytes(b"\r\n".join(lines[1:]) + b"\r\n\r\n")
    except Exception:
        return None

    return {
        "host": addr[0],
        "port": addr[1],
        "location": headers.get("LOCATION", ""),
        "server": headers.get("SERVER", ""),
        "st": headers.get("ST", headers.get("NT", "")),
        "usn": headers.get("USN", ""),
        "nt": headers.get("NT", ""),
        "nts": headers.get("NTS", ""),
    }


def _create_ssdp_socket() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        sock.bind(("", SSDP_PORT))
    except OSError:
        sock.close()
        raise

    membership = struct.pack("=4s4s", socket.inet_aton(SSDP_HOST), socket.inet_aton("0.0.0.0"))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.setblocking(False)
    return sock


class SsdpSource(Source):
    """Discovers UPnP/SSDP devices on the local network."""

    def __init__(self, config: SourceConfig, emit: Callable) -> None:
        super().__init__(config, emit)
        self._scan_interval = int(config.settings.get("scan_interval_s", 60))
        self._search_targets = list(config.settings.get("search_targets", SEARCH_TARGETS))
        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: _SsdpProtocol | None = None
        self._running = False
        self._sweep_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._running = True
        loop = asyncio.get_running_loop()

        try:
            sock = _create_ssdp_socket()
            transport, protocol = await loop.create_datagram_endpoint(
                lambda: _SsdpProtocol(self._on_datagram),
                sock=sock,
            )
        except Exception as exc:
            self._running = False
            self.emit("error", {"msg": f"Failed to create SSDP socket: {exc}"})
            return

        self._transport = transport
        self._protocol = protocol
        self.emit("status", {"msg": "scanning"})
        await self._send_msearch()
        self._sweep_task = asyncio.create_task(self._periodic_sweep())

    async def stop(self) -> None:
        self._running = False

        if self._sweep_task is not None:
            self._sweep_task.cancel()
            try:
                await self._sweep_task
            except asyncio.CancelledError:
                pass
            self._sweep_task = None

        if self._transport is not None:
            self._transport.close()
            self._transport = None

        self._protocol = None
        self.emit("status", {"msg": "stopped"})

    async def _send_msearch(self) -> None:
        if self._transport is None:
            return

        for search_target in self._search_targets:
            self._transport.sendto(_build_msearch(search_target), SSDP_ADDR)

    async def _periodic_sweep(self) -> None:
        while self._running:
            await asyncio.sleep(self._scan_interval)
            if not self._running:
                break

            try:
                await self._send_msearch()
            except Exception as exc:
                self.emit("error", {"msg": f"Sweep error: {exc}"})

    def _on_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        payload = _parse_ssdp_message(data, addr)
        if payload is not None:
            self.emit("discovery", payload)


class _SsdpProtocol(asyncio.DatagramProtocol):
    """asyncio UDP protocol for SSDP traffic."""

    def __init__(self, callback: Callable[[bytes, tuple[str, int]], None]) -> None:
        super().__init__()
        self._callback = callback

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._callback(data, addr)

    def error_received(self, exc: Exception) -> None:
        logger.warning("SSDP socket error: %s", exc)

    def connection_lost(self, exc: Exception | None) -> None:
        if exc is not None:
            logger.warning("SSDP connection lost: %s", exc)
