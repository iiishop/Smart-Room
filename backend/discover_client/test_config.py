from pathlib import Path
import os

from discover_client.config import load_config, save_config
from discover_client.source import SourceConfig


def test_config_save_and_load_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    original = [
        SourceConfig(
            source_id="mqtt-main",
            source_type="mqtt",
            enabled=True,
            settings={
                "host": "mqtt.example.test",
                "port": 1883,
                "username": "user",
                "password": "p@ss",
                "topic_whitelist": ["site/#"],
                "topic_blacklist": ["site/debug/#"],
            },
        ),
        SourceConfig(
            source_id="mdns-main",
            source_type="mdns",
            enabled=False,
            settings={
                "scan_interval_s": 45,
                "service_types": ["_mqtt._tcp.local.", "_matter._tcp.local."],
            },
        ),
        SourceConfig(
            source_id="sniff-pcap",
            source_type="packet_sniff",
            enabled=True,
            settings={
                "pcap_path": "D:\\captures\\mqtt.pcap",
                "live": False,
                "interface": "",
                "broker_ports": [1883],
                "capture_filter": "",
                "emit_publish_topics": True,
                "max_packets": 0,
            },
        ),
    ]

    save_config(original, path)
    loaded = load_config(path)

    assert loaded == original


def test_config_save_retries_when_target_is_temporarily_locked(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "config.toml"
    real_replace = os.replace
    attempts = 0

    def flaky_replace(source, target):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("temporarily locked")
        return real_replace(source, target)

    monkeypatch.setattr("discover_client.config.os.replace", flaky_replace)
    save_config(
        [
            SourceConfig(
                source_id="mqtt-main",
                source_type="mqtt",
                settings={"host": "localhost", "port": 1883},
            )
        ],
        path,
    )

    assert attempts == 3
    assert load_config(path)[0].source_id == "mqtt-main"
