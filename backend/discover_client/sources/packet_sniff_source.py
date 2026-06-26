"""Passive MQTT/ARP packet source.

This source emits discovery evidence from classic pcap/pcapng files or,
optionally, a live Scapy/Npcap capture. It does not publish MQTT messages and
does not classify data/operations; it only adds identity evidence.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

from discover_client.source import Source, SourceConfig, SourceEvent
from discover_client.sources.packet_mqtt import (
    ArpObservation,
    MqttConnectObservation,
    MqttPublishObservation,
    PacketObservation,
    parse_capture_file,
    parse_ethernet_frame,
)


class PacketSniffSource(Source):
    def __init__(self, config: SourceConfig, emit) -> None:
        super().__init__(config, emit)
        settings = config.settings
        self._pcap_path = str(settings.get("pcap_path") or "").strip()
        self._interface = str(settings.get("interface") or "").strip() or None
        self._live = bool(settings.get("live", False))
        self._broker_ports = _int_list(settings.get("broker_ports") or [1883]) or [1883]
        self._capture_filter = str(settings.get("capture_filter") or "").strip()
        self._emit_publish_topics = bool(settings.get("emit_publish_topics", True))
        self._max_packets = int(settings.get("max_packets") or 0)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event = threading.Event()
        self._live_task: asyncio.Task | None = None
        self._ip_to_mac: dict[str, str] = {}
        self._ip_to_client_id: dict[str, str] = {}

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_event.clear()
        if self._pcap_path:
            await self._load_pcap_once(Path(self._pcap_path))
        if self._live:
            self._live_task = asyncio.create_task(self._run_live())
        if not self._pcap_path and not self._live:
            self.emit("status", {"msg": "disabled", "reason": "no pcap_path and live=false"})

    async def stop(self) -> None:
        self._stop_event.set()
        if self._live_task is not None:
            self._live_task.cancel()
            try:
                await self._live_task
            except asyncio.CancelledError:
                pass
            self._live_task = None
        self.emit("status", {"msg": "stopped"})

    async def _load_pcap_once(self, path: Path) -> None:
        loop = asyncio.get_running_loop()
        try:
            observations = await loop.run_in_executor(
                None,
                lambda: parse_capture_file(path, self._broker_ports),
            )
        except Exception as exc:
            self.emit("error", {"msg": f"pcap parse failed: {exc}", "path": str(path)})
            return

        count = 0
        for observation in observations:
            count += self._emit_observation(observation)
        self.emit("status", {"msg": "pcap_loaded", "path": str(path), "observations": count})

    async def _run_live(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._sniff_live_blocking)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.emit("error", {"msg": f"live sniff failed: {exc}"})

    def _sniff_live_blocking(self) -> None:
        try:
            from scapy.all import sniff  # type: ignore
        except Exception as exc:
            raise RuntimeError("live packet sniffing requires scapy and Npcap/libpcap") from exc

        capture_filter = self._capture_filter or _default_capture_filter(self._broker_ports)

        def on_packet(packet: Any) -> None:
            if self._stop_event.is_set():
                return
            try:
                frame = bytes(packet)
            except Exception:
                return
            timestamp = float(getattr(packet, "time", 0.0) or 0.0)
            for observation in parse_ethernet_frame(
                frame,
                timestamp=timestamp,
                broker_ports=self._broker_ports,
            ):
                self._emit_observation(observation)

        self.emit(
            "status",
            {
                "msg": "sniffing",
                "interface": self._interface or "default",
                "filter": capture_filter,
            },
        )
        sniff(
            iface=self._interface,
            filter=capture_filter,
            prn=on_packet,
            store=False,
            stop_filter=lambda _packet: self._stop_event.is_set(),
            count=max(0, self._max_packets),
        )

    def _emit_observation(self, observation: PacketObservation) -> int:
        if isinstance(observation, ArpObservation):
            self._ip_to_mac[observation.ip] = observation.mac
            self._emit_with_timestamp(
                observation.timestamp,
                "discovery",
                {
                    "kind": "arp",
                    "ip": observation.ip,
                    "mac": observation.mac,
                    "operation": observation.operation,
                },
            )
            return 1

        if isinstance(observation, MqttConnectObservation):
            self._ip_to_client_id[observation.client_ip] = observation.client_id
            mac = self._ip_to_mac.get(observation.client_ip, "")
            self._emit_with_timestamp(
                observation.timestamp,
                "discovery",
                {
                    "kind": "mqtt_connect",
                    "client_id": observation.client_id,
                    "ip": observation.client_ip,
                    "mac": mac,
                    "broker_ip": observation.broker_ip,
                    "client_port": observation.client_port,
                    "broker_port": observation.broker_port,
                    "keepalive_s": observation.keepalive_s,
                    "clean_session": observation.clean_session,
                    "username_present": observation.username_present,
                    "password_present": observation.password_present,
                    "protocol_name": observation.protocol_name,
                    "protocol_level": observation.protocol_level,
                },
            )
            return 1

        if isinstance(observation, MqttPublishObservation):
            if not self._emit_publish_topics:
                return 0
            mac = self._ip_to_mac.get(observation.client_ip, "")
            client_id = self._ip_to_client_id.get(observation.client_ip, "")
            self._emit_with_timestamp(
                observation.timestamp,
                "discovery",
                {
                    "kind": "mqtt_publish",
                    "client_id": client_id,
                    "ip": observation.client_ip,
                    "mac": mac,
                    "broker_ip": observation.broker_ip,
                    "client_port": observation.client_port,
                    "broker_port": observation.broker_port,
                    "topic": observation.topic,
                    "qos": observation.qos,
                    "retain": observation.retain,
                },
            )
            return 1
        return 0

    def _emit_with_timestamp(self, timestamp: float, event_type: str, payload: dict[str, Any]) -> None:
        event = SourceEvent(
            source_id=self.source_id,
            source_type=self.source_type,
            timestamp=timestamp,
            event_type=event_type,
            payload=payload,
        )
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._emit, event)
        else:
            self._emit(event)


def _default_capture_filter(ports: list[int]) -> str:
    port_filter = " or ".join(f"tcp port {int(port)}" for port in ports)
    return f"arp or ({port_filter})" if port_filter else "arp or tcp port 1883"


def _int_list(value: object) -> list[int]:
    if isinstance(value, int):
        return [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[int] = []
    for item in value:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            pass
    return result
