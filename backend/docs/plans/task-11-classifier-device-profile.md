# Task 11 — Topic Classifier + Device Profile Page

## Scope
实现 MQTT Topic 分类器（6 条规则，三分类，带置信度），并新增 Device Profile 页面——卡片式设备列表，点开展示数据和可点击的操作按钮。

## Files to create
- `discover_client/identification/classifier.py` — TopicClassifier
- `discover_client/gui/device_profile_page.py` — Device Profile cards + expand

## Files to modify
- `discover_client/identification/__init__.py` — export TopicClassifier
- `discover_client/gui/main_window.py` — add Device Profile tab, wire signals
- `discover_client/gui/worker.py` — integrate classifier into on_event pipeline
- `discover_client/gui/style.qss` — card styles for device profiles

## Task 11.1 — TopicClassifier (classifier.py)

Six rules from spec Section 10, each returning a Classification:

```python
from dataclasses import dataclass

@dataclass
class Classification:
    label: str           # "telemetry" | "command" | "unknown"
    confidence: float    # 0.0 - 1.0
    evidence: list[str]  # human-readable reasons

class TopicClassifier:
    # Rules in priority order (first match wins, but all rules contribute to confidence)
    def classify(self, topic: str, payload: dict | str | None = None) -> Classification:
        # Rule 1: topic ends with set/command/cmnd/ctrl → command, 0.85
        # Rule 2: payload is enum-like ("ON"/"OFF"/"TOGGLE"/"true"/"false") → command, 0.90
        # Rule 3: topic ends with sensor word (temperature/humidity/pressure/light/motion/...) → telemetry, 0.80
        # Rule 4: topic ends with state/status/data → telemetry, 0.70
        # Rule 5: payload has numeric + unit pattern → telemetry, 0.75
        # Rule 6: fallback → unknown, 0.30
        ...
```

**Integration**: Worker's `on_event` calls `classifier.classify()` for MQTT data events. Classification result is:
- Stored in a per-device classification accumulator
- Emitted via a new `device_classified` Signal alongside dedup/devices update

DataSnapshot continues to process telemetry events; OperationsTracker continues to process command events.

## Task 11.2 — Device Profile Page (device_profile_page.py)

Replaces the current flat Dedup/Data/Operations tables with a unified card view.

### Card layout (collapsed state)

```
┌──────────────────────────────────────────────┐
│ ● device-3    TP-Link Hub    7 证据            │
│ IP: 192.168.5.2   MAC: 58:04:4F              │
│ Matter 设备 | 置信度: 0.68                    │
└──────────────────────────────────────────────┘
```

### Card layout (expanded state)

```
┌──────────────────────────────────────────────┐
│ ● device-3    TP-Link Hub    7 证据     [收起]│
│ IP: 192.168.5.2   MAC: 58:04:4F              │
│ Matter 设备 | 置信度: 0.68                    │
├──────────────────────────────────────────────┤
│ ┌─ Data ──────────────────────────────────┐  │
│ │ (no data)                                │  │
│ └──────────────────────────────────────────┘  │
│ ┌─ Operations ────────────────────────────┐  │
│ │ [Toggle] [Set Brightness]                │  │
│ │ topic: device-3/set   payload: {"val":_} │  │
│ └──────────────────────────────────────────┘  │
└──────────────────────────────────────────────┘
```

### Behavior
- Click card header → expand/collapse
- Each card is a QFrame with collapsible sections
- Data section reads from DataSnapshot (same data as Data tab)
- Operations section shows buttons for each discovered operation
  - Button click → publish MQTT command via Worker
  - Textbox+button for operations that take a parameter value
- Cards are scrollable if more than fit on screen

### Worker wiring
- New signal: `device_profile_updated = Signal(list)` — emits list of DeviceProfile objects
- DeviceProfile = Device + category + confidence + latest data snapshot + operation capabilities

```python
@dataclass
class DeviceProfile:
    device_id: str
    category: str               # from classifier accumulator
    confidence: float           # from classifier accumulator
    ip_addresses: set[str]
    mac_prefixes: set[str]
    vendor: str | None
    data_sensors: dict[str, dict]  # sensor_name -> {value, unit, ts}
    operations: list[dict]         # [{action, topic, args: [{key, type, example}]}]
    total_evidence_count: int
    last_seen: float
```

## Task 11.3 — Verification

Smoke test with mock Govee source:

```python
from discover_client.identification.classifier import TopicClassifier

c = TopicClassifier()

# Govee temperature topic
r = c.classify("govee/H5179/a1b2c3d4e5f6/temperature", {"unit": "C", "value": 25.3})
assert r.label == "telemetry"
assert r.confidence > 0.7
print(f"temperature: {r.label} ({r.confidence}) — {r.evidence}")

# Set command topic
r = c.classify("zigbee2mqtt/0x001/set", {"state": "ON"})
assert r.label == "command"
assert r.confidence > 0.8
print(f"set command: {r.label} ({r.confidence}) — {r.evidence}")

# Ambiguous topic
r = c.classify("custom/device/output")
assert r.label == "unknown"
print(f"ambiguous: {r.label} ({r.confidence}) — {r.evidence}")

print("Classifier checks passed")
```

## Anti-patterns to avoid
- Do NOT add GUI tab for classifier — it's a pipeline component, not a visible page
- Do NOT modify existing Dedup/Data/Operations pages — Device Profile is a separate tab
- Do NOT change the DataSnapshot or OperationsTracker extraction logic — classifier feeds them, not replaces them
