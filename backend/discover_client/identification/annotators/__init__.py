"""Import annotator modules so their registration side effects run."""

from discover_client.identification.annotators import mqtt

__all__ = ["mqtt"]
