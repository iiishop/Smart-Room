# Task 09 — Device Feature Extraction + UI Page

## Scope
For each deduplicated Device, extract semantic features from accumulated evidence:
- Topic keyword tags (temperature → 温度传感器 capability)
- Payload key tags (unit/value → 数值型数据)
- Protocol tags (MQTT, Matter from mDNS service_type)
- Vendor hints (from topic or nmap OUI)
- Topic path itself (for spatial-VR matching: "this device is at topic govee/H5179/a1b2c3d4e5f6")

Add a new UI page "Features" showing the feature table.

## Design: deterministic feature extraction (no LLM for now)

Don't call external LLM APIs yet. Instead, use a keyword-to-capability mapping table run locally. This is instant, free, and reliable. LLM-enhanced mode can be added later as an optional layer.

## Files to create/modify

- `discover_client/identification/features.py` ← NEW
- `discover_client/gui/features_page.py` ← NEW
- Update `discover_client/identification/__init__.py` — export FeatureExtractor
- Update `discover_client/gui/main_window.py` — add Features tab
- Update `discover_client/gui/worker.py` — call feature extraction after dedup update

## Task 9.1 — Feature extraction engine

### Capability keyword map

```python
CAPABILITY_KEYWORDS = {
    # Topic keywords → capability tags
    "temperature": "温度传感",
    "temp": "温度传感",
    "humidity": "湿度传感",
    "humid": "湿度传感",
    "light": "光照传感",
    "lux": "光照传感",
    "motion": "运动检测",
    "pir": "人体感应",
    "switch": "开关控制",
    "relay": "继电器",
    "power": "功耗监测",
    "energy": "能耗监测",
    "battery": "电池供电",
    "voltage": "电压监测",
    "current": "电流监测",
    "co2": "CO2传感",
    "air": "空气质量",
    "door": "门磁",
    "window": "窗磁",
    "leak": "漏水检测",
    "smoke": "烟雾检测",
    "button": "按钮",
    "display": "显示屏",
    # Payload key keywords
    "unit": "数值型数据",
    "value": "数值型数据",
    "state": "状态值",
    "status": "状态值",
    "rgb": "RGB灯控",
    "brightness": "亮度控制",
    "on": "布尔状态",
    "off": "布尔状态",
}
```

### FeatureExtractor class

```python
class FeatureExtractor:
    def extract(self, device: Device) -> DeviceFeatures:
        """Extract semantic features from accumulated device evidence."""
        tags = set()
        
        # From topic prefixes
        for topic_prefix in device.topic_prefixes:
            for keyword, tag in CAPABILITY_KEYWORDS.items():
                if keyword in topic_prefix.lower():
                    tags.add(tag)
        
        # From payload keys
        for key in device.payload_keys:
            for keyword, tag in CAPABILITY_KEYWORDS.items():
                if keyword in key.lower():
                    tags.add(tag)
        
        # Protocol detection
        protocols = set()
        if device.topic_prefixes:
            protocols.add("MQTT")
        if any("_matter" in svc for svc in device.service_types):
            protocols.add("Matter")
        if any("_home-assistant" in svc for svc in device.service_types):
            protocols.add("HomeAssistant")
        
        # Vendor hints from topic (e.g., "govee" from govee/H5179/...)
        vendor_hints = set()
        for topic_prefix in device.topic_prefixes:
            parts = topic_prefix.split("/")
            if parts:
                vendor_hints.add(parts[0])
        # Don't add numeric parts as vendor hints (e.g., "192.168.5.2" from nmap hostname)
        vendor_hints = {v for v in vendor_hints if not v.replace(".", "").isdigit()}
        
        return DeviceFeatures(
            device_id=device.device_id,
            capabilities=sorted(tags),
            protocols=sorted(protocols),
            vendor_hints=sorted(vendor_hints),
            topic_prefixes=sorted(device.topic_prefixes),
            evidence_count=device.total_evidence_count,
        )
```

### DeviceFeatures dataclass

```python
@dataclass
class DeviceFeatures:
    device_id: str
    capabilities: list[str]
    protocols: list[str]
    vendor_hints: list[str]
    topic_prefixes: list[str]
    evidence_count: int
```

## Task 9.2 — Features page

New tab "Features" in GUI. A table:

| Device ID | Capabilities | Protocols | Vendor Hints | Topic Prefixes | Evidence Count |
|-----------|-------------|-----------|-------------|----------------|----------------|
| device-1 | 数值型数据, 温度传感, 湿度传感 | MQTT | govee | govee/H5179/a1b2c3d4e5f6 | 36 |
| device-3 | Matter | TP-Link Systems Inc. | 58044F9ADC05 | 7 |

The table uses CopyableTableWidget for selection+copy.

### FeatureExtractor in Worker

Add a `FeatureExtractor` instance in Worker. After each dedup update, run feature extraction and emit a separate signal `features_updated = Signal(list)`.

### MainWindow wiring

```python
self.worker.features_updated.connect(self._on_features_updated)

def _on_features_updated(self, features: list) -> None:
    self._features_page.set_features(features)
```

## Task 9.3 — Worker integration detail

In `worker.py`, after the dedup_updated emission:

```python
if self.deduplicator is not None:
    self.deduplicator.ingest(evidence)
    self.dedup_updated.emit(self.deduplicator.get_devices())
    if self.feature_extractor is not None:
        features = [self.feature_extractor.extract(d) for d in self.deduplicator.get_devices()]
        self.features_updated.emit(features)
```

Create `self.feature_extractor = None` in `__init__`, set to `FeatureExtractor()` in `_run_loop` alongside `self.deduplicator = Deduplicator()`.

## Verification

```python
from discover_client.identification.features import FeatureExtractor
from discover_client.identification.device import Device

extractor = FeatureExtractor()

# Test device-1: MQTT temp/humidity sensor
d1 = Device(device_id="device-1")
d1.topic_prefixes = {"govee/H5179/a1b2c3d4e5f6"}
d1.payload_keys = {"unit", "value"}
f1 = extractor.extract(d1)
assert "温度传感" in f1.capabilities
assert "湿度传感" in f1.capabilities
assert "数值型数据" in f1.capabilities
assert "MQTT" in f1.protocols
assert "govee" in f1.vendor_hints

# Test device-3: Matter device
d3 = Device(device_id="device-3")
d3.service_types = {"_matter._tcp.local."}
f3 = extractor.extract(d3)
assert "Matter" in f3.protocols
assert not f3.capabilities  # No MQTT topics → no capability keywords

print("All feature extraction checks passed")
```

## Anti-patterns to avoid

- Do NOT call external LLM APIs — use deterministic keyword matching only
- Do NOT modify the deduplicator or device model
- Do NOT add new dependencies (no pip install)
- Handle devices with zero evidence gracefully (empty feature sets)
- topic_prefixes may be empty for devices discovered only via nmap/mDNS
