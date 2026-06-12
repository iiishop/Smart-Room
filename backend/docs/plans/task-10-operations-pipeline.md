# Task 10 — Operations Pipeline

## Scope
检测每个 MQTT 设备支持哪些操作（命令），用启发式规则分类 topic 是数据还是命令，记录操作能力，并在 GUI 新增 Operations 页面可视化。

## Background
MQTT 协议不区分数据和操作——全看 topic 命名约定和 payload 模式。操作管线用启发式规则检测命令 topic，记录操作能力。后续 Tier 2a 设备识别（VR 触发操作来定位设备）会用到这个操作列表。

## Files to create/modify
- Create: `discover_client/operations/tracker.py` — OperationsTracker class
- Create: `discover_client/operations/__init__.py`
- Create: `discover_client/gui/operations_page.py` — GUI tab
- Modify: `discover_client/gui/worker.py` — wire tracker, emit operations_updated
- Modify: `discover_client/gui/main_window.py` — add Operations tab

## Task 10.1 — OperationsTracker (tracker.py)

### OperationCapability data class

```python
@dataclass
class OperationCapability:
    device_id: str
    topic: str           # e.g. "govee/H5179/a1b2c3d4e5f6/set"
    action: str          # "set", "toggle", "switch", "command"
    accepted_values: list[str]  # ["ON","OFF"] or ["TOGGLE"] or []
    confidence: float    # 0.0 - 1.0
    first_seen: float
    last_seen: float
```

### OperationsTracker class

```python
class OperationsTracker:
    def __init__(self):
        self._capabilities: dict[str, dict[str, OperationCapability]] = {}
    
    def ingest(self, device_id: str, evidence: SignalEvidence) -> OperationCapability | None:
        """Classify as command? If yes, record capability. Returns new/changed cap or None."""
        ...
    
    def get_capabilities(self, device_id: str) -> list[OperationCapability]:
        ...
    
    def get_all(self) -> dict[str, list[OperationCapability]]:
        ...
```

### Classification rules (from spec Section 6 Topic Classifier)

| Priority | Rule | Assigns | Confidence |
|----------|------|---------|------------|
| 1 | topic 末段 in `[set, command, cmnd, ctrl, switch, toggle, power, relay]` | command | 0.85 |
| 2 | topic 末段 in `[state, status, data, telemetry, sensor, reading]` | telemetry | 0.70 |
| 3 | topic 包含传感器词 (`temperature, humidity, pressure, light, motion`) | telemetry | 0.80 |
| 4 | payload 是小写枚举字符串 (`ON, OFF, TOGGLE, true, false, open, close`) | command | 0.90 |
| 5 | payload 含数值 (int/float) + unit key | telemetry | 0.75 |
| 6 | 以上都不匹配 | unknown | 0.30 |

Only command-classified evidence (any rule with command assignment) triggers capability recording. An `OperationCapability` is created or updated: topic deduplicated per device, values accumulated.

### Action extraction
From the topic's last segment, extract a canonical action name:
- `set` → "set"
- `switch` → "switch"
- `toggle` → "toggle"
- `power` → "power"
- `command` → "command"
- `cmnd` → "command"
- `ctrl` → "control"
- `relay` → "switch"

From the payload, extract accepted values:
- If payload is a string like "ON", "OFF" → record these as accepted values
- If payload is a dict like `{"state": "ON"}` → extract the value from the dict
- Accumulate unique values

## Task 10.2 — Worker wiring

Add to Worker:
```python
operations_updated = Signal(list)  # fires when operations change

self._ops_tracker: OperationsTracker | None = None
```

In `_run_loop`, create `OperationsTracker()` after `Deduplicator()`.

In `on_event`, after deduplicator processes the evidence, also pass to operations tracker. If a new/changed operation capability is detected, emit `operations_updated` with the full list for that device.

## Task 10.3 — GUI Operations page

New page "Operations" between "Data" and "Dedup" tabs. Table columns:

| Col | Content |
|-----|---------|
| Device ID | device-1 |
| Topic | govee/H5179/a1b2c3d4e5f6/switch |
| Action | switch |
| Values | {"state": "ON"} → ON, OFF |
| Confidence | 0.85 |
| First Seen | 22:17:45 |
| Last Seen | 22:18:30 |

## Task 10.4 — Verification

Test script (to be run by the user after starting MQTT source):

Since we don't have real command topics in the mock Govee source, the verification is:
1. Unit test for OperationsTracker classification rules
2. Unit test for capability accumulation (duplicate topics merged)
3. Smoke test: GUI loads, Operations tab appears, no crash

```python
# test_operations_tracker.py
from discover_client.operations.tracker import OperationsTracker, _classify

# Test Rule 1: command topic last segment
evidence = SignalEvidence(source_type="mqtt", mqtt_topic="lab/device/switch", ...)
category, conf = _classify(evidence)
assert category == "command"
assert conf == 0.85

# Test Rule 4: enumeration payload
evidence = SignalEvidence(source_type="mqtt", mqtt_topic="lab/device/something",
                          payload_keys={"state"}, ...)
# (payload inspection done inside tracker, not _classify)
tracker = OperationsTracker()
cap = tracker.ingest("device-1", evidence_with_payload_containing_ON)
assert cap is not None
assert "ON" in cap.accepted_values
```

## Anti-patterns to avoid
- Do NOT classify all MQTT topics as commands — only the ones matching rules 1, 4
- Do NOT hardcode device-specific topic patterns — rules must be generic
- Do NOT emit operations_updated on every telemetry event — only when a new command topic is discovered
- Do NOT create the Operations tab with empty data visible — show "(no operations discovered)" placeholder
