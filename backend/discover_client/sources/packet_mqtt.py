"""Passive packet helpers for MQTT/ARP identity evidence.

The parser is intentionally small and dependency-free so pcap imports work even
when live capture dependencies such as Scapy/Npcap are not installed.
"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import struct
from pathlib import Path
from typing import Iterable


ETHERTYPE_IPV4 = 0x0800
ETHERTYPE_ARP = 0x0806
IPPROTO_TCP = 6


@dataclass(frozen=True)
class ArpObservation:
    timestamp: float
    ip: str
    mac: str
    operation: str


@dataclass(frozen=True)
class MqttConnectObservation:
    timestamp: float
    client_ip: str
    broker_ip: str
    client_port: int
    broker_port: int
    client_id: str
    keepalive_s: int
    clean_session: bool
    username_present: bool
    password_present: bool
    protocol_name: str
    protocol_level: int


@dataclass(frozen=True)
class MqttPublishObservation:
    timestamp: float
    client_ip: str
    broker_ip: str
    client_port: int
    broker_port: int
    topic: str
    qos: int
    retain: bool


PacketObservation = ArpObservation | MqttConnectObservation | MqttPublishObservation


def parse_capture_file(path: str | Path, broker_ports: Iterable[int] = (1883,)) -> list[PacketObservation]:
    data = Path(path).read_bytes()
    if data.startswith(b"\x0a\x0d\x0d\x0a"):
        return list(_parse_pcapng(data, broker_ports))
    return list(_parse_pcap(data, broker_ports))


def parse_ethernet_frame(
    frame: bytes,
    *,
    timestamp: float = 0.0,
    broker_ports: Iterable[int] = (1883,),
) -> list[PacketObservation]:
    if len(frame) < 14:
        return []
    ethertype = int.from_bytes(frame[12:14], "big")
    payload = frame[14:]
    if ethertype == ETHERTYPE_ARP:
        observation = _parse_arp(payload, timestamp)
        return [] if observation is None else [observation]
    if ethertype != ETHERTYPE_IPV4:
        return []
    return _parse_ipv4(payload, timestamp, {int(port) for port in broker_ports})


def _parse_pcap(data: bytes, broker_ports: Iterable[int]) -> Iterable[PacketObservation]:
    if len(data) < 24:
        return
    magic = data[:4]
    if magic == b"\xd4\xc3\xb2\xa1":
        endian = "<"
        scale = 1_000_000.0
    elif magic == b"\xa1\xb2\xc3\xd4":
        endian = ">"
        scale = 1_000_000.0
    elif magic == b"\x4d\x3c\xb2\xa1":
        endian = "<"
        scale = 1_000_000_000.0
    elif magic == b"\xa1\xb2\x3c\x4d":
        endian = ">"
        scale = 1_000_000_000.0
    else:
        raise ValueError("unsupported capture format; expected pcap or pcapng")
    _version_major, _version_minor, _thiszone, _sigfigs, _snaplen, network = struct.unpack(
        endian + "HHiiii",
        data[4:24],
    )
    if network != 1:
        raise ValueError(f"unsupported pcap link type {network}; only Ethernet is supported")
    offset = 24
    while offset + 16 <= len(data):
        ts_sec, ts_frac, captured_len, _original_len = struct.unpack(
            endian + "IIII",
            data[offset : offset + 16],
        )
        offset += 16
        frame = data[offset : offset + captured_len]
        offset += captured_len
        timestamp = float(ts_sec) + float(ts_frac) / scale
        yield from parse_ethernet_frame(frame, timestamp=timestamp, broker_ports=broker_ports)


def _parse_pcapng(data: bytes, broker_ports: Iterable[int]) -> Iterable[PacketObservation]:
    offset = 0
    endian = "<"
    link_types: dict[int, int] = {}
    while offset + 12 <= len(data):
        block_type_le, block_len_le = struct.unpack("<II", data[offset : offset + 8])
        if block_len_le < 12 or offset + block_len_le > len(data):
            return
        block = data[offset : offset + block_len_le]
        if block_type_le == 0x0A0D0D0A:
            byte_order_magic = block[8:12]
            if byte_order_magic == b"\x4d\x3c\x2b\x1a":
                endian = "<"
            elif byte_order_magic == b"\x1a\x2b\x3c\x4d":
                endian = ">"
            else:
                raise ValueError("invalid pcapng byte-order magic")
        else:
            block_type, block_len = struct.unpack(endian + "II", block[:8])
            if block_len != block_len_le:
                return
            if block_type == 0x00000001 and len(block) >= 20:
                link_type = struct.unpack(endian + "H", block[8:10])[0]
                link_types[len(link_types)] = link_type
            elif block_type == 0x00000006 and len(block) >= 32:
                iface_id, ts_high, ts_low, captured_len, _original_len = struct.unpack(
                    endian + "IIIII",
                    block[8:28],
                )
                if link_types.get(int(iface_id), 1) != 1:
                    offset += block_len_le
                    continue
                timestamp = float((int(ts_high) << 32) | int(ts_low)) / 1_000_000.0
                frame = block[28 : 28 + captured_len]
                yield from parse_ethernet_frame(frame, timestamp=timestamp, broker_ports=broker_ports)
        offset += block_len_le


def _parse_arp(payload: bytes, timestamp: float) -> ArpObservation | None:
    if len(payload) < 28:
        return None
    htype, ptype, hlen, plen, oper = struct.unpack("!HHBBH", payload[:8])
    if htype != 1 or ptype != ETHERTYPE_IPV4 or hlen != 6 or plen != 4:
        return None
    sender_mac = _format_mac(payload[8:14])
    sender_ip = str(ipaddress.IPv4Address(payload[14:18]))
    operation = "request" if oper == 1 else "reply" if oper == 2 else str(oper)
    return ArpObservation(timestamp=timestamp, ip=sender_ip, mac=sender_mac, operation=operation)


def _parse_ipv4(payload: bytes, timestamp: float, broker_ports: set[int]) -> list[PacketObservation]:
    if len(payload) < 20:
        return []
    version = payload[0] >> 4
    header_len = (payload[0] & 0x0F) * 4
    if version != 4 or header_len < 20 or len(payload) < header_len:
        return []
    total_len = int.from_bytes(payload[2:4], "big")
    protocol = payload[9]
    if protocol != IPPROTO_TCP:
        return []
    src_ip = str(ipaddress.IPv4Address(payload[12:16]))
    dst_ip = str(ipaddress.IPv4Address(payload[16:20]))
    tcp = payload[header_len:total_len if total_len else len(payload)]
    return _parse_tcp(tcp, timestamp, broker_ports, src_ip, dst_ip)


def _parse_tcp(
    tcp: bytes,
    timestamp: float,
    broker_ports: set[int],
    src_ip: str,
    dst_ip: str,
) -> list[PacketObservation]:
    if len(tcp) < 20:
        return []
    src_port, dst_port = struct.unpack("!HH", tcp[:4])
    header_len = ((tcp[12] >> 4) & 0x0F) * 4
    if header_len < 20 or len(tcp) < header_len:
        return []
    if dst_port not in broker_ports:
        return []
    mqtt_payload = tcp[header_len:]
    if not mqtt_payload:
        return []
    return _parse_mqtt_stream(
        mqtt_payload,
        timestamp=timestamp,
        client_ip=src_ip,
        broker_ip=dst_ip,
        client_port=src_port,
        broker_port=dst_port,
    )


def _parse_mqtt_stream(
    payload: bytes,
    *,
    timestamp: float,
    client_ip: str,
    broker_ip: str,
    client_port: int,
    broker_port: int,
) -> list[PacketObservation]:
    observations: list[PacketObservation] = []
    offset = 0
    while offset < len(payload):
        packet_type = payload[offset] >> 4
        flags = payload[offset] & 0x0F
        remaining, length_size = _decode_remaining_length(payload, offset + 1)
        if remaining is None or length_size is None:
            break
        start = offset + 1 + length_size
        end = start + remaining
        if end > len(payload):
            break
        body = payload[start:end]
        if packet_type == 1:
            item = _parse_mqtt_connect_body(
                body,
                timestamp=timestamp,
                client_ip=client_ip,
                broker_ip=broker_ip,
                client_port=client_port,
                broker_port=broker_port,
            )
            if item is not None:
                observations.append(item)
        elif packet_type == 3:
            item = _parse_mqtt_publish_body(
                body,
                flags=flags,
                timestamp=timestamp,
                client_ip=client_ip,
                broker_ip=broker_ip,
                client_port=client_port,
                broker_port=broker_port,
            )
            if item is not None:
                observations.append(item)
        offset = end
    return observations


def _parse_mqtt_connect_body(
    body: bytes,
    *,
    timestamp: float,
    client_ip: str,
    broker_ip: str,
    client_port: int,
    broker_port: int,
) -> MqttConnectObservation | None:
    protocol_name, offset = _read_mqtt_utf8(body, 0)
    if protocol_name is None or offset + 4 > len(body):
        return None
    protocol_level = body[offset]
    connect_flags = body[offset + 1]
    keepalive_s = int.from_bytes(body[offset + 2 : offset + 4], "big")
    client_id, _payload_offset = _read_mqtt_utf8(body, offset + 4)
    if client_id is None:
        return None
    return MqttConnectObservation(
        timestamp=timestamp,
        client_ip=client_ip,
        broker_ip=broker_ip,
        client_port=client_port,
        broker_port=broker_port,
        client_id=client_id,
        keepalive_s=keepalive_s,
        clean_session=bool(connect_flags & 0x02),
        username_present=bool(connect_flags & 0x80),
        password_present=bool(connect_flags & 0x40),
        protocol_name=protocol_name,
        protocol_level=int(protocol_level),
    )


def _parse_mqtt_publish_body(
    body: bytes,
    *,
    flags: int,
    timestamp: float,
    client_ip: str,
    broker_ip: str,
    client_port: int,
    broker_port: int,
) -> MqttPublishObservation | None:
    topic, _offset = _read_mqtt_utf8(body, 0)
    if not topic:
        return None
    qos = (flags >> 1) & 0x03
    return MqttPublishObservation(
        timestamp=timestamp,
        client_ip=client_ip,
        broker_ip=broker_ip,
        client_port=client_port,
        broker_port=broker_port,
        topic=topic,
        qos=qos,
        retain=bool(flags & 0x01),
    )


def _decode_remaining_length(payload: bytes, offset: int) -> tuple[int | None, int | None]:
    multiplier = 1
    value = 0
    bytes_read = 0
    while offset + bytes_read < len(payload) and bytes_read < 4:
        encoded = payload[offset + bytes_read]
        value += (encoded & 127) * multiplier
        bytes_read += 1
        if encoded & 128 == 0:
            return value, bytes_read
        multiplier *= 128
    return None, None


def _read_mqtt_utf8(payload: bytes, offset: int) -> tuple[str | None, int]:
    if offset + 2 > len(payload):
        return None, offset
    length = int.from_bytes(payload[offset : offset + 2], "big")
    start = offset + 2
    end = start + length
    if end > len(payload):
        return None, offset
    return payload[start:end].decode("utf-8", errors="replace"), end


def _format_mac(raw: bytes) -> str:
    return ":".join(f"{byte:02X}" for byte in raw[:6])
