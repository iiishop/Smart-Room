"""NMAP annotator that extracts identification clues from nmap scan events."""

from __future__ import annotations

from discover_client.identification.annotator import Annotator, register_annotator
from discover_client.identification.evidence import SignalEvidence
from discover_client.source import SourceEvent
from discover_client.identification.tokens import extract_identity_tokens


@register_annotator("nmap")
class NmapAnnotator(Annotator):
    def annotate(self, event: SourceEvent) -> SignalEvidence | None:
        if event.event_type != "discovery":
            return None

        payload = event.payload

        mac = payload.get("mac")
        vendor = payload.get("vendor")
        os_guess = payload.get("os_guess")
        ip = payload.get("ip")
        hostnames = payload.get("hostnames", [])

        # Skip hosts with no useful fingerprint data
        if not mac and not os_guess:
            return None

        # Extract mac_prefix (first 3 octets) for OUI-based matching
        mac_prefix = _extract_mac_prefix(mac) if mac else None

        # Enrich vendor via local IEEE OUI database
        if mac_prefix and (not vendor or vendor == "Unknown"):
            from discover_client.identification.oui import load_oui
            vendor = load_oui().get(mac_prefix, vendor)

        # Use first hostname as the primary one
        primary_hostname = hostnames[0] if hostnames else None

        return SignalEvidence(
            source_id=event.source_id,
            source_type="nmap",
            nmap_mac=mac,
            nmap_vendor=vendor,
            nmap_os_guess=os_guess,
            ip_address=ip,
            hostname=primary_hostname,
            mac_prefix=mac_prefix,
            identity_tokens=extract_identity_tokens(mac, primary_hostname),
            timestamp=event.timestamp,
        )


def _extract_mac_prefix(mac: str) -> str | None:
    """AA:BB:CC:DD:EE:FF -> AA:BB:CC"""
    parts = mac.replace("-", ":").split(":")
    if len(parts) >= 3:
        return ":".join(parts[:3]).upper()
    return None
