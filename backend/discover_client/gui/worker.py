"""Background worker that runs DiscoverClient's asyncio loop."""

import asyncio
import threading

from PySide6.QtCore import QObject, Signal

from discover_client.client import DiscoverClient
from discover_client.identification import ANNOTATORS, DataSnapshot, Deduplicator, FeatureExtractor
from discover_client.operations import OperationsTracker
from discover_client.source import SourceEvent
from discover_client.config import load_config


class Worker(QObject):
    """Runs DiscoverClient on a background thread, bridges events to Qt."""

    event_received = Signal(str, str, float, str, dict)
    evidence_produced = Signal(object)
    dedup_updated = Signal(list)
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

            annotator_cls = ANNOTATORS.get(event.source_type)
            if annotator_cls is None:
                return

            evidence = annotator_cls().annotate(event)
            if evidence is not None:
                self.evidence_produced.emit(evidence)
                if self.deduplicator is not None:
                    device = self.deduplicator.ingest(evidence)
                    devices = self.deduplicator.get_devices()
                    self.dedup_updated.emit(devices)
                    if self.feature_extractor is not None:
                        features = [self.feature_extractor.extract(device) for device in devices]
                        self.features_updated.emit(features)
                    if device is not None and self._ops_tracker is not None:
                        changed = self._ops_tracker.ingest(device.device_id, evidence)
                        if changed is not None:
                            self.operations_updated.emit(self._ops_tracker.get_capabilities(device.device_id))
                    if event.event_type == "data" and self.data_snapshot is not None:
                        data_event = {
                            "topic": event.payload.get("topic", ""),
                            "value": event.payload.get("value"),
                            "timestamp": event.timestamp,
                        }
                        for device in devices:
                            matched = False
                            for prefix in device.topic_prefixes:
                                if data_event["topic"].startswith(prefix):
                                    readings = self.data_snapshot.ingest(device.device_id, data_event)
                                    if readings:
                                        self.data_updated.emit(self.data_snapshot.get_all())
                                    matched = True
                                    break
                            if matched:
                                break

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
