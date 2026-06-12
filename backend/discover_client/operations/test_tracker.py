from discover_client.identification.evidence import SignalEvidence
from discover_client.operations.tracker import OperationsTracker, _classify


def test_classify_command_from_topic_suffix() -> None:
    evidence = SignalEvidence(
        source_id="mqtt-1",
        source_type="mqtt",
        mqtt_topic="lab/device/switch",
        timestamp=100.0,
    )

    category, confidence = _classify(evidence)

    assert category == "command"
    assert confidence == 0.85


def test_classify_telemetry_from_sensor_topic_word() -> None:
    evidence = SignalEvidence(
        source_id="mqtt-1",
        source_type="mqtt",
        mqtt_topic="lab/device/temperature",
        timestamp=100.0,
    )

    category, confidence = _classify(evidence)

    assert category == "telemetry"
    assert confidence == 0.8


def test_ingest_records_command_values_from_string_payload() -> None:
    tracker = OperationsTracker()

    capability = tracker.ingest(
        "device-1",
        SignalEvidence(
            source_id="mqtt-1",
            source_type="mqtt",
            mqtt_topic="lab/device/set",
            mqtt_payload="ON",
            timestamp=100.0,
        ),
    )

    assert capability is not None
    assert capability.action == "set"
    assert capability.accepted_values == ["ON"]
    assert capability.confidence == 0.9


def test_ingest_merges_duplicate_topics_and_accumulates_values() -> None:
    tracker = OperationsTracker()

    tracker.ingest(
        "device-1",
        SignalEvidence(
            source_id="mqtt-1",
            source_type="mqtt",
            mqtt_topic="lab/device/switch",
            mqtt_payload={"state": "ON"},
            timestamp=100.0,
        ),
    )
    capability = tracker.ingest(
        "device-1",
        SignalEvidence(
            source_id="mqtt-1",
            source_type="mqtt",
            mqtt_topic="lab/device/switch",
            mqtt_payload={"state": "OFF"},
            timestamp=110.0,
        ),
    )

    assert capability is not None
    assert capability.topic == "lab/device/switch"
    assert capability.action == "switch"
    assert capability.accepted_values == ["OFF", "ON"]
    assert capability.first_seen == 100.0
    assert capability.last_seen == 110.0
    assert len(tracker.get_capabilities("device-1")) == 1


def test_ingest_ignores_telemetry_topics() -> None:
    tracker = OperationsTracker()

    capability = tracker.ingest(
        "device-1",
        SignalEvidence(
            source_id="mqtt-1",
            source_type="mqtt",
            mqtt_topic="lab/device/temperature",
            mqtt_payload={"value": 21.5, "unit": "C"},
            timestamp=100.0,
        ),
    )

    assert capability is None
    assert tracker.get_capabilities("device-1") == []
