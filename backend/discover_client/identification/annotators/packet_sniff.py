"""Annotator for passive packet-sniffed MQTT/ARP identity evidence."""

from __future__ import annotations

import re

from discover_client.identification.annotator import Annotator, register_annotator
from discover_client.identification.evidence import SignalEvidence
from discover_client.identification.tokens import extract_identity_tokens
from discover_client.source import SourceEvent


@register_annotator("packet_sniff")
class PacketSniffAnnotator(Annotator):
    def annotate(self, event: SourceEvent) -> SignalEvidence | None:
        if event.event_type != "discovery":
            return None
        payload = event.payload
        kind = str(payload.get("kind") or "")
        ip = str(payload.get("ip") or "").strip() or None
        mac = str(payload.get("mac") or "").strip() or None
        client_id = str(payload.get("client_id") or "").strip() or None

        if kind == "arp":
            if not ip or not mac:
                return None
            return SignalEvidence(
                source_id=event.source_id,
                source_type=event.source_type,
                ip_address=ip,
                nmap_mac=mac,
                mac_prefix=_extract_mac_prefix(mac),
                identity_tokens=extract_identity_tokens(mac),
                timestamp=event.timestamp,
                event_type=event.event_type,
            )

        if kind == "mqtt_connect":
            if not ip and not client_id:
                return None
            return SignalEvidence(
                source_id=event.source_id,
                source_type=event.source_type,
                ip_address=ip,
                nmap_mac=mac,
                mac_prefix=_extract_mac_prefix(mac),
                mqtt_client_id=client_id,
                identity_tokens=_packet_identity_tokens(client_id=client_id, mac=mac),
                timestamp=event.timestamp,
                event_type=event.event_type,
            )

        if kind == "mqtt_publish":
            topic = str(payload.get("topic") or "").strip()
            if not topic:
                return None
            return SignalEvidence(
                source_id=event.source_id,
                source_type=event.source_type,
                mqtt_topic=topic,
                topic_prefix=_topic_prefix(topic),
                mqtt_client_id=client_id,
                ip_address=ip,
                nmap_mac=mac,
                mac_prefix=_extract_mac_prefix(mac),
                identity_tokens=_packet_identity_tokens(client_id=client_id, mac=mac, topic=topic),
                timestamp=event.timestamp,
                event_type=event.event_type,
            )
        return None


def _packet_identity_tokens(
    *,
    client_id: str | None = None,
    mac: str | None = None,
    topic: str | None = None,
) -> set[str]:
    tokens = extract_identity_tokens({"client_id": client_id}, {"mac": mac}, topic)
    normalized_mac = _normalize_token(mac)
    if _looks_like_mac(normalized_mac):
        tokens.add(normalized_mac)
    normalized_client = _normalize_token(client_id)
    if len(normalized_client) >= 3:
        tokens.add("mqttclient" + normalized_client)
    return tokens


def _topic_prefix(topic: str) -> str | None:
    prefix, separator, _leaf = str(topic or "").strip().rpartition("/")
    if not separator:
        return None
    return prefix


def _extract_mac_prefix(mac: str | None) -> str | None:
    if not mac:
        return None
    parts = str(mac).replace("-", ":").split(":")
    if len(parts) >= 3:
        return ":".join(part.zfill(2) for part in parts[:3]).upper()
    return None


def _normalize_token(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _looks_like_mac(value: str) -> bool:
    return len(value) == 12 and all(char in "0123456789abcdef" for char in value)
