"""Device identification primitives and annotator registry."""

from discover_client.identification.annotator import ANNOTATORS, Annotator, register_annotator
from discover_client.identification.device import Device, DeviceHypothesis
from discover_client.identification.evidence import SignalEvidence
from discover_client.identification.fingerprint import FINGERPRINTS, DeviceFingerprint

__all__ = [
    "ANNOTATORS",
    "Annotator",
    "register_annotator",
    "Device",
    "DeviceHypothesis",
    "SignalEvidence",
    "FINGERPRINTS",
    "DeviceFingerprint",
]
