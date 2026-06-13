# MQTT Dialect Recognition — Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.  
> **Goal:** Replace hardcoded `TopicClassifier` + `MqttAnnotator` with pluggable dialect recognizers that handle 5 MQTT formats.  
> **Architecture:** Each dialect = one `DialectRecognizer` subclass (registered via `@register_recognizer`). Aggregator runs all, normalizes keys, deduplicates, fans out to existing `OperationsTracker`/`DataSnapshot`.  
> **Tech Stack:** Python 3.12, uv, pytest, dataclasses

---

## Task 1: Create `dialect` package skeleton

**Objective:** Bootstrap the new `discover_client/dialect/` module with registry, ABC, and imports.

**Files:**
- Create: `discover_client/dialect/__init__.py`
- Create: `discover_client/dialect/recognizer.py`
- Create: `discover_client/dialect/utils.py`
- Create: `discover_client/dialect/recognizers/__init__.py`

**Step 1: Write `recognizer.py` (data structures + ABC)**

```python
"""DialectRecognizer ABC and output types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RecognizedOperation:
    topic: str
    action: str
    sensor_key: str | None = None
    accepted_values: list[str] = field(default_factory=list)
    payload_example: Any = None
    is_enum: bool = False


@dataclass
class RecognizedSensor:
    sensor_type: str
    value: Any
    unit: str = ""


@dataclass
class RecognizerOutput:
    operations: list[RecognizedOperation] = field(default_factory=list)
    sensor_readings: list[RecognizedSensor] = field(default_factory=list)
    message_type: str = "data"
    dialect_hint: str | None = None
    device_id_hint: str | None = None


class DialectRecognizer(ABC):
    SPECIFICITY: int = 50

    @abstractmethod
    def match(self, topic: str, payload: Any) -> float:
        ...

    @abstractmethod
    def extract(self, topic: str, payload: Any) -> RecognizerOutput:
        ...
```

**Step 2: Write `__init__.py` (registry)**

```python
"""MQTT dialect recognition — pluggable recognizer registry."""

from discover_client.dialect.recognizer import (
    DialectRecognizer,
    RecognizedOperation,
    RecognizedSensor,
    RecognizerOutput,
)

RECOGNIZERS: dict[str, type[DialectRecognizer]] = {}


def register_recognizer(name: str):
    def decorator(cls: type[DialectRecognizer]):
        RECOGNIZERS[name] = cls
        return cls
    return decorator


# Auto-load all recognizers on import
from discover_client.dialect.recognizers import flatdict, subtopic, tasmota, zigbee2mqtt, barevalue  # noqa: E402, F401

__all__ = [
    "DialectRecognizer",
    "RECOGNIZERS",
    "register_recognizer",
    "RecognizedOperation",
    "RecognizedSensor",
    "RecognizerOutput",
]
```

**Step 3: Write `utils.py` (shared helpers)**

```python
"""Shared helpers for dialect recognizers."""

from typing import Any

COMMAND_SUFFIXES = {
    "set": "set",
    "command": "command",
    "cmnd": "command",
    "ctrl": "control",
    "control": "control",
    "switch": "switch",
    "toggle": "toggle",
    "relay": "switch",
}
TELEMETRY_SUFFIXES = {"state", "status", "data", "telemetry", "sensor", "reading"}
ENUM_VALUES = {"on", "off", "toggle", "true", "false", "open", "close"}


def _topic_suffix(topic: str) -> str:
    return topic.rsplit("/", 1)[-1].strip().lower() if topic else ""


def _extract_accepted_values(payload: Any) -> list[str]:
    if isinstance(payload, str):
        val = payload.strip().upper()
        return [val] if val.lower() in ENUM_VALUES else []
    return []


def _coerce_value(payload: Any) -> Any:
    if isinstance(payload, str):
        stripped = payload.strip()
        try:
            return float(stripped)
        except ValueError:
            return stripped
    if isinstance(payload, (int, float)) and not isinstance(payload, bool):
        return float(payload)
    return payload


def _bare_value_confidence(topic: str, payload: Any) -> float:
    base = 0.10
    suffix = _topic_suffix(topic)
    if suffix in {"power", "state", "temperature", "humidity", "brightness", "set", "toggle"}:
        base += 0.30
    if isinstance(payload, str) and payload.strip().upper() in {"ON", "OFF", "TRUE", "FALSE", "TOGGLE", "OPEN", "CLOSE"}:
        base += 0.20
    if isinstance(payload, (int, float)) and not isinstance(payload, bool):
        base += 0.20
    if len(topic.split("/")) > 3:
        base += 0.10
    return min(base, 0.50)
```

