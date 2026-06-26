"""mDNS annotator that extracts identification clues from mDNS discovery events."""

from __future__ import annotations

from discover_client.identification.annotator import Annotator, register_annotator
from discover_client.identification.evidence import SignalEvidence
from discover_client.source import SourceEvent
from discover_client.identification.tokens import extract_identity_tokens


@register_annotator("mdns")
class MdnsAnnotator(Annotator):
    def annotate(self, event: SourceEvent) -> SignalEvidence | None:
        if event.event_type != "discovery":
            return None

        payload = event.payload

        # Skip removal events (device left the network)
        if payload.get("removed"):
            return None

        service_type = payload.get("service_type")
        hostname = payload.get("host")  # e.g. "58044F9ADC05.local."
        properties = payload.get("properties", {})
        txt_keys = set(properties.keys()) if isinstance(properties, dict) else set()

        # Don't emit evidence if there's nothing useful
        if not service_type and not hostname:
            return None

        # Extract IP if available
        addresses = payload.get("addresses", [])
        ip_address = addresses[0] if addresses else None

        return SignalEvidence(
            source_id=event.source_id,
            source_type="mdns",
            mdns_service_type=service_type,
            mdns_txt_keys=txt_keys,
            hostname=hostname,
            ip_address=ip_address,
            identity_tokens=extract_identity_tokens(hostname, properties, payload.get("name")),
            timestamp=event.timestamp,
        )
