from discover_client.identification.annotators.packet_sniff import PacketSniffAnnotator
from discover_client.source import SourceEvent


def test_packet_sniff_annotator_extracts_mqtt_publish_identity() -> None:
    evidence = PacketSniffAnnotator().annotate(
        SourceEvent(
            source_id="sniff-lab",
            source_type="packet_sniff",
            timestamp=1.0,
            event_type="discovery",
            payload={
                "kind": "mqtt_publish",
                "client_id": "picloud-12",
                "ip": "192.168.1.44",
                "mac": "AA:BB:CC:DD:EE:FF",
                "topic": "student/PiCloud/picloud-12/poe",
            },
        )
    )

    assert evidence is not None
    assert evidence.mqtt_client_id == "picloud-12"
    assert evidence.mqtt_topic == "student/PiCloud/picloud-12/poe"
    assert evidence.topic_prefix == "student/PiCloud/picloud-12"
    assert evidence.ip_address == "192.168.1.44"
    assert evidence.nmap_mac == "AA:BB:CC:DD:EE:FF"
    assert "mqttclientpicloud12" in evidence.identity_tokens


def test_packet_sniff_annotator_extracts_arp_identity() -> None:
    evidence = PacketSniffAnnotator().annotate(
        SourceEvent(
            source_id="sniff-lab",
            source_type="packet_sniff",
            timestamp=1.0,
            event_type="discovery",
            payload={
                "kind": "arp",
                "ip": "192.168.1.44",
                "mac": "AA:BB:CC:DD:EE:FF",
            },
        )
    )

    assert evidence is not None
    assert evidence.ip_address == "192.168.1.44"
    assert evidence.nmap_mac == "AA:BB:CC:DD:EE:FF"
    assert evidence.mac_prefix == "AA:BB:CC"