**Step 4: Write `recognizers/__init__.py` (empty — just marker for auto-import)**

```python
"""Dialect recognizer plugins — auto-loaded by the registry."""
```

**Step 5: Verify imports**

Run: `uv run python -c "from discover_client.dialect import RECOGNIZERS; print('OK')"`
Expected: `ImportError` (recognizers not written yet — expected)

**Step 6: Commit**

```bash
git add discover_client/dialect/
git commit -m "feat(dialect): skeleton — ABC, registry, utils"
```

---

## Task 2: FlatDictRecognizer (existing mock_light format)

**Objective:** Port the current `MqttAnnotator` extraction logic into a recognizer. This format IS mock_light.

**Files:**
- Create: `discover_client/dialect/recognizers/flatdict.py`
- Create: `discover_client/dialect/tests/test_flatdict.py`

**Step 1: Write failing test**

```python
"""Tests for FlatDictRecognizer."""

from discover_client.dialect.recognizers.flatdict import FlatDictRecognizer
from discover_client.dialect.recognizer import RecognizedOperation, RecognizedSensor


def test_flatdict_matches_dict_payload() -> None:
    rec = FlatDictRecognizer()
    assert rec.match("mock/light-1/state", {"power": "OFF", "brightness": 100}) > 0
    assert rec.match("mock/light-1/set", {"power": "ON"}) > 0


def test_flatdict_rejects_non_dict() -> None:
    rec = FlatDictRecognizer()
    assert rec.match("mock/light-1/set", "ON") == 0


def test_flatdict_rejects_service_prefixes() -> None:
    rec = FlatDictRecognizer()
    assert rec.match("cmnd/light-1/Power", {"power": "ON"}) == 0
    assert rec.match("zigbee2mqtt/bulb/set", {"state": "ON"}) == 0
    assert rec.match("stat/light-1/POWER", {"POWER": "ON"}) == 0


def test_flatdict_extracts_operations_and_sensors() -> None:
    rec = FlatDictRecognizer()
    result = rec.extract("mock/light-1/state", {"power": "OFF", "brightness": 100})

    # Two operations: power and brightness
    ops = {(op.sensor_key, op.action) for op in result.operations}
    assert ops == {("power", "set"), ("brightness", "set")}

    # Two sensors: power (text) and brightness (numeric)
    sensors = {(s.sensor_type, type(s.value).__name__) for s in result.sensor_readings}
    assert sensors == {("power", "str"), ("brightness", "float")}


def test_flatdict_command_topic_creates_set_action() -> None:
    rec = FlatDictRecognizer()
    result = rec.extract("mock/light-1/set", {"power": "ON"})
    assert result.operations[0].action == "set"
    assert result.operations[0].accepted_values == ["ON"]


def test_flatdict_skips_metadata_keys() -> None:
    rec = FlatDictRecognizer()
    result = rec.extract("mock/light-1/state", {"_announce": True, "power": "OFF"})
    sensor_keys = {s.sensor_type for s in result.sensor_readings}
    assert "_announce" not in sensor_keys
    assert "power" in sensor_keys
```

**Step 2: Run test to verify failure**

Run: `uv run python -m pytest discover_client/dialect/tests/test_flatdict.py -v`
Expected: `ERROR — FlatDictRecognizer not found`

**Step 3: Write `flatdict.py`**

