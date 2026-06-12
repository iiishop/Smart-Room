"""Import annotator modules so their registration side effects run."""

from discover_client.identification.annotators import mqtt
from discover_client.identification.annotators import nmap

__all__ = ["mqtt", "nmap"]
