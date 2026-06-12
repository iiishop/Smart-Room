from discover_client.identification.evidence import SignalEvidence


def test_signal_evidence_summarize_includes_key_clues() -> None:
    evidence = SignalEvidence(
        source_id="test",
        source_type="mqtt",
        mqtt_topic="govee/+/state",
        mqtt_payload_keys={"temp"},
    )

    summary = evidence.summarize()

    assert "topic=govee/+/state" in summary
    assert "keys={temp}" in summary


def test_signal_evidence_summarize_uses_nmap_mac_field() -> None:
    evidence = SignalEvidence(
        source_id="test",
        source_type="nmap",
        nmap_mac="AA:BB:CC",
        nmap_vendor="Intel",
        ip_address="192.168.1.1",
    )

    summary = evidence.summarize()

    assert "mac=AA:BB:CC" in summary
    assert "vendor=Intel" in summary
    assert "ip=192.168.1.1" in summary