```python
"""Recognizer for single-topic + dict payload (mock_light format)."""

from typing import Any

from discover_client.dialect.recognizer import DialectRecognizer, RecognizerOutput, RecognizedOperation, RecognizedSensor
from discover_client.dialect.utils import _extract_accepted_values, _coerce_value, _topic_suffix, COMMAND_SUFFIXES

_SERVICE_PREFIXES = {"cmnd/", "stat/", "tele/", "zigbee2mqtt/", "homeassistant/"}
_METADATA_KEYS = {"_announce", "timestamp", "device", "type", "mac", "event"}


class FlatDictRecognizer(DialectRecognizer):
    SPECIFICITY = 40

    def match(self, topic: str, payload: Any) -> float:
        if not isinstance(payload, dict):
            return 0.0
        if any(topic.startswith(p) for p in _SERVICE_PREFIXES):
            return 0.0
        return 0.80  # high confidence for dict payloads

    def extract(self, topic: str, payload: Any) -> RecognizerOutput:
        operations = []
        sensors = []
        suffix = _topic_suffix(topic)
        action = COMMAND_SUFFIXES.get(suffix, "set")

        for key, val in payload.items():
            if key in _METADATA_KEYS:
                continue
            if isinstance(val, bool):
                continue

            operations.append(RecognizedOperation(
                topic=topic,
                action=action,
                sensor_key=key,
                accepted_values=_extract_accepted_values(val),
                is_enum=isinstance(val, str) and _extract_accepted_values(val) != [],
            ))

            sensors.append(RecognizedSensor(
                sensor_type=key,
                value=_coerce_value(val),
            ))

        return RecognizerOutput(
            operations=operations,
            sensor_readings=sensors,
            dialect_hint="flatdict",
        )
```

**Step 4: Run tests to verify pass**

Run: `uv run python -m pytest discover_client/dialect/tests/test_flatdict.py -v`
Expected: 6 passed

**Step 5: Commit**

```bash
git add discover_client/dialect/recognizers/flatdict.py discover_client/dialect/tests/
git commit -m "feat(dialect): FlatDictRecognizer — mock_light format"
```

---

## Task 3: SubTopicRecognizer

**Objective:** Recognizer for `mock/light-1/set/power` ← `"ON"` style.

**Files:**
- Create: `discover_client/dialect/recognizers/subtopic.py`
- Create: `discover_client/dialect/tests/test_subtopic.py`

**Step 1: Write test**

```python
from discover_client.dialect.recognizers.subtopic import SubTopicRecognizer


def test_subtopic_matches_bare_value_on_deep_topic() -> None:
    rec = SubTopicRecognizer()
    assert rec.match("mock/light-1/set/power", "ON") > 0


def test_subtopic_rejects_dict_payload() -> None:
    rec = SubTopicRecognizer()
    assert rec.match("mock/light-1/set/power", {"power": "ON"}) == 0


def test_subtopic_rejects_short_topic() -> None:
    rec = SubTopicRecognizer()
    assert rec.match("mock/set", "ON") == 0


def test_subtopic_extracts_sensor_from_last_segment() -> None:
    rec = SubTopicRecognizer()
    result = rec.extract("mock/light-1/set/power", "ON")
    assert result.operations[0].sensor_key == "power"
    assert result.operations[0].accepted_values == ["ON"]


def test_subtopic_extracts_brightness_numeric() -> None:
    rec = SubTopicRecognizer()
    result = rec.extract("mock/light-1/set/brightness", "50")
    assert result.sensor_readings[0].sensor_type == "brightness"
    assert result.sensor_readings[0].value == 50.0
```

**Step 2: Run to verify failure** → `uv run python -m pytest discover_client/dialect/tests/test_subtopic.py -v`

**Step 3: Write `subtopic.py`**

```python
class SubTopicRecognizer(DialectRecognizer):
    SPECIFICITY = 50

    def match(self, topic, payload):
        if isinstance(payload, dict):
            return 0.0
        segments = topic.split("/")
        if len(segments) < 3:
            return 0.0
        suffix = segments[-2].lower()
        return 0.85 if suffix in COMMAND_SUFFIXES else 0.0

    def extract(self, topic, payload):
        segments = topic.split("/")
        sensor_key = segments[-1].lower()
        action = COMMAND_SUFFIXES.get(segments[-2].lower(), "command")
        return RecognizerOutput(
            operations=[RecognizedOperation(
                topic=topic, action=action, sensor_key=sensor_key,
                accepted_values=_extract_accepted_values(payload),
                is_enum=isinstance(payload, str),
            )],
            sensor_readings=[RecognizedSensor(
                sensor_type=sensor_key,
                value=_coerce_value(payload),
            )],
            dialect_hint="subtopic",
        )
```

