from discover_client.identification.deduplicator import Deduplicator
from discover_client.identification.evidence import SignalEvidence


def test_deduplicator_merges_cross_source_evidence_and_accumulates_fields() -> None:
    deduplicator = Deduplicator()

    device1 = deduplicator.ingest(
        SignalEvidence(
            source_id="nmap-1",
            source_type="nmap",
            nmap_mac="58:04:4F:9A:DC:05",
            nmap_vendor="TP-Link Systems Inc.",
            mac_prefix="58:04:4F",
            ip_address="192.168.5.2",
            timestamp=1.0,
        )
    )

    assert device1.device_id == "device-1"
    assert device1.ip_addresses == {"192.168.5.2"}
    assert device1.mac_prefixes == {"58:04:4F"}
    assert device1.mac_addresses == {"58:04:4F:9A:DC:05"}
    assert device1.vendor == "TP-Link Systems Inc."

    device2 = deduplicator.ingest(
        SignalEvidence(
            source_id="mdns-1",
            source_type="mdns",
            mdns_service_type="_matter._tcp.local.",
            hostname="58044F9ADC05.local.",
            ip_address="192.168.5.2",
            timestamp=2.0,
        )
    )

    assert device2.device_id == "device-1"
    assert device2.total_evidence_count == 2
    assert device2.hostnames == {"58044F9ADC05.local."}
    assert device2.service_types == {"_matter._tcp.local."}
    assert device2.source_ids == {"nmap-1", "mdns-1"}
    assert device2.last_seen == 2.0


def test_deduplicator_does_not_merge_by_mac_prefix_alone() -> None:
    deduplicator = Deduplicator()

    first = deduplicator.ingest(
        SignalEvidence(
            source_id="nmap-1",
            source_type="nmap",
            nmap_mac="80:64:7C:D9:AC:23",
            nmap_vendor="Tuya Smart Inc.",
            mac_prefix="80:64:7C",
            ip_address="192.168.5.3",
            timestamp=3.0,
        )
    )
    second = deduplicator.ingest(
        SignalEvidence(
            source_id="nmap-1",
            source_type="nmap",
            nmap_mac="80:64:7C:11:22:33",
            nmap_vendor="Tuya Smart Inc.",
            mac_prefix="80:64:7C",
            ip_address="192.168.5.4",
            timestamp=4.0,
        )
    )

    assert first.device_id == "device-1"
    assert second.device_id == "device-2"
    assert len(deduplicator.get_devices()) == 2


def test_deduplicator_collects_mqtt_payload_keys_on_same_device() -> None:
    deduplicator = Deduplicator()

    deduplicator.ingest(
        SignalEvidence(
            source_id="nmap-1",
            source_type="nmap",
            nmap_mac="58:04:4F:9A:DC:05",
            mac_prefix="58:04:4F",
            ip_address="192.168.5.2",
            timestamp=1.0,
        )
    )
    device = deduplicator.ingest(
        SignalEvidence(
            source_id="mqtt-1",
            source_type="mqtt",
            mqtt_payload_keys={"temp", "humidity"},
            ip_address="192.168.5.2",
            timestamp=5.0,
        )
    )

    assert device.device_id == "device-1"
    assert device.payload_keys == {"humidity", "temp"}
