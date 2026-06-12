"""SSDP annotator that extracts identification clues from SSDP/UPnP discovery events."""

from __future__ import annotations

from discover_client.identification.annotator import Annotator, register_annotator
from discover_client.identification.evidence import SignalEvidence
from discover_client.source import SourceEvent


@register_annotator("ssdp")
class SsdpAnnotator(Annotator):
    def annotate(self, event: SourceEvent) -> SignalEvidence | None:
        if event.event_type != "discovery":
            return None

        payload = event.payload

        usn = payload.get("usn", "")
        server = payload.get("server", "")
        ip_address = payload.get("host")  # source IP from datagram
        location = payload.get("location", "")  # description URL

        # Skip if no useful identifiers
        if not usn and not server:
            return None

        # Try to extract hostname from USN or Location URL
        hostname = _extract_hostname(location) or _extract_hostname_from_usn(usn)

        return SignalEvidence(
            source_id=event.source_id,
            source_type="ssdp",
            ssdp_usn=usn,
            ssdp_server=server,
            ip_address=ip_address,
            hostname=hostname,
            timestamp=event.timestamp,
        )


def _extract_hostname(url: str) -> str | None:
    """Extract hostname from an HTTP Location URL."""
    if not url.lower().startswith("http"):
        return None
    # http://192.168.5.2:8080/desc.xml -> 192.168.5.2
    try:
        stripped = url.split("://", 1)[1]
        host_port = stripped.split("/", 1)[0]
        return host_port.split(":")[0]
    except (IndexError, ValueError):
        return None


def _extract_hostname_from_usn(usn: str) -> str | None:
    """Best-effort hostname extraction from USN."""
    # uuid:device-name::upnp:rootdevice -> device-name
    if "::" not in usn:
        return None
    try:
        after_uuid = usn.split(":", 1)[1]  # drop "uuid:"
        device_part = after_uuid.split("::", 1)[0]
        if device_part and device_part != after_uuid:
            return device_part
    except (IndexError, ValueError):
        pass
    return None