**Step 4: Run tests** → 5 passed

**Step 5: Commit**

---

## Task 4: TasmotaRecognizer

**Objective:** Recognizer for `cmnd/light-1/Power` ← `"ON"`.

**Files:**
- Create: `discover_client/dialect/recognizers/tasmota.py`
- Create: `discover_client/dialect/tests/test_tasmota.py`

**Step 1: Write test (7 cases)**

```python
def test_tasmota_matches_cmnd_prefix():
    rec = TasmotaRecognizer()
    assert rec.match("cmnd/light-1/Power", "ON") > 0

def test_tasmota_matches_stat_prefix():
    rec = TasmotaRecognizer()
    assert rec.match("stat/light-1/POWER", {"POWER": "ON"}) > 0

def test_tasmota_rejects_non_tasmota():
    rec = TasmotaRecognizer()
    assert rec.match("mock/light-1/set", "ON") == 0

def test_tasmota_extracts_power_command():
    rec = TasmotaRecognizer()
    result = rec.extract("cmnd/light-1/Power", "ON")
    assert result.operations[0].sensor_key == "power"
    assert result.operations[0].accepted_values == ["ON"]

def test_tasmota_dual_output_command_echo():
    """cmnd/Power ← "ON" should produce BOTH operation AND sensor reading."""
    rec = TasmotaRecognizer()
    result = rec.extract("cmnd/light-1/Power", "ON")
    assert len(result.operations) == 1
    assert len(result.sensor_readings) == 1
    assert result.sensor_readings[0].sensor_type == "power"
    assert result.sensor_readings[0].value == "ON"

def test_tasmota_dict_payload():
    rec = TasmotaRecognizer()
    result = rec.extract("stat/light-1/STATE", {"POWER": "ON", "Dimmer": 50})
    sensor_keys = {s.sensor_type for s in result.sensor_readings}
    assert "power" in sensor_keys
    assert "dimmer" in sensor_keys

def test_tasmota_skips_result_topic():
    rec = TasmotaRecognizer()
    result = rec.extract("stat/light-1/RESULT", {"POWER": "ON"})
    # RESULT topics are command echoes, not real state — produce only sensor
    assert len(result.operations) == 0
```

**Step 2: Run to verify failure**

**Step 3: Write `tasmota.py`**

```python
class TasmotaRecognizer(DialectRecognizer):
    SPECIFICITY = 90

    def match(self, topic, payload):
        return 0.92 if topic.startswith(("cmnd/", "stat/", "tele/")) else 0.0

    def extract(self, topic, payload):
        segments = topic.split("/")
        sensor_key = segments[-1] if len(segments) > 1 else "value"
        suffix = segments[-1].upper()
        # RESULT is command echo — no real operation
        is_result = suffix in {"RESULT", "STATUS"}

        ops, sensors = [], []

        if isinstance(payload, dict):
            for k, v in payload.items():
                ops.append(RecognizedOperation(
                    topic=topic, action="set", sensor_key=k.lower(),
                    accepted_values=_extract_accepted_values(v),
                    is_enum=isinstance(v, str),
                ))
                sensors.append(RecognizedSensor(sensor_type=k, value=_coerce_value(v)))
        elif not is_result:
            ops.append(RecognizedOperation(
                topic=topic, action="set", sensor_key=sensor_key.lower(),
                accepted_values=_extract_accepted_values(payload),
                is_enum=True,
            ))
            sensors.append(RecognizedSensor(sensor_type=sensor_key, value=_coerce_value(payload)))

        return RecognizerOutput(
            operations=ops,
            sensor_readings=sensors,
            dialect_hint="tasmota",
        )
```

**Step 4: Run tests** → 7 passed

**Step 5: Commit**

---

## Task 5: Zigbee2MqttRecognizer

**Objective:** Recognizer for `zigbee2mqtt/bulb/set` ← `{"state": "ON"}`.

**Files:**
- Create: `discover_client/dialect/recognizers/zigbee2mqtt.py`
- Create: `discover_client/dialect/tests/test_zigbee2mqtt.py`

