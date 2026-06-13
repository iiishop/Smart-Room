# Multi-Format MQTT Operation Support

**Status:** draft  
**Author:** 盒马  
**Date:** 2026-06-13

---

## 1. Problem

`OperationsTracker` and `TopicClassifier` currently assume a **single topic + dict payload** shape:

```
Topic:  mock/light-1/set
Payload: {"power": "ON", "brightness": 50}
```

In reality, IoT MQTT devices use at least 5 different conventions. Guerrilla devices in the lab,
consumer gear (Tasmota, Zigbee2MQTT, Shelly), and bare-metal sensors all speak different dialects.

**Current failure modes:**

| Incoming message | What happens today | What should happen |
|---|---|---|
| `mock/light-1/set/power` ← `"ON"` | Classified as telemetry or unknown (topic suffix "power" is not in TELEMETRY_SUFFIXES or COMMAND_SUFFIXES) | Recognized as command for sensor `power` |
| `cmnd/light-1/Power` ← `"ON"` | `cmnd` suffix not in any list — classified unknown | Forced command via `cmnd/` prefix, sensor `power` |
| `zigbee2mqtt/light-1/set` ← `{"state": "ON"}` | Extracts `state` as a text sensor (already works if TELEMETRY_SUFFIX_CONFIDENCE wins) | Correctly recognized as telemetry or command based on suffix `set`; `state` mapped as sensor |
| `mock/light-1/set` ← `"ON"` (bare string) | `_extract_action` returns "command" (fallback), but `_classify` sees no enum payload → scores only from topic suffix `set` → command (0.85). Payload missed entirely by DataSnapshot. | Recognized as command, single generic capability |

---

## 2. Five MQTT Dialects We Must Support

### Dialect A: Single topic + dict (current, mock_light)
```
Topic:  mock/light-1/set
Payload: {"power": "ON", "brightness": 50}
```
Each dict key → one `OperationCapability`.

### Dialect B: Sub-topic per sensor (ESPHome, custom firmware)
```
Topic:  mock/light-1/set/power       ← "ON"
Topic:  mock/light-1/set/brightness  ← 50
```
Topic last segment is the sensor name. Payload is naked string or number.

### Dialect C: Tasmota-style prefix (cmnd/stat)
```
Topic:  cmnd/light-1/Power    ← "ON"
Topic:  cmnd/light-1/Dimmer   ← 50
Topic:  stat/light-1/POWER    ← "ON"  (telemetry, not command)
```
`cmnd/` prefix → force `command` classification. `stat/` prefix → force `telemetry`.
Sensor name is the topic last segment.

### Dialect D: Zigbee2MQTT / Shelly
```
Topic:  zigbee2mqtt/light-1/set   ← {"state": "ON", "brightness": 100}
Topic:  shelly/plug-1/relay/0     ← {"command": "toggle"}
```
Dict payload (works like Dialect A). Extra classification signals:
- `"command"` key in payload → operation hint
- Suffix `set` → command context

### Dialect E: Bare value on a set topic (rare but real)
```
Topic:  mock/light-1/set   ← "ON"
```
Payload is a string or number, no key. Sensor name inferred from DataSnapshot context
(or default "value").

---

## 3. Architecture

### 3.1 TopicClassifier → generalized prefix + suffix scoring

New rules added to `_classify()`:

| Signal | Score | Category |
|---|---|---|
| Topic prefix is `cmnd/` | 1.00 | command |
| Topic prefix is `stat/` | 1.00 | telemetry |
| Topic suffix in COMMAND_SUFFIXES | 0.85 | command |
| Topic suffix in TELEMETRY_SUFFIXES | 0.95 | telemetry |
| Payload dict has `"command"` key | 0.80 | command |
| Payload contains enum value (ON/OFF/…) | 0.90 | command |
| Payload has numeric + unit | 0.75 | telemetry |
| None of the above | 0.30 | unknown |

**New**: `_topic_prefix()` extracts the first segment (before first `/`) and checks for known
command/telemetry prefixes.

### 3.2 OperationsTracker → multi-key ingestion

`ingest()` currently creates one `OperationCapability` per topic. After the change:

