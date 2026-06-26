"""Merge per-event evidence into tracked devices."""

from __future__ import annotations

from ipaddress import ip_address

from discover_client.identification.device import Device
from discover_client.identification.evidence import SignalEvidence


class Deduplicator:
    def __init__(self) -> None:
        self._devices: list[Device] = []
        self._next_id = 1

    def ingest(self, evidence: SignalEvidence) -> Device:
        for device in self._devices:
            score, reason = self._match(device, evidence)
            if score >= 50:
                self._merge(device, evidence)
                explanation = f"evidence merged at score {score}: {reason}"
                if reason and explanation not in device.identity_reasons:
                    device.identity_reasons.append(explanation)
                return device

        device = Device(device_id=f"device-{self._next_id}")
        self._next_id += 1
        self._merge(device, evidence)
        self._devices.append(device)
        return device

    def get_devices(self) -> list[Device]:
        return sorted(self._devices, key=lambda device: device.last_seen, reverse=True)

    def _match_score(self, device: Device, evidence: SignalEvidence) -> int:
        return self._match(device, evidence)[0]

    def _match(self, device: Device, evidence: SignalEvidence) -> tuple[int, str]:
        if evidence.nmap_mac and evidence.nmap_mac in device.mac_addresses:
            return 100, "exact MAC address"
        if evidence.identity_tokens and device.identity_tokens.intersection(evidence.identity_tokens):
            shared = sorted(device.identity_tokens.intersection(evidence.identity_tokens))
            return 88, "shared high-specificity identifier " + ", ".join(shared[:3])
        mqtt_client_identity = (
            f"{evidence.source_id}|{evidence.mqtt_client_id.strip()}"
            if evidence.mqtt_client_id and evidence.mqtt_client_id.strip()
            else ""
        )
        if mqtt_client_identity and mqtt_client_identity in device.mqtt_client_ids:
            return 75, "same source-scoped MQTT client id"
        if self._mac_prefix_hostname_ip_match(device, evidence):
            return 95, "hostname embeds the observed MAC prefix on the same IP"
        if evidence.ip_address and evidence.mdns_service_type:
            if evidence.ip_address in device.ip_addresses and evidence.mdns_service_type in device.service_types:
                return 70, "same IP address and mDNS service type"
        # Topic identities are scoped to their source/broker.
        mqtt_identity = (
            f"{evidence.source_id}|{evidence.topic_prefix}"
            if evidence.source_type == "mqtt" and evidence.topic_prefix
            else ""
        )
        if mqtt_identity and mqtt_identity in device.mqtt_identities:
            return 60, "same source-scoped MQTT entity identity"
        if (
            evidence.source_type == "packet_sniff"
            and evidence.topic_prefix
            and evidence.topic_prefix in device.topic_prefixes
        ):
            return 58, "same observed MQTT topic prefix"
        if evidence.ip_address and evidence.ip_address in device.ip_addresses:
            return 50, "same currently observed IP address"
        if self._hostname_prefix_subnet_match(device, evidence):
            return 30, "weak hostname-prefix and subnet similarity"
        return 0, "no shared stable or connection identity"

    def _merge(self, device: Device, evidence: SignalEvidence) -> None:
        device.total_evidence_count += 1
        device.last_seen = max(device.last_seen, evidence.timestamp)
        device.source_ids.add(evidence.source_id)

        if evidence.ip_address:
            device.ip_addresses.add(evidence.ip_address)
        if evidence.hostname:
            device.hostnames.add(evidence.hostname)
        if evidence.nmap_mac:
            device.mac_addresses.add(evidence.nmap_mac.upper().replace("-", ":"))
        if evidence.mac_prefix:
            device.mac_prefixes.add(evidence.mac_prefix.upper())
        if evidence.nmap_vendor and not device.vendor:
            device.vendor = evidence.nmap_vendor
        if evidence.nmap_os_guess and not device.os_guess:
            device.os_guess = evidence.nmap_os_guess
        if evidence.mdns_service_type:
            device.service_types.add(evidence.mdns_service_type)
        if evidence.ssdp_usn:
            device.ssdp_usns.add(evidence.ssdp_usn.strip())
        if evidence.mqtt_payload_keys:
            device.payload_keys.update(evidence.mqtt_payload_keys)
        if evidence.mqtt_client_id and evidence.mqtt_client_id.strip():
            device.mqtt_client_ids.add(f"{evidence.source_id}|{evidence.mqtt_client_id.strip()}")
        if evidence.topic_prefix:
            device.topic_prefixes.add(evidence.topic_prefix)
            if evidence.source_type == "mqtt":
                device.mqtt_identities.add(f"{evidence.source_id}|{evidence.topic_prefix}")
        if evidence.identity_tokens:
            device.identity_tokens.update(evidence.identity_tokens)

    def _mac_prefix_hostname_ip_match(self, device: Device, evidence: SignalEvidence) -> bool:
        if evidence.ip_address and evidence.hostname:
            hostname_prefix = _hostname_mac_prefix(evidence.hostname)
            if hostname_prefix and evidence.ip_address in device.ip_addresses:
                for mac_prefix in device.mac_prefixes:
                    if hostname_prefix == mac_prefix.replace(":", ""):
                        return True

        if evidence.ip_address and evidence.mac_prefix:
            normalized_prefix = evidence.mac_prefix.upper().replace(":", "")
            if evidence.ip_address in device.ip_addresses:
                for hostname in device.hostnames:
                    if _hostname_mac_prefix(hostname) == normalized_prefix:
                        return True

        return False

    def _hostname_prefix_subnet_match(self, device: Device, evidence: SignalEvidence) -> bool:
        if not evidence.hostname or not evidence.ip_address:
            return False

        evidence_prefix = _hostname_prefix(evidence.hostname)
        if len(evidence_prefix) < 3:
            return False

        for hostname in device.hostnames:
            device_prefix = _hostname_prefix(hostname)
            if _common_prefix_len(evidence_prefix, device_prefix) < 3:
                continue
            for known_ip in device.ip_addresses:
                if _same_subnet(known_ip, evidence.ip_address):
                    return True
        return False


def _hostname_prefix(hostname: str) -> str:
    return hostname.split(".", 1)[0].strip().lower()


def _hostname_mac_prefix(hostname: str) -> str | None:
    prefix = "".join(ch for ch in _hostname_prefix(hostname) if ch.isalnum()).upper()
    if len(prefix) < 6:
        return None
    candidate = prefix[:6]
    return candidate if all(ch in "0123456789ABCDEF" for ch in candidate) else None


def _common_prefix_len(left: str, right: str) -> int:
    count = 0
    for left_char, right_char in zip(left, right):
        if left_char != right_char:
            break
        count += 1
    return count


def _same_subnet(left: str, right: str) -> bool:
    try:
        left_ip = ip_address(left)
        right_ip = ip_address(right)
    except ValueError:
        return False

    if left_ip.version != right_ip.version:
        return False
    if left_ip.version == 4:
        return str(left_ip).split(".")[:3] == str(right_ip).split(".")[:3]
    return left_ip.packed[:8] == right_ip.packed[:8]
