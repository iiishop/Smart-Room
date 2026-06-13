"""Background worker that runs DiscoverClient's asyncio loop."""

import asyncio
import json
import threading
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal

from discover_client.client import DiscoverClient
from discover_client.gui.device_profile_page import DeviceProfile
from discover_client.identification import ANNOTATORS, DataSnapshot, Deduplicator, FeatureExtractor, TopicClassifier
from discover_client.identification.evidence import SignalEvidence
from discover_client.operations import OperationsTracker
from discover_client.source import SourceEvent
from discover_client.config import load_config


@dataclass
class _DeviceClassificationState:
    scores: dict[str, float] = field(default_factory=lambda: {"telemetry": 0.0, "command": 0.0, "unknown": 0.0})
    samples: int = 0

    def update(self, label: str, confidence: float) -> None:
        self.samples += 1
        self.scores[label] = self.scores.get(label, 0.0) + confidence

    def summary(self) -> tuple[str, float]:
        label = max(self.scores, key=self.scores.get)
        total = sum(self.scores.values())
        confidence = 0.0 if total <= 0 else self.scores[label] / total
        return label, confidence


class Worker(QObject):
    """Runs DiscoverClient on a background thread, bridges events to Qt."""

    event_received = Signal(str, str, float, str, dict)
    evidence_produced = Signal(object)
    device_classified = Signal(str, object)
    dedup_updated = Signal(list)
    device_profile_updated = Signal(list)
    features_updated = Signal(list)
    data_updated = Signal(dict)
    operations_updated = Signal(list)
    status_changed = Signal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._client: DiscoverClient | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self.deduplicator: Deduplicator | None = None
        self.feature_extractor: FeatureExtractor | None = None
        self.data_snapshot: DataSnapshot | None = None
        self._ops_tracker: OperationsTracker | None = None
        self._classifier: TopicClassifier | None = None
        self._classification_state: dict[str, _DeviceClassificationState] = {}

        # ── throttle: batch UI signals to one flush every 200ms ──
        self._throttle_interval: float = 0.2
        self._flush_scheduled: bool = False
        self._pending_devices: list = []
        self._pending_device_ids: list[str] = []

    def _schedule_flush(self, devices: list, device_id: str) -> None:
        """Accumulate state and schedule a deferred signal flush.

        Data ingestion happens immediately for every event — only Qt signal
        emissions are batched.  If a flush is already pending we just update
        the latest device list; the pending timer will pick it up.
        """
        self._pending_devices = devices
        if device_id not in self._pending_device_ids:
            self._pending_device_ids.append(device_id)

        if self._flush_scheduled:
            return

        self._flush_scheduled = True
        loop = asyncio.get_event_loop()
        loop.call_later(self._throttle_interval, self._do_flush)

    def _do_flush(self) -> None:
        """Emit all accumulated UI signals with latest state."""
        self._flush_scheduled = False
        devices = self._pending_devices
        device_ids = self._pending_device_ids
        self._pending_device_ids = []

        # Per-device signals
        for did in device_ids:
            self.device_classified.emit(did, None)
            if self._ops_tracker is not None:
                self.operations_updated.emit(self._ops_tracker.get_capabilities(did))

        # Global state signals
        self.dedup_updated.emit(devices)
        if self.data_snapshot is not None:
            self.data_updated.emit(self.data_snapshot.get_all())

        if self.feature_extractor is not None:
            features = [self.feature_extractor.extract(d) for d in devices]
            self.features_updated.emit(features)

        # Heaviest: rebuild all profile cards
        self.device_profile_updated.emit(self._build_device_profiles(devices))

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._async_main())
        finally:
            loop.close()

    async def _async_main(self) -> None:
        self._client = DiscoverClient()
        self.deduplicator = Deduplicator()
        self.feature_extractor = FeatureExtractor()
        self.data_snapshot = DataSnapshot()
        self._ops_tracker = OperationsTracker()
        self._classifier = TopicClassifier()
        configs = load_config()

        def on_event(event: SourceEvent) -> None:
            self.event_received.emit(
                event.source_id,
                event.source_type,
                event.timestamp,
                event.event_type,
                event.payload,
            )
            # Track real connection state from status events
            if event.event_type == "status":
                msg = event.payload.get("msg", "")
                if "connected" in msg or msg == "scanning":
                    self.status_changed.emit(event.source_id, True)
                elif msg == "stopped" or "Disconnected" in str(msg):
                    self.status_changed.emit(event.source_id, False)

            # ── MQTT data events → new dialect pipeline ──
            if event.source_type == "mqtt" and event.event_type == "data":
                try:
                    from discover_client.dialect.aggregator import aggregate
                    topic = str(event.payload.get("topic", ""))
                    value = event.payload.get("value")
                    aggregated = aggregate(topic, value)
                    if aggregated is not None:
                        # Build evidence from aggregated output
                        evidence = SignalEvidence(
                            source_id=event.source_id,
                            source_type=event.source_type,
                            timestamp=event.timestamp,
                            event_type=event.event_type,
                            mqtt_topic=topic,
                            mqtt_payload=value,
                            mqtt_payload_keys=(
                                {op.sensor_key for op in aggregated.operations if op.sensor_key}
                                | {s.sensor_type for s in aggregated.sensor_readings}
                            ),
                            topic_prefix=aggregated.device_id,
                            dialect=aggregated.primary_dialect,
                            dialect_confidence=aggregated.dialect_confidence,
                        )
                        # Dedup first to get stable device_id
                        if self.deduplicator is not None:
                            device = self.deduplicator.ingest(evidence)
                            device_id = device.device_id  # use dedup's device_id consistently
                            devices = self.deduplicator.get_devices()
                        else:
                            device_id = aggregated.device_id
                            devices = []

                        self.evidence_produced.emit(evidence)

                        # Fan out to OperationsTracker + DataSnapshot (after dedup for correct device_id)
                        if self._ops_tracker is not None:
                            for op in aggregated.operations:
                                self._ops_tracker.ingest_structured(device_id, op)

                        if self.data_snapshot is not None:
                            for sensor in aggregated.sensor_readings:
                                self.data_snapshot.ingest_structured(device_id, sensor, timestamp=event.timestamp)

                        # Update classification state for device profile
                        state = self._classification_state.setdefault(device_id, _DeviceClassificationState())
                        state.update("command" if aggregated.operations else "telemetry", aggregated.dialect_confidence)

                        # Throttle all UI signals to one flush per 200ms
                        self._schedule_flush(devices, device_id)
                        return  # skip old annotator path for this event
                except Exception as exc:
                    import traceback
                    print(f"[dialect pipeline] ERROR: {exc}")
                    traceback.print_exc()
                    # fall through to legacy path

            # ── Legacy annotator path (non-MQTT events, or MQTT fallback) ──
            annotator_cls = ANNOTATORS.get(event.source_type)
            if annotator_cls is None:
                return

            evidence = annotator_cls().annotate(event)
            if evidence is not None:
                self.evidence_produced.emit(evidence)
                if self.deduplicator is not None:
                    device = self.deduplicator.ingest(evidence)
                    devices = self.deduplicator.get_devices()

                    classification = None
                    if (
                        event.source_type == "mqtt"
                        and event.event_type == "data"
                        and self._classifier is not None
                    ):
                        classification = self._classifier.classify(
                            event.payload.get("topic", ""),
                            event.payload.get("value"),
                        )
                        state = self._classification_state.setdefault(device.device_id, _DeviceClassificationState())
                        state.update(classification.label, classification.confidence)

                    if (
                        device is not None
                        and self._ops_tracker is not None
                        and classification is not None
                        and classification.label == "command"
                        and classification.confidence >= 0.7
                    ):
                        self._ops_tracker.ingest(device.device_id, evidence)

                    if (
                        event.event_type == "data"
                        and self.data_snapshot is not None
                        and classification is not None
                        and classification.label == "telemetry"
                        and classification.confidence >= 0.7
                    ):
                        data_event = {
                            "topic": event.payload.get("topic", ""),
                            "value": event.payload.get("value"),
                            "timestamp": event.timestamp,
                        }
                        for dev in devices:
                            matched = False
                            for prefix in dev.topic_prefixes:
                                if data_event["topic"].startswith(prefix):
                                    self.data_snapshot.ingest(dev.device_id, data_event)
                                    matched = True
                                    break
                            if matched:
                                break

                    # Throttle all UI signals to one flush per 200ms
                    self._schedule_flush(devices, device.device_id)

        self._client.subscribe(on_event)

        # All enabled sources start as not-yet-connected
        for c in configs:
            if c.enabled:
                self.status_changed.emit(c.source_id, False)

        try:
            await self._client.start(configs)
            while self._running:
                await asyncio.sleep(0.1)
        finally:
            await self._client.stop()

    def publish_mqtt(self, topic: str, payload: object) -> bool:
        if not topic or self._client is None:
            return False

        encoded = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=True)
        for source in self._client._sources.values():
            if getattr(source, "source_type", None) != "mqtt":
                continue
            client = getattr(source, "_client", None)
            if client is None:
                continue
            client.publish(topic, encoded)
            return True
        return False

    def _build_device_profiles(self, devices: list) -> list[DeviceProfile]:
        profiles: list[DeviceProfile] = []
        for device in devices:
            category, confidence = self._classification_state.get(device.device_id, _DeviceClassificationState()).summary()
            data_sensors: dict[str, dict] = {}
            if self.data_snapshot is not None:
                for sensor_name, reading in self.data_snapshot.get_latest(device.device_id).items():
                    data_sensors[sensor_name] = {
                        "value": reading.text_value if reading.text_value else reading.value,
                        "unit": reading.unit,
                        "ts": reading.timestamp,
                    }

            operations: list[dict] = []
            if self._ops_tracker is not None:
                for capability in self._ops_tracker.get_capabilities(device.device_id):
                    operations.append(
                        {
                            "action": capability.action.replace("_", " ").title(),
                            "topic": capability.topic,
                            "sensor_key": capability.sensor_key,
                            "accepted_values": list(capability.accepted_values),
                            "args": _infer_operation_args(
                                capability.topic,
                                capability.accepted_values,
                                list(data_sensors.keys()),
                                capability.sensor_key,
                            ),
                        }
                    )

            profiles.append(
                DeviceProfile(
                    device_id=device.device_id,
                    category=category,
                    confidence=confidence,
                    ip_addresses=set(device.ip_addresses),
                    mac_prefixes=set(device.mac_prefixes),
                    vendor=device.vendor,
                    data_sensors=data_sensors,
                    operations=operations,
                    total_evidence_count=device.total_evidence_count,
                    last_seen=device.last_seen,
                )
            )
        return profiles


def _infer_operation_args(topic: str, accepted_values: list[str], data_keys: list[str] | None = None, sensor_key: str | None = None) -> list[dict]:
    # If sensor_key is specified, only generate args for that key
    keys_to_use = [sensor_key] if sensor_key else (data_keys or [])
    keys_to_use = [k for k in keys_to_use if k]

    if keys_to_use:
        args = []
        for key in keys_to_use:
            if key == "power":
                args.append({"key": "power", "type": "string", "example": "ON"})
            elif key in ("brightness", "level"):
                args.append({"key": key, "type": "number", "example": "50"})
            elif key == "color":
                args.append({"key": "color", "type": "string", "example": "#ffffff"})
            else:
                args.append({"key": key, "type": "string", "example": ""})
        return args if args else [{"key": "value", "type": "string", "example": ""}]

    # No DataSnapshot keys — fall back to topic heuristics
    if accepted_values:
        return []
    lowered = topic.lower()
    if any(token in lowered for token in {"brightness", "level", "dimmer"}):
        return [{"key": "value", "type": "number", "example": "50"}]
    return [{"key": "value", "type": "string", "example": ""}]