**Test cases:** matches prefix, rejects non-z2m, extracts friendly_name, handles /bridge/ rejection, nested payload support.

**Step 3: Write recognizer — /bridge/ filter:** `match()` returns 0 if `/bridge/` in topic.

**Step 4: Run tests** → 5 passed

**Step 5: Commit**

---

## Task 6: BareValueRecognizer

**Objective:** Fallback recognizer — always matches, capped confidence 0.50.

**Files:**
- Create: `discover_client/dialect/recognizers/barevalue.py`
- Create: `discover_client/dialect/tests/test_barevalue.py`

**Test cases:** always returns >0, caps at 0.50, extracts enum payload, extracts generic "(no ops)" placeholder behavior.

**Step 4: Run tests** → 4 passed

**Step 5: Commit**

---

## Task 7: Normalizer functions

**Objective:** `to_canonical()` and `to_dialect()` with `DIALECT_MAPPINGS`.

**Files:**
- Create: `discover_client/dialect/normalizer.py`
- Create: `discover_client/dialect/tests/test_normalizer.py`

**Test cases:**
- `to_canonical("zigbee2mqtt", "state")` → `"power"`
- `to_canonical("tasmota", "POWER")` → `"power"`
- `to_canonical("flatdict", "power")` → `"power"` (pass-through)
- `to_dialect("zigbee2mqtt", "power")` → `"state"`
- `to_dialect("flatdict", "power")` → `"power"` (fallback)

**Commit**

---

## Task 8: Device identity extraction

**Objective:** Pure function `extract_device_id(dialect, topic)`.

**Files:**
- Create: `discover_client/dialect/identity.py`
- Create: `discover_client/dialect/tests/test_identity.py`

**Test cases:**
- Tasmota: `"cmnd/light-1/Power"` → `"light-1"`
- Zigbee2MQTT: `"zigbee2mqtt/bulb/set"` → `"bulb"`
- SubTopic: `"mock/light-1/set/power"` → `"mock/light-1"`
- FlatDict: `"mock/light-1/set"` → `"mock/light-1"`
- BareValue: `"some/topic"` → `"some"`

**Commit**

---

## Task 9: Aggregator

**Objective:** `aggregate(topic, payload)` — runs all recognizers, normalizes, deduplicates.

**Files:**
- Create: `discover_client/dialect/aggregator.py`
- Create: `discover_client/dialect/tests/test_aggregator.py`

**Test cases (8):**
1. `"mock/light-1/state", {"power":"OFF","brightness":100}` → primary=flatdict, 2 ops, 2 sensors
2. `"cmnd/light-1/Power", "ON"` → primary=tasmota, 1 op + 1 sensor
3. `"mock/light-1/set/power", "ON"` → primary=subtopic, sensor_key=power
4. `"zigbee2mqtt/bulb/set", {"state":"ON"}` → primary=zigbee2mqtt, sensor=power (normalized)
5. `"unknown/topic", 25.0` → primary=barevalue, sensor=value
6. FlatDict beats SubTopic when payload is dict (specificity tie, higher confidence)
7. BareValue never wins against specialized recognizers
8. Dedup: two recognizers produce same operation → only one kept

**Commit**

---

## Task 10: OperationCapability + sensor_key

**Objective:** Add `sensor_key` field, change `_capabilities` key to `(topic, sensor_key)`.

**Files:**
- Modify: `discover_client/operations/tracker.py`

**Step 1: Add `sensor_key` to `OperationCapability`**

Add `sensor_key: str | None = None` field.

**Step 2: Add `ingest_structured()` method**

```python
def ingest_structured(self, device_id: str, op: RecognizedOperation) -> list[OperationCapability] | None:
    by_topic = self._capabilities.setdefault(device_id, {})
    key = (op.topic, op.sensor_key or "")
    existing = by_topic.get(key)
    values = set(op.accepted_values)
    if existing is None:
        capability = OperationCapability(
            device_id=device_id, topic=op.topic, sensor_key=op.sensor_key,
            action=op.action, accepted_values=sorted(values),
            confidence=0.95, first_seen=time.time(), last_seen=time.time(),
        )
        by_topic[key] = capability
        return [replace(capability)]
    # merge ...
```

**Step 3: Fix `get_capabilities()` sort**

