# Task 01 — Device Identifier Core

## Scope
实现 Device Identifier 的核心数据结构和第一个标注器（MQTT），跑通 `event → evidence → fingerprint match` 全流程。

## Files to create
- `discover_client/identification/__init__.py`
- `discover_client/identification/fingerprint.py` — DeviceFingerprint + FINGERPRINTS registry
- `discover_client/identification/evidence.py` — SignalEvidence
- `discover_client/identification/device.py` — DeviceHypothesis, Device
- `discover_client/identification/annotator.py` — Annotator ABC + ANNOTATORS registry + register_annotator
- `discover_client/identification/annotators/__init__.py` — import all → trigger @register_annotator
- `discover_client/identification/annotators/mqtt.py` — MqttAnnotator

## Task 1.1 — Data structures (fingerprint.py, evidence.py, device.py)

Create the three core dataclasses:

### fingerprint.py
```python
from dataclasses import dataclass, field

@dataclass
class DeviceFingerprint:
    device_type: str          # "Govee H5179"
    category: str             # "temp_sensor", "smart_plug", "light", "gateway", "unknown"
    mqtt_topic_patterns: list[str] = field(default_factory=list)
    mqtt_payload_keys: set[str] = field(default_factory=set)
    mdns_service_types: list[str] = field(default_factory=list)
    mdns_txt_keys: set[str] = field(default_factory=set)
    ssdp_usn_patterns: list[str] = field(default_factory=list)
    ssdp_server_patterns: list[str] = field(default_factory=list)
    nmap_mac_prefixes: list[str] = field(default_factory=list)
    nmap_os_guesses: list[str] = field(default_factory=list)
    hostname_pattern: str | None = None

# Initial fingerprint registry — at least Govee H5179 + Unknown Device
FINGERPRINTS: list[DeviceFingerprint] = [...]
```

The Govee H5179 fingerprint should have:
- device_type="Govee H5179", category="temp_sensor"
- mqtt_topic_patterns=["govee/+/state"]
- mqtt_payload_keys={"temp", "humidity", "battery"}
- mdns_service_types=["_matter._tcp.local."]
- mdns_txt_keys={"SII", "SAI"}
- hostname_pattern="govee-*"
- nmap_mac_prefixes=["AA:BB:CC"], nmap_os_guesses=["Linux*embedded*"]

Also include a catch-all Unknown Device fingerprint with all fields empty/default.

### evidence.py
```python
from dataclasses import dataclass

@dataclass
class SignalEvidence:
    source_id: str
    source_type: str  # "mqtt", "mdns", "ssdp", "nmap"
    # Identification clues
    mqtt_topic: str | None = None
    mqtt_payload_keys: set[str] | None = None
    mdns_service_type: str | None = None
    mdns_txt_keys: set[str] | None = None
    ssdp_usn: str | None = None
    ssdp_server: str | None = None
    nmap_mac: str | None = None
    nmap_vendor: str | None = None
    nmap_os_guess: str | None = None
    # Deduplication clues
    ip_address: str | None = None
    hostname: str | None = None
    mac_prefix: str | None = None
    # Timestamp
    timestamp: float = 0.0
```

### device.py
```python
from dataclasses import dataclass, field

@dataclass
class DeviceHypothesis:
    fingerprint: DeviceFingerprint
    probability: float = 0.0

@dataclass
class Device:
    device_id: str
    hypotheses: list[DeviceHypothesis] = field(default_factory=list)
    total_evidence_count: int = 0
    last_seen: float = 0.0
    ip_addresses: set[str] = field(default_factory=set)
    hostnames: set[str] = field(default_factory=set)
    mac_prefixes: set[str] = field(default_factory=set)
```

## Task 1.2 — Annotator ABC + registry (annotator.py)

```python
from abc import ABC, abstractmethod
from discover_client.source import SourceEvent
from discover_client.identification.evidence import SignalEvidence

ANNOTATORS: dict[str, type["Annotator"]] = {}

def register_annotator(source_type: str):
    def wrapper(cls):
        ANNOTATORS[source_type] = cls
        cls.source_type = source_type
        return cls
    return wrapper

class Annotator(ABC):
    source_type: str

    @abstractmethod
    def annotate(self, event: SourceEvent) -> SignalEvidence | None: ...
```

## Task 1.3 — MqttAnnotator (annotators/mqtt.py)

```python
from discover_client.identification.annotator import Annotator, register_annotator
from discover_client.source import SourceEvent
from discover_client.identification.evidence import SignalEvidence
import re
import time

@register_annotator("mqtt")
class MqttAnnotator(Annotator):
    def annotate(self, event: SourceEvent) -> SignalEvidence | None:
        topic = event.payload.get("topic", "")
        if event.event_type == "data":
            # Extract hostname hint from topic: govee/abc123/state → abc123
            hostname_hint = None
            parts = topic.split("/")
            if len(parts) >= 2:
                hostname_hint = parts[1]

            return SignalEvidence(
                source_id=event.source_id,
                source_type="mqtt",
                mqtt_topic=topic,
                mqtt_payload_keys=set(event.payload.get("value", {}).keys()),
                hostname=hostname_hint,
                timestamp=event.timestamp,
            )
        return None
```

## Task 1.4 — __init__.py files

`discover_client/identification/__init__.py`:
```python
from discover_client.identification.fingerprint import DeviceFingerprint, FINGERPRINTS
from discover_client.identification.evidence import SignalEvidence
from discover_client.identification.device import Device, DeviceHypothesis
from discover_client.identification.annotator import Annotator, ANNOTATORS, register_annotator
```

`discover_client/identification/annotators/__init__.py`:
```python
from discover_client.identification.annotators import mqtt  # triggers @register_annotator("mqtt")
```

## Verification

Run this script and verify output:

```python
from discover_client.identification import FINGERPRINTS, ANNOTATORS, SignalEvidence
from discover_client.identification.annotators import mqtt

# 1. Fingerprints loaded
assert len(FINGERPRINTS) >= 2  # Govee + Unknown
govee = [f for f in FINGERPRINTS if f.device_type == "Govee H5179"]
assert len(govee) == 1
assert govee[0].category == "temp_sensor"

# 2. Annotator registered
assert "mqtt" in ANNOTATORS
annotator = ANNOTATORS["mqtt"]()

# 3. Annotate a mock event
from discover_client.source import SourceEvent
event = SourceEvent(
    source_id="mqtt-1", source_type="mqtt", timestamp=1234567890.0,
    event_type="data",
    payload={"topic": "govee/abc123/state", "value": {"temp": 23.5, "humidity": 55}}
)
evidence = annotator.annotate(event)
assert evidence is not None
assert evidence.mqtt_topic == "govee/abc123/state"
assert evidence.mqtt_payload_keys == {"temp", "humidity"}
assert evidence.hostname == "abc123"
assert evidence.source_type == "mqtt"

print("All checks passed")
```
