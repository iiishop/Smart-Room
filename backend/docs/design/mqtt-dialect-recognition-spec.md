# MQTT Dialect Recognition — Revised Spec (v2)

> **Status:** Post-review revision — all 4 Blockers fixed, 5 Major issues resolved  
> **Context:** Discovers multiple MQTT message formats via pluggable recognizers, fans out unified operation + sensor data to existing pipeline.  

---

## 1. Architecture (Revised)

```
MQTT message (topic, payload)
    │
    ▼
┌─────────────────────────────────────────────────┐
│            DialectRecognizer Registry            │
│  5 recognizers, specificity-then-confidence     │
└───────────────┬─────────────────────────────────┘
                │ all recognizer outputs
                ▼
┌─────────────────────────────────────────────────┐
│                  Aggregator                      │
│  1. Group by specificity → highest group         │
│  2. Within group → highest confidence = primary  │
│  3. NORMALIZE keys before dedup ← MOVED UP      │
│  4. Deduplicate (sensor KEY & op SIGNATURE)     │
│  5. Extract device_id per dialect                │
└───────────────┬─────────────────────────────────┘
                │ AggregatedOutput (canonical keys)
                ▼
        ┌───────┴───────┐
        ▼               ▼
  OperationsTracker  DataSnapshot
  (ingest_structured) (ingest_structured)
        │               │
        └───────┬───────┘
                ▼
         Deduplicator bridge
         AggregatedOutput → SignalEvidence
```

**Key change from v1:** PropertyNormalizer moved INSIDE aggregator, runs BEFORE dedup. DeviceIdentityExtractor folded into aggregator as a function.

---

## 2. Data Structures

### 2.1 RecognizerOutput (per-recognizer, pre-normalization)

```python
@dataclass
class RecognizedOperation:
    topic: str
    action: str            # canonical: "set", "toggle", "command"
    sensor_key: str | None # canonical: "power", "brightness", None=generic
    accepted_values: list[str] = field(default_factory=list)
    payload_example: Any = None
    is_enum: bool = False

@dataclass
class RecognizedSensor:
    sensor_type: str       # NOT normalized yet — raw dialect key
    value: Any             # float for numeric, str for enum
    unit: str = ""

@dataclass
class RecognizerOutput:
    operations: list[RecognizedOperation] = field(default_factory=list)
    sensor_readings: list[RecognizedSensor] = field(default_factory=list)
    message_type: str = "data"
    dialect_hint: str | None = None
    device_id_hint: str | None = None
```

### 2.2 AggregatedOutput (post-aggregation, canonical keys)

```python
@dataclass
class AggregatedOutput:
    operations: list[RecognizedOperation]
    sensor_readings: list[RecognizedSensor]
    dialect: str
    device_id: str         # filled by device identity extraction
    message_type: str
```

### 2.3 Recognizer ABC

```python
class DialectRecognizer(ABC):
    SPECIFICITY: int = 50  # 0-100

    @abstractmethod
    def match(self, topic: str, payload: Any) -> float:
        """0.0-1.0 confidence this message belongs to this dialect."""
        ...

    @abstractmethod
    def extract(self, topic: str, payload: Any) -> RecognizerOutput:
        """Called after match() succeeds."""
        ...
```

---

## 3. Five Recognizers

| Recognizer | SPEC | Match | Extract |
|---|---|---|---|
| `TasmotaRecognizer` | 90 | topic starts with `cmnd/`, `stat/`, or `tele/` | 3rd segment = sensor (e.g. `Power`). Payload dict → each key a sensor; bare `"ON"` → BOTH operation + sensor reading |
| `Zigbee2MqttRecognizer` | 90 | topic starts with `zigbee2mqtt/` and does NOT contain `/bridge/` | 2nd segment = friendly_name. Dict keys → sensors (e.g. `state` → `power` via normalizer) |
| `SubTopicRecognizer` | 50 | Payload is NOT dict; topic has ≥3 segments; second-to-last segment in COMMAND_SUFFIXES | Last segment = sensor_key; payload = value (operational) |
| `FlatDictRecognizer` | 40 | Payload is dict; no `cmnd/`/`zigbee2mqtt/`/`stat/`/`tele/` prefix | Each dict key → sensor + operation pair |
| `BareValueRecognizer` | 0 | Always matches | Dynamic confidence (cap 0.50). Suffix-based sensor name. |