Change from sorting `caps.items()` (KeyError on tuple) to sorting by value fields:
```python
return [replace(c) for _, c in sorted(caps.items(), key=lambda kv: (kv[1].topic, kv[1].sensor_key or ""))]
```

**Step 4: Run existing tests** → all 36 pass

**Step 5: Commit**

---

## Task 11: DataSnapshot `ingest_structured()`

**Objective:** Accept pre-parsed `RecognizedSensor` directly.

**Files:**
- Modify: `discover_client/identification/data_snapshot.py`
- Modify: `discover_client/identification/test_data_snapshot.py`

**Step 1: Add method**

```python
def ingest_structured(self, device_id: str, sensor: RecognizedSensor, timestamp: float | None = None) -> list[SensorReading] | None:
    ts = timestamp or time.time()
    self._latest_timestamp = max(self._latest_timestamp, ts)
    value = 0.0
    text_value = None
    if isinstance(sensor.value, (int, float)) and not isinstance(sensor.value, bool):
        value = float(sensor.value)
    elif isinstance(sensor.value, str):
        text_value = sensor.value.strip()
    reading = SensorReading(sensor_type=sensor.sensor_type, value=value, unit=sensor.unit, timestamp=ts, text_value=text_value)
    device_readings = self._readings.setdefault(device_id, {})
    device_readings.setdefault(sensor.sensor_type, []).append(reading)
    self._prune_device(device_id, now=ts)
    return [reading]
```

**Step 2: Add test**

```python
def test_ingest_structured_recognized_sensor():
    from discover_client.dialect.recognizer import RecognizedSensor
    ds = DataSnapshot()
    readings = ds.ingest_structured("dev-1", RecognizedSensor(sensor_type="power", value="OFF"), timestamp=100.0)
    assert readings[0].sensor_type == "power"
    assert readings[0].text_value == "OFF"
```

**Step 3: Run all** → 37 passed (4 existing + 1 new)

**Step 4: Commit**

---

## Task 12: Worker integration (dialect pipeline)

**Objective:** Hook `aggregate()` into `worker.py._async_main()` → `on_event()`.

**Files:**
- Modify: `discover_client/gui/worker.py`
- Modify: `discover_client/identification/evidence.py`

**Step 1: Add fields to `SignalEvidence`**

```python
dialect: str | None = None
dialect_confidence: float = 0.0
canonical_sensor_keys: dict[str, Any] | None = None
is_meta_message: bool = False
```

**Step 2: Add `_build_evidence_from_aggregated()` to worker.py**

```python
@staticmethod
def _build_evidence_from_aggregated(agg: AggregatedOutput, timestamp: float, source_id: str) -> SignalEvidence:
    return SignalEvidence(
        source_id=source_id, source_type="mqtt", timestamp=timestamp,
        mqtt_topic=agg.device_id, topic_prefix=agg.device_id,
        ip_address="", hostname="", mac_prefix="",
        mqtt_payload_keys=set(op.sensor_key for op in agg.operations if op.sensor_key) | {s.sensor_type for s in agg.sensor_readings},
        mqtt_payload=agg.sensor_readings[0].value if agg.sensor_readings else None,
    )
```

**Step 3: Hook into `on_event()`**

In `on_event()`, BEFORE the annotator+classifier block:
```python
if event.source_type == "mqtt" and event.event_type == "data":
    from discover_client.dialect import RECOGNIZERS
    from discover_client.dialect.aggregator import aggregate
    if RECOGNIZERS:  # only when recognizers loaded
        topic = str(event.payload.get("topic", ""))
        value = event.payload.get("value")
        aggregated = aggregate(topic, value)
        if aggregated:
            for op in aggregated.operations:
                self._ops_tracker.ingest_structured(aggregated.device_id, op)
            for sensor in aggregated.sensor_readings:
                self.data_snapshot.ingest_structured(aggregated.device_id, sensor)
            evidence = self._build_evidence_from_aggregated(aggregated, event.timestamp, event.source_id)
            self.evidence_produced.emit(evidence)
            if self.deduplicator:
                device = self.deduplicator.ingest(evidence)
                # ... rest of dedup/profile update ...
            return  # skip old annotator path for this event
```

