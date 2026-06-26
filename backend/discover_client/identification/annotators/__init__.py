"""Import annotator modules so their registration side effects run."""

from discover_client.identification.annotators import mqtt
from discover_client.identification.annotators import mdns
from discover_client.identification.annotators import ssdp
from discover_client.identification.annotators import nmap
from discover_client.identification.annotators import packet_sniff

__all__ = ["mqtt", "mdns", "ssdp", "nmap", "packet_sniff"]