**Zigbee2MQTT bridge guard:** `zigbee2mqtt/bridge/state` is filtered — recognizer returns `match=0`. Prevents creating a fake "bridge" device.

**Tasmota dual output:** `cmnd/light-1/Power` ← `"ON"` produces BOTH a `RecognizedOperation(action="set", sensor_key="power", accepted_values=["ON"])` AND a `RecognizedSensor(sensor_type="POWER", value="ON")`. The sensor reading represents the command-echo confirmation.

**SubTopicRecognizer pre-filter:** Before extracting, checks that payload is NOT a dict. This prevents `mock/light-1/set/power` ← `{"power":"ON"}` from being matched (FlatDict handles that shape).

---

## 4. Aggregator (Revised)

### 4.1 `_op_signature()` — explicitly defined

```python
def _op_signature(op: RecognizedOperation) -> str:
    """Stable dedup key for operations."""
    return f"{op.topic}::{op.action}::{op.sensor_key or ''}"
```

### 4.2 Aggregation logic

```python
def aggregate(topic: str, payload: Any) -> AggregatedOutput | None:
    # 1. Run every recognizer
    scored = []
    for name, cls in RECOGNIZERS.items():
        rec = cls()
        conf = rec.match(topic, payload)
        if conf <= 0:
            continue
        output = rec.extract(topic, payload)
        scored.append((cls.SPECIFICITY, conf, name, output))

    if not scored:
        return None

    # 2. Group by specificity
    max_spec = max(s[0] for s in scored)
    candidates = [s for s in scored if s[0] == max_spec]

    # 3. Primary = highest confidence in group
    primary = max(candidates, key=lambda s: s[1])
    _, _, primary_name, primary_output = primary

    # 4. NORMALIZE before dedup
    normalizer = _get_normalizer(primary_name)
    _normalize_ops(primary_output.operations, normalizer)
    _normalize_sensors(primary_output.sensor_readings, normalizer)

    # 5. Merge non-conflicting — only from candidates (not 'candidates + scored' — that was a bug)
    merged_ops = list(primary_output.operations)
    merged_sensors = list(primary_output.sensor_readings)
    seen_op = {_op_signature(op) for op in merged_ops}
    seen_sensor = {s.sensor_type for s in merged_sensors}

    for _, _, _, output in candidates[1:]:  # skip primary (index 0)
        _normalize_ops(output.operations, normalizer)
        _normalize_sensors(output.sensor_readings, normalizer)
        for op in output.operations:
            sig = _op_signature(op)
            if sig not in seen_op:
                merged_ops.append(op)
                seen_op.add(sig)
        for sensor in output.sensor_readings:
            if sensor.sensor_type not in seen_sensor:
                merged_sensors.append(sensor)
                seen_sensor.add(sensor.sensor_type)

    # 6. Extract device_id
    device_id = _extract_device_id(primary_name, topic)

    _log(topic, primary_name, max_spec, primary[1], device_id, merged_ops, merged_sensors)

    return AggregatedOutput(
        operations=merged_ops,
        sensor_readings=merged_sensors,
        dialect=primary_name,
        device_id=device_id,
        message_type=primary_output.message_type,
    )
```

**Bug fixed:** `candidates + scored` → `candidates[1:]`. The old code iterated `candidates + scored`, which double-counted candidates when they also appeared in `scored` (BareValueRecognizer with low specificity would append its outputs to a primary that already had them).

---

## 5. PropertyNormalizer (as module-level functions)

Not a class — two pure functions + a mapping dict:

