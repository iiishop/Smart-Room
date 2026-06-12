from discover_client.identification.classifier import TopicClassifier


def test_topic_classifier_identifies_telemetry_from_sensor_topic_and_unit_payload() -> None:
    classifier = TopicClassifier()

    result = classifier.classify(
        "govee/H5179/a1b2c3d4e5f6/temperature",
        {"unit": "C", "value": 25.3},
    )

    assert result.label == "telemetry"
    assert result.confidence >= 0.8
    assert result.evidence


def test_topic_classifier_identifies_command_from_set_topic_and_enum_payload() -> None:
    classifier = TopicClassifier()

    result = classifier.classify("zigbee2mqtt/0x001/set", {"state": "ON"})

    assert result.label == "command"
    assert result.confidence >= 0.9
    assert result.evidence


def test_topic_classifier_returns_unknown_for_ambiguous_topic() -> None:
    classifier = TopicClassifier()

    result = classifier.classify("custom/device/output")

    assert result.label == "unknown"
    assert result.confidence == 0.3
    assert result.evidence == ["fallback: no command or telemetry rule matched"]
