from __future__ import annotations

import asyncio
import struct
from pathlib import Path

from discover_client.output import OutputQueue
from discover_client.source import SourceConfig
from discover_client.sources.packet_mqtt import (
    ArpObservation,
    MqttConnectObservation,
    MqttPublishObservation,
    parse_capture_file,
    parse_ethernet_frame,
)
from discover_client.sources.packet_sniff_source import PacketSniffSource


CLIENT_MAC = bytes.fromhex("AABBCCDDEEFF")
BROKER_MAC = bytes.fromhex("112233445566")
CLIENT_IP = "192.168.1.44"
BROKER_IP = "192.168.1.10"


def test_parse_mqtt_connect_publish_and_arp_frames() -> None:
    frames = [
        _arp_frame(CLIENT_MAC, CLIENT_IP),
        _mqtt_tcp_frame(
            _mqtt_connect_payload("sensor-client-1"),
            src_port=49152,
            dst_port=1883,
        ),
        _mqtt_tcp_frame(
            _mqtt_publish_payload("lab/sensor-1/temperature", b"21.5"),
            src_port=49152,
            dst_port=1883,
        ),
    ]

    observations = [
        observation
        for frame in frames
        for observation in parse_ethernet_frame(frame, timestamp=1.0, broker_ports=[1883])
    ]

    assert isinstance(observations[0], ArpObservation)
    assert observations[0].ip == CLIENT_IP
    assert observations[0].mac == "AA:BB:CC:DD:EE:FF"
    assert isinstance(observations[1], MqttConnectObservation)
    assert observations[1].client_id == "sensor-client-1"
    assert observations[1].client_ip == CLIENT_IP
    assert observations[1].keepalive_s == 60
    assert isinstance(observations[2], MqttPublishObservation)
    assert observations[2].topic == "lab/sensor-1/temperature"


def test_parse_capture_file_reads_classic_pcap(tmp_path: Path) -> None:
    path = tmp_path / "mqtt.pcap"
    _write_pcap(
        path,
        [
            _arp_frame(CLIENT_MAC, CLIENT_IP),
            _mqtt_tcp_frame(_mqtt_connect_payload("sensor-client-1")),
        ],
    )

    observations = parse_capture_file(path, broker_ports=[1883])

    assert [type(item) for item in observations] == [ArpObservation, MqttConnectObservation]


def test_packet_sniff_source_emits_pcap_observations(tmp_path: Path) -> None:
    async def check() -> None:
        path = tmp_path / "mqtt.pcap"
        _write_pcap(
            path,
            [
                _arp_frame(CLIENT_MAC, CLIENT_IP),
                _mqtt_tcp_frame(_mqtt_connect_payload("sensor-client-1")),
                _mqtt_tcp_frame(_mqtt_publish_payload("lab/sensor-1/temperature", b"21.5")),
            ],
        )
        queue = OutputQueue()
        source = PacketSniffSource(
            SourceConfig(
                source_id="sniff-lab",
                source_type="packet_sniff",
                settings={"pcap_path": str(path), "broker_ports": [1883]},
            ),
            emit=queue.put,
        )

        await source.start()
        events = [await asyncio.wait_for(queue.get(), timeout=1.0) for _ in range(4)]

        discovery = [event for event in events if event.event_type == "discovery"]
        assert [event.payload["kind"] for event in discovery] == [
            "arp",
            "mqtt_connect",
            "mqtt_publish",
        ]
        assert discovery[1].payload["client_id"] == "sensor-client-1"
        assert discovery[1].payload["mac"] == "AA:BB:CC:DD:EE:FF"
        assert discovery[2].payload["topic"] == "lab/sensor-1/temperature"

    asyncio.run(check())


def _write_pcap(path: Path, frames: list[bytes]) -> None:
    body = bytearray()
    body.extend(b"\xd4\xc3\xb2\xa1")
    body.extend(struct.pack("<HHiiii", 2, 4, 0, 0, 65535, 1))
    for index, frame in enumerate(frames, start=1):
        body.extend(struct.pack("<IIII", index, 0, len(frame), len(frame)))
        body.extend(frame)
    path.write_bytes(bytes(body))


def _arp_frame(sender_mac: bytes, sender_ip: str) -> bytes:
    target_mac = b"\x00" * 6
    target_ip = b"\x00" * 4
    payload = struct.pack("!HHBBH", 1, 0x0800, 6, 4, 1)
    payload += sender_mac
    payload += _ip_bytes(sender_ip)
    payload += target_mac
    payload += target_ip
    return BROKER_MAC + sender_mac + struct.pack("!H", 0x0806) + payload


def _mqtt_tcp_frame(payload: bytes, src_port: int = 49152, dst_port: int = 1883) -> bytes:
    tcp = struct.pack("!HHIIHHHH", src_port, dst_port, 1, 0, 0x5018, 8192, 0, 0) + payload
    ip_total = 20 + len(tcp)
    ip_header = struct.pack(
        "!BBHHHBBH4s4s",
        0x45,
        0,
        ip_total,
        1,
        0,
        64,
        6,
        0,
        _ip_bytes(CLIENT_IP),
        _ip_bytes(BROKER_IP),
    )
    return BROKER_MAC + CLIENT_MAC + struct.pack("!H", 0x0800) + ip_header + tcp


def _mqtt_connect_payload(client_id: str) -> bytes:
    body = _mqtt_string("MQTT")
    body += b"\x04"
    body += b"\x02"
    body += struct.pack("!H", 60)
    body += _mqtt_string(client_id)
    return b"\x10" + _remaining_length(len(body)) + body


def _mqtt_publish_payload(topic: str, payload: bytes) -> bytes:
    body = _mqtt_string(topic) + payload
    return b"\x30" + _remaining_length(len(body)) + body


def _mqtt_string(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return struct.pack("!H", len(encoded)) + encoded


def _remaining_length(value: int) -> bytes:
    result = bytearray()
    while True:
        encoded = value % 128
        value //= 128
        if value:
            encoded |= 128
        result.append(encoded)
        if not value:
            return bytes(result)


def _ip_bytes(value: str) -> bytes:
    return bytes(int(part) for part in value.split("."))
