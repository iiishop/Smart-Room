# Task 09 — Data Pipeline + Snapshot

## Scope
实现数据管线——从 MQTT data 事件提取传感器数值，按去重后的 Device 归堆，维护实时数据快照，并在 GUI 新增 Data 页面展示。

## Current state

- Deduplicator 输出 `list[Device]`，每个 Device 聚合了 `topic_prefixes`, `payload_keys`, `mac_addresses` 等
- Worker 的 `on_event` 已能收到所有 SourceEvent
- GUI 已有 Source&Log / Evidence / Dedup 三页

## Files to create

- `discover_client/identification/data_snapshot.py` — DataSnapshot class
- `discover_client/gui/data_page.py` — GUI Data page
- Update `discover_client/gui/worker.py` — wire data events to snapshot
- Update `discover_client/gui/main_window.py` — add Data page tab

## Task 9.1 — DataSnapshot

```python
@dataclass
class SensorReading:
    sensor_type: str       # "temperature", "humidity"
    value: float
    unit: str              # "C", "%"
    timestamp: float

class DataSnapshot:
    """Per-device recent sensor readings."""
    
    def __init__(self, retention_s: float = 60):
        self._readings: dict[str, dict[str, list[SensorReading]]] = {}
        # {device_id: {sensor_type: [reading, ...]}}
    
    def ingest(self, device_id: str, event: dict) -> list[SensorReading] | None:
        """
        Try to extract sensor readings from a MQTT data event.
        event = {topic: "govee/.../temperature", value: {unit: "C", value: 23.5}}
        Returns list of extracted readings or None.
        """
        ...
    
    def get_latest(self, device_id: str) -> dict[str, SensorReading]:
        """Return {sensor_type: latest_reading} for a device."""
        ...
    
    def get_all(self) -> dict[str, dict[str, list[SensorReading]]]:
        """Return all readings grouped by device."""
        ...
```

### Value extraction logic

MQTT data payloads come in unpredictable shapes. Try these strategies in order:

1. **Dict with numeric "value" key**: `{"value": 23.5, "unit": "C"}` → 23.5, unit="C"
2. **Dict with one numeric value**: `{"temperature": 23.5}` → 23.5, unit=""
3. **Dict, find first numeric leaf**: `{"data": {"temp_c": 23.5}}` → 23.5
4. **Plain string that looks numeric**: `"23.5"` → 23.5

If none work, skip the event — it's not sensor data.

### Sensor type inference

From the **last topic segment** (e.g. `govee/H5179/abc/temperature` → "temperature").
If the last segment is not meaningful (e.g. "state", "data"), try the payload key.

### Per-device association

Match the event's topic_prefix against each device's `topic_prefixes`. 
If a device has `topic_prefixes = {"govee/H5179/a1b2c3d4e5f6"}` and the event's topic is `govee/H5179/a1b2c3d4e5f6/temperature` → it matches.

## Task 9.2 — Worker wiring

In `on_event`, after deduplicator.ingest(evidence):

```python
# Data pipeline: extract sensor readings
if event.event_type == "data":
    data_event = {"topic": event.payload.get("topic", ""), "value": event.payload.get("value")}
    # Find which device this event belongs to
    for device in self.deduplicator.get_devices():
        for prefix in device.topic_prefixes:
            if data_event["topic"].startswith(prefix):
                readings = self.data_snapshot.ingest(device.device_id, data_event)
                if readings:
                    self.data_updated.emit(self.data_snapshot.get_all())
                break
```

Add `data_updated = Signal(dict)` to Worker.

## Task 9.3 — GUI Data page

New tab "Data" showing a tree or grouped table:

```
Device          Sensor       Value     Unit    Last Updated
─────────────────────────────────────────────────────────
device-1        temperature  23.5       C      22:18:03
device-1        humidity     60.3       %      22:18:04
device-3        (no data)    —          —      —
device-4        (no data)    —          —      —
```

Or simpler: a table with columns Device ID, Sensor, Latest Value, Unit.

## Task 9.4 — Verification

Run this Python script:

```python
from discover_client.identification.data_snapshot import DataSnapshot

ds = DataSnapshot()

# Mock a MQTT temperature event
readings = ds.ingest("device-1", {
    "topic": "govee/H5179/a1b2c3d4e5f6/temperature",
    "value": {"unit": "C", "value": 23.5}
})
assert readings is not None
assert len(readings) == 1
assert readings[0].sensor_type == "temperature"
assert readings[0].value == 23.5
assert readings[0].unit == "C"

# Mock a humidity event
ds.ingest("device-1", {
    "topic": "govee/H5179/a1b2c3d4e5f6/humidity",
    "value": {"unit": "%", "value": 60.3}
})

# Get latest
latest = ds.get_latest("device-1")
assert latest["temperature"].value == 23.5
assert latest["humidity"].value == 60.3

# Get all
all_data = ds.get_all()
assert "device-1" in all_data
assert len(all_data["device-1"]) == 2

# Alternative payload format
readings2 = ds.ingest("device-2", {
    "topic": "zigbee/0x00158d/temperature",
    "value": 25.0
})
assert readings2 is not None
assert readings2[0].value == 25.0

print("All data snapshot checks passed")
```

## Anti-patterns to avoid

- Do NOT assume MQTT payload always has `{"value": N, "unit": "X"}` shape — try multiple extraction strategies
- Do NOT crash on non-sensor events (status messages, discovery events)
- Do NOT associate data with device before deduplicator has assigned a device_id
- Keep only recent readings (60s retention) to avoid unbounded memory growth
