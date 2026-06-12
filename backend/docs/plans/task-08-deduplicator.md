# Task 08 — Deduplicator + UI Page

## Scope
实现设备去重器（识别管线第一步），将不同源的结构化证据合并为统一设备，并在 GUI 新增 Dedup 页面可视化结果。

## Files to create
- `discover_client/identification/deduplicator.py`
- `discover_client/gui/dedup_page.py`
- Update `discover_client/gui/main_window.py` — add Dedup tab
- Update `discover_client/gui/worker.py` — wire evidence to deduplicator

## Task 8.1 — Deduplicator core

### Rules (in order of strength)

| Priority | Rule | Score | How |
|----------|------|-------|-----|
| 1 | MAC address exact match | 100 | `mac` field from NmapAnnotator evidence |
| 2 | MAC prefix (3 octets) = hostname prefix + same IP | 95 | `58:04:4F` in hostname `58044F9ADC05.local.` + IP matches |
| 3 | Same IP + same mDNS service type | 70 | Both on 192.168.5.2 + both _matter._tcp. |
| 4 | Same IP | 50 | Two devices on same IP |
| 5 | Hostname prefix (3+ chars) match + same IP subnet | 30 | `58044F9ADC05` prefix match |

**Merge threshold: >= 50**. An evidence matches an existing device if ANY rule meets or exceeds the threshold.

Beyond just merging devices, the deduplicator also needs to **collect all evidence fields** for the matched device, building a richer device profile: multiple IPs, hostnames, MAC prefixes, and service types from different sources. When an evidence's hostname/IP/MAC fields are present, they should be added to the device's collective sets.

### Device object (update device.py)

```python
@dataclass
class Device:
    device_id: str                    # assigned by deduplicator
    hypotheses: list[DeviceHypothesis]    # from fingerprint matching (future)
    total_evidence_count: int
    last_seen: float
    ip_addresses: set[str]
    hostnames: set[str]
    mac_prefixes: set[str]
    vendor: str | None                # from NmapAnnotator (OUI-enriched)
    os_guess: str | None              # from NmapAnnotator
    service_types: set[str]           # from mDNS
    payload_keys: set[str]            # from MQTT (accumulated)
    source_ids: set[str]              # which sources contributed
```

### Deduplicator class

```python
class Deduplicator:
    def __init__(self):
        self._devices: list[Device] = []
        self._next_id = 1
    
    def ingest(self, evidence: SignalEvidence) -> Device:
        """Assign evidence to existing or new Device. Return the assigned Device."""
        ...
    
    def get_devices(self) -> list[Device]:
        """Return current device list sorted by last_seen desc."""
        ...
```

### Matching algorithm pseudocode

```
def ingest(evidence):
    for each existing device:
        for each rule in priority order:
            if rule matches:
                merge evidence into device
                return device
    
    # No match found — create new device
    device = Device(device_id=f"device-{next_id}", ...)
    merge evidence into device
    return device
```

## Task 8.2 — GUI Dedup page

New tab "Dedup" between "Events" tab and "Sources & Log" tab. Inside: a table.

### Columns

| Col | Content |
|-----|---------|
| Device ID | `device-1`, `device-2`, ... |
| IPs | `192.168.5.1, 192.168.5.2` |
| Hostnames | `zte.home, 58044F9ADC05.local.` |
| MAC Prefixes | `7C:7D:21, 58:04:4F` |
| Vendor | `zte corporation` |
| Service Types | `_matter._tcp.local.` |
| Evidence Count | `8` |
| Last Seen | `21:23:47` |

### Signals in Worker

Add `dedup_updated = Signal(list)` — fires when deduplicator merges/creates device. Passes full device list.

Wire `_on_evidence` to also call `self.deduplicator.ingest(evidence)` and emit the updated device list.

## Task 8.3 — Verification

Run this Python script:

```python
from discover_client.identification.deduplicator import Deduplicator
from discover_client.identification.evidence import SignalEvidence

d = Deduplicator()

# Simulate nmap discovery: TP-Link hub
e1 = SignalEvidence(source_id="nmap-1", source_type="nmap",
    nmap_mac="58:04:4F:9A:DC:05", nmap_vendor="TP-Link Systems Inc.",
    mac_prefix="58:04:4F", ip_address="192.168.5.2", hostname=None, timestamp=1.0)
dev1 = d.ingest(e1)
assert dev1.device_id == "device-1"
assert "192.168.5.2" in dev1.ip_addresses
assert "58:04:4F" in dev1.mac_prefixes

# Simulate mDNS discovery: same TP-Link hub
e2 = SignalEvidence(source_id="mdns-1", source_type="mdns",
    mdns_service_type="_matter._tcp.local.", hostname="58044F9ADC05.local.",
    ip_address="192.168.5.2", timestamp=2.0)
dev2 = d.ingest(e2)
assert dev2.device_id == "device-1"  # merged!
assert "58044F9ADC05.local." in dev2.hostnames
assert "_matter._tcp.local." in dev2.service_types

# Simulate different device on different IP
e3 = SignalEvidence(source_id="nmap-1", source_type="nmap",
    nmap_mac="80:64:7C:D9:AC:23", nmap_vendor="Tuya Smart Inc.",
    mac_prefix="80:64:7C", ip_address="192.168.5.3", hostname=None, timestamp=3.0)
dev3 = d.ingest(e3)
assert dev3.device_id == "device-2"  # NOT merged!

devices = d.get_devices()
assert len(devices) == 2

print("All dedup checks passed")
```

## Anti-patterns to avoid

- Do NOT merge by MAC prefix alone — two Tuya devices share `80:64:7C` but are different physical devices
- Do NOT assume mDNS TXT keys are present — Matter devices often have empty TXT records
- Do NOT skip evidence with missing fields — empty fields are normal for partial sources