For a **dict** payload:
- If topic suffix is "set" (or similar), iterate over ALL dict keys
- Each key → one `OperationCapability` with:
  - `action` derived from topic context + key name
  - `topic` = the full MQTT topic
  - `accepted_values` = values seen for this specific key across all events

For a **bare value** payload:
- If topic suffix is in COMMAND_SUFFIXES OR topic has `cmnd/` prefix:
  - `action` = topic suffix or last segment
  - `accepted_values` = accumulated enum values seen
- Otherwise: ignore (it's telemetry data, not an operation)

Sub-topic dialect:
```
mock/light-1/set/power ← "ON"
```
Topic suffix: "power" (last segment after the COMMAND_SUFFIX tokens are stripped? No —
we look at the last segment; "power" itself could be a sensor name.)

**Decision**: if topic last segment is NOT in COMMAND_SUFFIXES and NOT in TELEMETRY_SUFFIXES
and NOT a known metadata word → treat it as a sensor name. Walk backwards through topic
segments to find the "action" segment.

```
segments = ["mock", "light-1", "set", "power"]
action_index = first suffix from right that IS in COMMAND_SUFFIXES → index 2 ("set")
sensor = segments[action_index + 1:] → ["power"]
topic_prefix = segments[:action_index + 1] → "mock/light-1/set"
```

### 3.3 _infer_operation_args → DataSnapshot-aware already

No change needed here. Current logic (commit `9d1174a`) already uses DataSnapshot sensor keys
to generate input fields. New OperationCapabilities from multi-key ingestion will have the
right sensor names (`power`, `brightness`) and the args will include corresponding input boxes.

### 3.4 DataSnapshot → already handles text values

No change needed. `_extract_text_readings` (commit `209fe71`) already detects the payload
shape and extracts text sensor values.

---

## 4. Detailed Changes

### 4.1 `TopicClassifier._classify()` — add prefix rules

```python
def _topic_prefix(topic: str | None) -> str:
    if not topic:
        return ""
    return topic.split("/", 1)[0].strip().lower()

# In _classify():
PREFIX_COMMAND = {"cmnd", "command"}
PREFIX_TELEMETRY = {"stat", "state", "tele", "telemetry"}

prefix = _topic_prefix(evidence.mqtt_topic)
if prefix in PREFIX_COMMAND:
    scores.append(("command", 1.0))
if prefix in PREFIX_TELEMETRY:
    scores.append(("telemetry", 1.0))
```

### 4.2 `OperationsTracker.ingest()` — explode dict into multi-key

```python
def ingest(self, device_id: str, evidence: SignalEvidence) -> list[OperationCapability]:
    # ... classification ...
    
    capabilities = []
    payload = evidence.mqtt_payload
    
    if isinstance(payload, dict):
        topic = evidence.mqtt_topic
        suffix = _topic_suffix(topic)
        
        # "set" suffix with dict payload → explode keys
        if suffix in COMMAND_SUFFIXES or category == "command":
            for key, val in payload.items():
                if _is_metadata_key(key):
                    continue
                action = _action_for_dict_key(key, suffix, topic)
                cap = self._record_capability(device_id, topic, key, action, val, evidence.timestamp)
                capabilities.append(cap)
        elif category == "telemetry":
            # Telemetry dict → don't create operations, just let DataSnapshot handle
            pass
    else:
        # Bare value
        action = _infer_action_from_topic(topic)
        if category == "command" or (suffix in COMMAND_SUFFIXES):
            cap = self._record_capability(device_id, topic, None, action, payload, evidence.timestamp)
            capabilities.append(cap)
    
    return capabilities if capabilities else None
```

Key: `_record_capability` merges by `(device_id, topic, sensor_key)`.
- For dict explosion: sensor_key = dict key ("power", "brightness")
- For bare value: sensor_key = last topic segment

### 4.3 Sub-topic sensor detection

`_infer_action_from_topic(topic)` walks segments right-to-left to find the action:

```python
def _infer_action_from_topic(topic: str) -> tuple[str, str]:
    """
    Returns (action, sensor_name) from a topic like "mock/light-1/set/power".
    action = "set"
    sensor = "power"
    """
    segments = topic.split("/")
    for i in range(len(segments) - 1, -1, -1):
        if segments[i].lower() in COMMAND_SUFFIXES:
            action = COMMAND_SUFFIXES[segments[i].lower()]
            sensor = segments[i + 1] if i + 1 < len(segments) else "value"
            return action, sensor
    # Fallback: last segment is the action
    action = COMMAND_SUFFIXES.get(segments[-1].lower(), "command")
    return action, "value"
```

### 4.4 Multi-key capability storage

Change `_capabilities` key from `topic` to `(topic, sensor_key)`:

```python
class OperationsTracker:
    def __init__(self):
        self._capabilities: dict[str, dict[tuple[str, str | None], OperationCapability]] = {}
    
    def _record_capability(self, device_id, topic, sensor_key, action, payload, ts):
        key = (topic, sensor_key)
        by_topic = self._capabilities.setdefault(device_id, {})
        existing = by_topic.get(key)
        if existing:
            # merge values
            ...
        else:
            cap = OperationCapability(device_id, topic, sensor_key, action, ...)
            by_topic[key] = cap
        
        return replace(...)
```

---

## 5. Backward Compatibility

- Dialect A (single topic + dict) works identically after the change
- Existing `get_capabilities(device_id)` returns all capabilities; new `sensor_key` field is
  added to `OperationCapability` but callers that don't use it are unaffected
- `_infer_operation_args` already handles `data_keys` — no change needed
- All 36 existing tests must pass

---

## 6. Test Plan

| Test | Dialect | Input | Expected |
|---|---|---|---|
| `test_ingest_explodes_dict_into_multi_key` | A | `mock/light-1/set` ← `{"power":"ON","brightness":50}` | 2 capabilities |
| `test_ingest_subtopic_naked_value` | B | `mock/light-1/set/power` ← `"ON"` | 1 capability, sensor="power" |
| `test_classify_tasmota_cmnd_prefix` | C | `cmnd/light-1/Power` ← `"ON"` | category="command", confidence=1.0 |
| `test_classify_tasmota_stat_prefix` | C | `stat/light-1/POWER` ← `"ON"` | category="telemetry", confidence=1.0 |
| `test_ingest_zigbee2mqtt_command_key` | D | `zigbee2mqtt/light-1/set` ← `{"command":"toggle"}` | command from `"command"` key |
| `test_ingest_bare_string_on_set_topic` | E | `mock/light-1/set` ← `"ON"` | 1 capability, sensor=None |
| `test_classify_prefix_overrides_suffix` | mixed | `cmnd/light-1/temperature` ← 25.0 | command (1.0) overrides sensor word |
| `test_get_capabilities_returns_all_keys` | A | dict with 3 keys ingested | 3 capabilities in list |

---

## 7. Risks

- **Sensor key collision**: `mock/light-1/set` ← `{"brightness": 50}` and
  `mock/light-1/set/brightness` ← `60` → two capabilities with same sensor key
  but different topics. De-duplicated by `(topic, sensor_key)` composite key.
- **Metadata keys leaking into operations**: `{"_announce": True, "device": "light-1"}`
  must be filtered. `_is_metadata_key()` checks for known metadata keys.
- **Tasmota `stat/` forwarding to DataSnapshot**: `stat/` prefix means telemetry,
  DataSnapshot already handles text values. No special treatment needed —
  `stat/light-1/POWER` ← `"ON"` → text reading for sensor "power".
- **Bare value ambiguous**: `mock/light-1/set` ← `25.0` — is this brightness or
  temperature? Without context, we assign generic "value" sensor name.
  DataSnapshot context (nearby topics) later disambiguates via dedup and feature extraction.

---

## 8. Implementation Order

1. Add `sensor_key` field to `OperationCapability` dataclass
2. Add `_topic_prefix()` and prefix scoring to `TopicClassifier._classify()`
3. Refactor `OperationsTracker.ingest()`: dict explosion, sub-topic walk, bare value
4. Update `_extract_action()` to handle sub-topic sensor names
5. Add `_is_metadata_key()` helper
6. Update `get_capabilities()` to return all keys
7. Write 8 tests (Section 6)
8. Run full test suite (36 existing + 8 new)
9. Manual integration test with mock_light