**Step 4: Verify** — start mosquitto + mock_light + GUI, check Device Profiles shows power + brightness

**Step 5: Run all tests** → 36 existing still pass

**Step 6: Commit**

---

## Task 13: DeviceProfileCard `_op_key` upgrade

**Objective:** `_op_key` → `topic::action::sensor_key` so multi-sensor operations show separate rows.

**Files:**
- Modify: `discover_client/gui/device_profile_page.py`

**Step 1: Update `_op_key`**

```python
def _op_key(operation: dict) -> str:
    topic = str(operation.get("topic") or "")
    action = str(operation.get("action") or "")
    sensor = str(operation.get("sensor_key") or "")
    return f"{topic}::{action}::{sensor}"
```

**Step 2: Update `_build_device_profiles` in worker.py**

Add `"sensor_key": capability.sensor_key` to the operation dict.

**Step 3: Update `_infer_operation_args`** — already done, verify it filters by `sensor_key`

**Step 4: Run tests** → GUI tests pass

**Step 5: Commit**

---

## Task 14: End-to-end smoke test

**Objective:** One script that exercises all 5 dialects.

**Files:**
- Create: `discover_client/dialect/tests/test_e2e.py`

```python
from discover_client.dialect import RECOGNIZERS
from discover_client.dialect.aggregator import aggregate

def test_all_recognizers_registered():
    assert len(RECOGNIZERS) == 5

def test_flatdict_aggregated():
    result = aggregate("mock/light-1/state", {"power": "OFF", "brightness": 100})
    assert result is not None
    assert result.dialect == "flatdict"
    assert len(result.sensor_readings) == 2

def test_tasmota_aggregated():
    result = aggregate("cmnd/light-1/Power", "ON")
    assert result is not None
    assert result.dialect == "tasmota"
    assert result.operations[0].sensor_key == "power"

def test_subtopic_aggregated():
    result = aggregate("mock/light-1/set/power", "ON")
    assert result is not None
    assert result.dialect == "subtopic"

def test_zigbee2mqtt_aggregated():
    result = aggregate("zigbee2mqtt/bulb", {"state": "ON"})
    assert result is not None
    assert result.dialect == "zigbee2mqtt"

def test_barevalue_aggregated():
    result = aggregate("unknown/topic", "ON")
    assert result is not None
    assert result.dialect == "barevalue"
```

**Step 2: Run** → 6 passed

**Step 3: Commit**

---

## Task 15: Cleanup — remove old code paths

**Objective:** Delete `TopicClassifier.classify()` and `tracker._classify()` (now replaced by dialect pipeline).

**Files:**
- Modify: `discover_client/identification/classifier.py` → add deprecation comment
- Modify: `discover_client/operations/tracker.py` → remove `_classify()`, `_topic_suffix()`, `_extract_action()`, `_extract_values()`, `_normalize_enum()`, `_has_numeric_value_with_unit()` (now in `dialect/utils.py`)

**Run all tests** → 36 pass (classifier tests removed)

**Commit**

---

## Summary

| # | Task | Files | Time |
|---|---|---|---|
| 1 | Skeleton | 4 new | 5 min |
| 2 | FlatDictRecognizer | 2 new | 10 min |
| 3 | SubTopicRecognizer | 2 new | 8 min |
| 4 | TasmotaRecognizer | 2 new | 10 min |
| 5 | Zigbee2MqttRecognizer | 2 new | 10 min |
| 6 | BareValueRecognizer | 2 new | 8 min |
| 7 | Normalizer | 2 new | 5 min |
| 8 | DeviceIdentityExtractor | 2 new | 5 min |
| 9 | Aggregator | 2 new | 10 min |
| 10 | OperationCapability +sensor_key | 1 modify | 8 min |
| 11 | DataSnapshot ingest_structured | 1 modify | 5 min |
| 12 | Worker integration | 2 modify | 10 min |
| 13 | DeviceProfileCard _op_key | 2 modify | 5 min |
| 14 | E2E smoke test | 1 new | 5 min |
| 15 | Cleanup | 2 modify | 5 min |

**Total: ~1.5 hours, 37 new files, 15 commits**

Always TDD: write test → verify failure → implement → verify pass → commit.