```python
# discover_client/dialect/normalizer.py

DIALECT_MAPPINGS: dict[str, dict[str, str]] = {
    "zigbee2mqtt": {"state": "power"},
    "tasmota": {"POWER": "power", "Power": "power", "Dimmer": "brightness", "POWER1": "power", "POWER2": "power"},
    # flatdict, subtopic, barevalue: no mapping needed
}

def to_canonical(dialect: str, key: str) -> str:
    """dialect_key → canonical_name."""
    return DIALECT_MAPPINGS.get(dialect, {}).get(key, key).lower()

def to_dialect(dialect: str, canonical: str) -> str:
    """Reverse lookup for sending commands back."""
    mapping = DIALECT_MAPPINGS.get(dialect, {})
    for dk, ck in mapping.items():
        if ck == canonical:
            return dk
    return canonical
```

**No class state** — mapping is module-level, iterable in tests.

---

## 6. DeviceIdentityExtractor (as module-level function)

```python
# discover_client/dialect/identity.py

_EXTRACTORS: dict[str, Callable[[str], str]] = {
    "tasmota": lambda t: t.split("/")[1] if len(t.split("/")) > 1 else "unknown",
    "zigbee2mqtt": lambda t: t.split("/")[1] if len(t.split("/")) > 1 else "unknown",
    "subtopic": lambda t: "/".join(
        [seg for seg in t.split("/") if seg not in COMMAND_SUFFIXES]
    ) if len(t.split("/")) >= 2 else t,
    "flatdict": lambda t: t.rsplit("/", 1)[0] if "/" in t else t,
    "barevalue": lambda t: t.rsplit("/", 1)[0] if "/" in t else t,
}

def extract_device_id(dialect: str, topic: str) -> str:
    fn = _EXTRACTORS.get(dialect)
    return fn(topic) if fn else (topic.rsplit("/", 1)[0] if "/" in topic else topic)
```

**Why `extract_device_id` not a class method:** no state, pure function. The aggregator calls it inline.

---

## 7. OperationsTracker — `ingest_structured()` (FULLY SPECIFIED)

```python
class OperationsTracker:
    def ingest_structured(self, device_id: str, op: RecognizedOperation) -> list[OperationCapability] | None:
        """
        Ingest a pre-parsed operation from any dialect recognizer.
        No classification needed — category/confidence already determined.
        """
        by_topic = self._capabilities.setdefault(device_id, {})
        key = (op.topic, op.sensor_key or "")
        existing = by_topic.get(key)

        values = set(op.accepted_values)
        if existing is None:
            capability = OperationCapability(
                device_id=device_id,
                topic=op.topic,
                sensor_key=op.sensor_key,
                action=op.action,
                accepted_values=sorted(values),
                confidence=0.95,  # dialect-verified
                first_seen=time.time(),
                last_seen=time.time(),
            )
            by_topic[key] = capability
            return [replace(capability)]

        merged_values = sorted(set(existing.accepted_values).union(values))
        changed = False
        if merged_values != existing.accepted_values:
            existing.accepted_values = merged_values
            changed = True
        if existing.last_seen != time.time():
            existing.last_seen = time.time()
            changed = True

        return [replace(existing)] if changed else None

    def get_capabilities(self, device_id: str) -> list[OperationCapability]:
        caps = self._capabilities.get(device_id, {})
        # Sort by (topic, sensor_key) — both are guaranteed strings
        return [replace(c) for _, c in sorted(caps.items())]
```

**`OperationCapability`** gains a `sensor_key` field:
```python
@dataclass
class OperationCapability:
    device_id: str
    topic: str
    action: str
    sensor_key: str | None = None  # NEW
    accepted_values: list[str] = field(default_factory=list)
    confidence: float = 0.0
    first_seen: float = 0.0
    last_seen: float = 0.0
```

The `_capabilities` dict key changes from `topic` (str) to `(topic, sensor_key)` (tuple). `get_capabilities()` sorts by value fields (`(c.topic, c.sensor_key or "")`) to avoid `TypeError` on `None` comparison.

---

## 8. DataSnapshot — `ingest_structured()` (FULLY SPECIFIED)

