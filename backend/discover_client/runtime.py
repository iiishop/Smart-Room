"""Headless discovery runtime embeddable in Viewer or other applications."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import threading
import time
from typing import Any, Callable

from discover_client.client import DiscoverClient
from discover_client.config import load_config
from discover_client.dialect.aggregator import aggregate
from discover_client.identification import ANNOTATORS, DataSnapshot, Deduplicator, TopicClassifier
from discover_client.identification.evidence import SignalEvidence
from discover_client.identification.tokens import extract_identity_tokens
from discover_client.operations import OperationsTracker
from discover_client.mqtt_entity import group_physical_devices
from discover_client.mqtt_metadata import MqttMetadataIndex
from discover_client.profile import build_device_profile
from discover_client.registry import PersistentDeviceRegistry
from discover_client.source import SourceEvent


class DiscoverRuntime:
    def __init__(
        self,
        *,
        config_path: str | Path | None = None,
        registry_path: str | Path,
        on_update: Callable[[list[dict[str, Any]]], None] | None = None,
        profile_refresh_interval_s: float = 1.0,
    ) -> None:
        self.config_path = None if config_path is None else Path(config_path)
        self.registry = PersistentDeviceRegistry(registry_path)
        self.on_update = on_update
        self.profile_refresh_interval_s = max(0.05, float(profile_refresh_interval_s))
        self.client: DiscoverClient | None = None
        self.deduplicator = Deduplicator()
        self.data_snapshot = DataSnapshot()
        self.operations_tracker = OperationsTracker()
        self.classifier = TopicClassifier()
        self.mqtt_metadata = MqttMetadataIndex()
        self._profiles: dict[str, dict[str, Any]] = {
            item["canonical_device_id"]: item for item in self.registry.stored_profiles()
        }
        self._source_status: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_error = ""
        self._profiles_dirty = False
        self._profile_refresh_in_progress = False
        self._last_profile_refresh_at = 0.0
        self._profile_revision = 0
        self._events_ingested = 0
        self._profile_refresh_count = 0
        self._last_profile_refresh_duration_s = 0.0
        self._startup_maintenance_duration_s = 0.0

    def start_background(self) -> None:
        if self._running:
            return
        self._last_profile_refresh_at = time.monotonic()
        self._running = True
        self._thread = threading.Thread(target=self._thread_main, name="DiscoverRuntime", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._running = False
        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(lambda: None)
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=max(0.1, timeout))
        try:
            self.registry.flush()
        except Exception as exc:
            with self._lock:
                self._last_error = f"registry flush: {exc}"

    def profiles(self) -> list[dict[str, Any]]:
        with self._lock:
            values = [dict(item) for item in self._profiles.values()]
        values.sort(key=lambda item: float(item.get("last_seen") or 0.0), reverse=True)
        return values

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self._running,
                "device_count": len(self._profiles),
                "sources": {key: dict(value) for key, value in self._source_status.items()},
                "last_error": self._last_error,
                "profile_revision": self._profile_revision,
                "events_ingested": self._events_ingested,
                "profile_refresh_count": self._profile_refresh_count,
                "last_profile_refresh_duration_s": self._last_profile_refresh_duration_s,
                "startup_maintenance_duration_s": self._startup_maintenance_duration_s,
            }

    def publish_mqtt(self, topic: str, payload: object) -> bool:
        if not topic or self.client is None:
            return False
        encoded = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=True)
        for source in self.client._sources.values():
            if getattr(source, "source_type", None) != "mqtt":
                continue
            mqtt_client = getattr(source, "_client", None)
            if mqtt_client is None:
                continue
            mqtt_client.publish(topic, encoded)
            return True
        return False

    def ingest_event(self, event: SourceEvent) -> None:
        self._handle_event(event)
        if not self._running:
            self._maybe_refresh_profiles(force=True)

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            self._run_startup_maintenance()
            loop.run_until_complete(self._async_main())
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
            print(f"[discover] runtime failed: {exc}", flush=True)
        finally:
            self._running = False
            self._loop = None
            loop.close()

    async def _async_main(self) -> None:
        self.client = DiscoverClient()
        self.client.subscribe(self._handle_event)
        configs = load_config(self.config_path)
        with self._lock:
            for config in configs:
                self._source_status[config.source_id] = {
                    "source_id": config.source_id,
                    "source_type": config.source_type,
                    "enabled": config.enabled,
                    "state": "starting" if config.enabled else "disabled",
                    "message": "",
                    "updated_at": time.time(),
                }
        try:
            await self.client.start(configs)
            while self._running:
                self._maybe_refresh_profiles()
                await asyncio.sleep(0.05)
        finally:
            self._maybe_refresh_profiles(force=True)
            await self.client.stop()
            self.client = None

    def _handle_event(self, event: SourceEvent) -> None:
        with self._lock:
            self._events_ingested += 1
        self._update_source_status(event)
        evidence: SignalEvidence | None = None

        if event.source_type == "mqtt" and event.event_type == "data":
            aggregated = None
            try:
                topic = str(event.payload.get("topic", ""))
                value = event.payload.get("value")
                if self.mqtt_metadata.ingest(event.source_id, topic, value):
                    for metadata_update in self.mqtt_metadata.drain_updates():
                        metadata_evidence = SignalEvidence(
                            source_id=event.source_id,
                            source_type=event.source_type,
                            timestamp=event.timestamp,
                            event_type="discovery",
                            mqtt_topic=topic,
                            mqtt_payload=value,
                            mqtt_payload_keys=set(metadata_update.capabilities),
                            topic_prefix=metadata_update.topic_prefix,
                            dialect=metadata_update.convention,
                            dialect_confidence=0.99,
                            identity_tokens=set(metadata_update.identity_tokens),
                        )
                        metadata_device = self.deduplicator.ingest(metadata_evidence)
                        self.mqtt_metadata.enrich_device(
                            metadata_device,
                            metadata_update,
                        )
                        self._mark_profiles_dirty()
                    return
                metadata = self.mqtt_metadata.lookup(event.source_id, topic)
                aggregated = aggregate(topic, value)
            except Exception as exc:
                with self._lock:
                    self._last_error = f"dialect parse: {exc}"
                metadata = None

            if aggregated is not None:
                try:
                    identity_tokens = extract_identity_tokens(topic, value)
                    topic_prefix = aggregated.device_id
                    if metadata is not None:
                        identity_tokens.update(metadata.identity_tokens)
                        topic_prefix = metadata.topic_prefix
                    evidence = SignalEvidence(
                        source_id=event.source_id,
                        source_type=event.source_type,
                        timestamp=event.timestamp,
                        event_type=event.event_type,
                        mqtt_topic=topic,
                        mqtt_payload=value,
                        mqtt_payload_keys=(
                            {op.sensor_key for op in aggregated.operations if op.sensor_key}
                            | {sensor.sensor_type for sensor in aggregated.sensor_readings}
                        ),
                        topic_prefix=topic_prefix,
                        dialect=aggregated.primary_dialect,
                        dialect_confidence=aggregated.dialect_confidence,
                        identity_tokens=identity_tokens,
                    )
                    device = self.deduplicator.ingest(evidence)
                    if metadata is not None:
                        self.mqtt_metadata.enrich_device(device, metadata)
                    for operation in aggregated.operations:
                        self.operations_tracker.ingest_structured(device.device_id, operation)
                    for sensor in aggregated.sensor_readings:
                        self.data_snapshot.ingest_structured(device.device_id, sensor, timestamp=event.timestamp)
                    self._mark_profiles_dirty()
                except Exception as exc:
                    with self._lock:
                        self._last_error = f"dialect ingest: {exc}"
                # Never run the legacy path after an event was accepted by the
                # dialect pipeline. Persistence/UI failures must not duplicate it.
                return

        annotator_cls = ANNOTATORS.get(event.source_type)
        if annotator_cls is not None:
            evidence = annotator_cls().annotate(event)
        if evidence is None:
            return

        device = self.deduplicator.ingest(evidence)
        if event.source_type == "mqtt" and event.event_type == "data":
            classification = self.classifier.classify(
                str(event.payload.get("topic", "")),
                event.payload.get("value"),
            )
            if classification.label == "command" and classification.confidence >= 0.7:
                self.operations_tracker.ingest(device.device_id, evidence)
            if classification.label == "telemetry" and classification.confidence >= 0.7:
                self.data_snapshot.ingest(
                    device.device_id,
                    {
                        "topic": event.payload.get("topic", ""),
                        "value": event.payload.get("value"),
                        "timestamp": event.timestamp,
                    },
                )
        self._mark_profiles_dirty()

    def _mark_profiles_dirty(self) -> None:
        with self._lock:
            self._profiles_dirty = True

    def _maybe_refresh_profiles(self, *, force: bool = False) -> bool:
        now = time.monotonic()
        with self._lock:
            if not self._profiles_dirty or self._profile_refresh_in_progress:
                return False
            if not force and now - self._last_profile_refresh_at < self.profile_refresh_interval_s:
                return False
            self._profiles_dirty = False
            self._profile_refresh_in_progress = True
        try:
            self._refresh_profiles()
            return True
        except Exception as exc:
            with self._lock:
                self._profiles_dirty = True
                self._last_error = f"profile refresh: {exc}"
            return False
        finally:
            with self._lock:
                self._profile_refresh_in_progress = False
                self._last_profile_refresh_at = time.monotonic()

    def _run_startup_maintenance(self) -> None:
        started = time.perf_counter()
        try:
            self.registry.compact_mqtt_channels()
            stored = {
                item["canonical_device_id"]: item
                for item in self.registry.stored_profiles()
            }
            with self._lock:
                self._profiles = stored
                self._profile_revision += 1
        except Exception as exc:
            with self._lock:
                self._last_error = f"registry startup maintenance: {exc}"
        finally:
            with self._lock:
                self._startup_maintenance_duration_s = time.perf_counter() - started

    def _refresh_profiles(self) -> None:
        started = time.perf_counter()
        fresh: dict[str, dict[str, Any]] = {}
        physical_devices = group_physical_devices(self.deduplicator.get_devices())
        for device in physical_devices:
            canonical_id = self.registry.resolve(device, save=False)
            profile = build_device_profile(
                device,
                canonical_id,
                self.data_snapshot,
                self.operations_tracker,
            ).to_dict()
            self.registry.update_profile(canonical_id, profile, save=False)
            fresh[canonical_id] = profile
        try:
            self.registry.flush_if_due(0.75)
        except Exception as exc:
            with self._lock:
                self._last_error = f"registry persistence: {exc}"

        for stored in self.registry.stored_profiles():
            canonical_id = str(stored.get("canonical_device_id") or "")
            if canonical_id and canonical_id not in fresh:
                stored["online"] = False
                fresh[canonical_id] = stored

        with self._lock:
            self._profiles = fresh
            self._profile_revision += 1
            self._profile_refresh_count += 1
            self._last_profile_refresh_duration_s = time.perf_counter() - started
            callback = self.on_update
            snapshot = [dict(item) for item in fresh.values()]
        if callback is not None:
            try:
                callback(snapshot)
            except Exception as exc:
                with self._lock:
                    self._last_error = f"update callback: {exc}"

    def _update_source_status(self, event: SourceEvent) -> None:
        if event.event_type not in {"status", "error"}:
            return
        message = str(event.payload.get("msg") or event.payload)
        state = "error" if event.event_type == "error" else _status_state(message)
        with self._lock:
            status = self._source_status.setdefault(
                event.source_id,
                {
                    "source_id": event.source_id,
                    "source_type": event.source_type,
                    "enabled": True,
                },
            )
            status.update(
                {
                    "state": state,
                    "message": message,
                    "updated_at": event.timestamp,
                }
            )
            if event.event_type == "error":
                self._last_error = message


def _status_state(message: str) -> str:
    lowered = message.lower()
    if "stopped" in lowered or "disconnect" in lowered:
        return "stopped"
    if "connected" in lowered or "scanning" in lowered:
        return "running"
    if "reconnect" in lowered:
        return "reconnecting"
    return "starting"