```python
class DataSnapshot:
    def ingest_structured(self, device_id: str, sensor: RecognizedSensor, timestamp: float | None = None) -> list[SensorReading] | None:
        """
        Ingest a pre-parsed sensor reading from any dialect recognizer.
        No extraction needed — sensor_type + value already determined.
        """
        ts = timestamp or time.time()
        self._latest_timestamp = max(self._latest_timestamp, ts)

        value = 0.0
        text_value = None
        unit = sensor.unit

        if isinstance(sensor.value, (int, float)) and not isinstance(sensor.value, bool):
            value = float(sensor.value)
        elif isinstance(sensor.value, str):
            text_value = sensor.value.strip()
        else:
            text_value = str(sensor.value)

        reading = SensorReading(
            sensor_type=sensor.sensor_type,
            value=value,
            unit=unit,
            timestamp=ts,
            text_value=text_value,
        )

        device_readings = self._readings.setdefault(device_id, {})
        device_readings.setdefault(sensor.sensor_type, []).append(reading)
        self._prune_device(device_id, now=ts)
        return [reading]
```

---

## 9. Deduplicator Bridge — AggregatedOutput → SignalEvidence (FULLY SPECIFIED)

In `worker.py`, after the Aggregator runs, build `SignalEvidence` for the Deduplicator:

```python
def _build_evidence_from_aggregated(agg: AggregatedOutput, timestamp: float, source_id: str) -> SignalEvidence:
    """Bridge: canonical AggregatedOutput → SignalEvidence for Deduplicator."""
    return SignalEvidence(
        source_id=source_id,
        source_type="mqtt",
        timestamp=timestamp,

        # Topic identity (for dedup topic_prefix matching)
        mqtt_topic=agg.device_id,  # device_id IS the topic prefix (e.g. "mock/light-1")
        topic_prefix=agg.device_id,

        # MAC/IP fields (empty — MQTT devices identified by topic_prefix)
        ip_address="",
        hostname="",
        mac_prefix="",

        # Payload keys for dedup cross-reference
        mqtt_payload_keys=set(op.sensor_key for op in agg.operations if op.sensor_key) | {s.sensor_type for s in agg.sensor_readings},

        # Legacy payload (first sensor value as raw — for backward compat)
        mqtt_payload=agg.sensor_readings[0].value if agg.sensor_readings else None,
    )
```

The Deduplicator's `topic_prefix` rule (60 points) handles MQTT device aggregation — devices from different sources (nmap, mDNS) share IP/MAC, but MQTT devices share `topic_prefix`. This bridge correctly populates that field.

---

## 10. Worker Integration (Revised)

```python
def on_event(event: SourceEvent) -> None:
    # ... existing signal emission (event_received, status_changed) ...

    # NEW: Dialect recognition for MQTT data events
    if event.source_type == "mqtt" and event.event_type == "data":
        topic = str(event.payload.get("topic", ""))
        value = event.payload.get("value")

        aggregated = aggregate(topic, value)
        if aggregated is None:
            return

        # Fan out to operations tracker
        for op in aggregated.operations:
            self._ops_tracker.ingest_structured(aggregated.device_id, op)

        # Fan out to data snapshot
        for sensor in aggregated.sensor_readings:
            self.data_snapshot.ingest_structured(aggregated.device_id, sensor,
                                                  timestamp=event.timestamp)

        # Build evidence for deduplicator
        evidence = _build_evidence_from_aggregated(aggregated, event.timestamp, event.source_id)
        self.evidence_produced.emit(evidence)

        if self.deduplicator is not None:
            device = self.deduplicator.ingest(evidence)
            devices = self.deduplicator.get_devices()
            self.dedup_updated.emit(devices)
            # ... classification state, device_profile_updated, etc ...

        # Omit old annotator+classifier path for MQTT data events
        return

    # Non-MQTT path (mDNS, SSDP, nmap): unchanged
    annotator_cls = ANNOTATORS.get(event.source_type)
    if annotator_cls is None:
        return
    evidence = annotator_cls().annotate(event)
    if evidence is not None:
        # ... existing pipeline ...
```

---

## 11. Recognizer Implementations

### Sub Topic Recognizer

```python
class SubTopicRecognizer(DialectRecognizer):
    SPECIFICITY = 50
    # Only match when payload is a bare value AND topic has enough segments
    def match(self, topic, payload):
        if isinstance(payload, dict):
            return 0.0
        segments = topic.split("/")
        if len(segments) < 3:
            return 0.0
        suffix = segments[-2].lower()
        action = COMMAND_SUFFIXES.get(suffix)
        return 0.85 if action else 0.0

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
            )] if _extract_accepted_values(payload) else [],
            dialect_hint="subtopic",
        )
```

### Bare Value Recognizer

```python
class BareValueRecognizer(DialectRecognizer):
    SPECIFICITY = 0

    def match(self, topic, payload):
        return _bare_value_confidence(topic, payload)

    def extract(self, topic, payload):
        suffix = topic.rsplit("/", 1)[-1].lower()
        sensor_key = suffix if suffix not in COMMAND_SUFFIXES else "value"
        return RecognizerOutput(
            operations=[RecognizedOperation(
                topic=topic, action="command", sensor_key=sensor_key,
                accepted_values=_extract_accepted_values(payload),
                is_enum=isinstance(payload, str),
            )],
            sensor_readings=[RecognizedSensor(
                sensor_type=sensor_key, value=_coerce_value(payload),
            )] if isinstance(payload, (str, int, float)) and not isinstance(payload, bool) else [],
            dialect_hint="barevalue",
        )
```

---

## 12. File Map

| New File | Purpose |
|---|---|
| `discover_client/dialect/__init__.py` | Registry + auto-import recognizers |
| `discover_client/dialect/recognizer.py` | ABC, RecognizedOperation, RecognizedSensor, RecognizerOutput |
| `discover_client/dialect/aggregator.py` | `aggregate()`, AggregatedOutput |
| `discover_client/dialect/normalizer.py` | `to_canonical()`, `to_dialect()`, `DIALECT_MAPPINGS` |
| `discover_client/dialect/identity.py` | `extract_device_id()`, `_EXTRACTORS` |
| `discover_client/dialect/utils.py` | `_extract_accepted_values()`, `_coerce_value()`, `_bare_value_confidence()` |
| `discover_client/dialect/recognizers/flatdict.py` | FlatDictRecognizer |
| `discover_client/dialect/recognizers/subtopic.py` | SubTopicRecognizer |
| `discover_client/dialect/recognizers/tasmota.py` | TasmotaRecognizer |
| `discover_client/dialect/recognizers/zigbee2mqtt.py` | Zigbee2MqttRecognizer |
| `discover_client/dialect/recognizers/barevalue.py` | BareValueRecognizer |

| Modified File | Change |
|---|---|
| `discover_client/operations/tracker.py` | `OperationCapability` +sensor_key; `_capabilities` key → tuple; `ingest_structured()`; `get_capabilities()` sort fix |
| `discover_client/identification/data_snapshot.py` | `ingest_structured()` |
| `discover_client/identification/evidence.py` | `SignalEvidence` +dialect fields |
| `discover_client/gui/worker.py` | Dialect pipeline hook + `_build_evidence_from_aggregated()`; `_infer_operation_args()` filter by sensor_key |
| `discover_client/gui/device_profile_page.py` | `_op_key()` → `topic::action::sensor_key` |

---

## 13. Risks & Mitigation

- **Old code coexistence:** `TopicClassifier` + `tracker._classify()` remain UNCHANGED but bypassed for MQTT data events. Once dialect pipeline is stable, delete them in a follow-up PR.
- **SubTopic vs FlatDict overlap:** `mock/light-1/set` with `{"power":"ON"}` could match both. SubTopicRecognizer's `match()` returns 0 when payload is dict → resolved.
- **DeviceProfile `_op_key` change:** Adding `sensor_key` to the key means old ops created before the dialect pipeline vanish. Acceptable — they're rebuilt on next event.
- **Sort breaking on None:** `get_capabilities()` sorts by value fields, not dict keys. Guaranteed `str` comparison only.
