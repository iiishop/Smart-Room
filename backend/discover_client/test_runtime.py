from pathlib import Path
import time

from discover_client.registry import PersistentDeviceRegistry
from discover_client.runtime import DiscoverRuntime
from discover_client.source import SourceEvent


def test_runtime_builds_profile_without_starting_gui_or_network(tmp_path: Path) -> None:
    runtime = DiscoverRuntime(registry_path=tmp_path / "registry.json")
    runtime.ingest_event(
        SourceEvent(
            source_id="mqtt-test",
            source_type="mqtt",
            timestamp=10.0,
            event_type="data",
            payload={
                "topic": "govee/H5179/abc/temperature",
                "value": {"value": 21.5, "unit": "C"},
            },
        )
    )

    profiles = runtime.profiles()

    assert len(profiles) == 1
    assert profiles[0]["canonical_device_id"].startswith("urn:smartroom:device:")
    assert "H5179" in profiles[0]["model_candidates"]
    assert "temperature" in profiles[0]["capabilities"]


def test_runtime_uses_home_assistant_device_metadata(tmp_path: Path) -> None:
    runtime = DiscoverRuntime(registry_path=tmp_path / "registry.json")
    runtime.ingest_event(
        SourceEvent(
            source_id="mqtt-test",
            source_type="mqtt",
            timestamp=1.0,
            event_type="data",
            payload={
                "topic": "homeassistant/sensor/desk_temperature/config",
                "value": {
                    "state_topic": "lab/desk/temperature",
                    "device_class": "temperature",
                    "device": {
                        "identifiers": ["desk-sensor-001"],
                        "name": "Desk sensor",
                        "manufacturer": "Acme",
                        "model": "Env-1",
                    },
                },
            },
        )
    )
    runtime.ingest_event(
        SourceEvent(
            source_id="mqtt-test",
            source_type="mqtt",
            timestamp=2.0,
            event_type="data",
            payload={"topic": "lab/desk/temperature", "value": 21.5},
        )
    )

    profiles = runtime.profiles()

    assert len(profiles) == 1
    assert profiles[0]["display_name"] == "Desk sensor"
    assert profiles[0]["vendor"] == "Acme"
    assert profiles[0]["classification"]["method"] == "explicit MQTT discovery metadata"


def test_runtime_does_not_fall_back_and_duplicate_after_profile_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = DiscoverRuntime(registry_path=tmp_path / "registry.json")

    def fail_resolve(*_args, **_kwargs):
        raise PermissionError("locked")

    monkeypatch.setattr(runtime.registry, "resolve", fail_resolve)
    runtime.ingest_event(
        SourceEvent(
            source_id="mqtt-test",
            source_type="mqtt",
            timestamp=1.0,
            event_type="data",
            payload={"topic": "site/device/value", "value": 1},
        )
    )

    assert len(runtime.deduplicator.get_devices()) == 1
    assert runtime.status()["last_error"].startswith("profile refresh:")


def test_runtime_exposes_tasmota_discovery_operation_without_observing_command(
    tmp_path: Path,
) -> None:
    runtime = DiscoverRuntime(registry_path=tmp_path / "registry.json")
    runtime.ingest_event(
        SourceEvent(
            source_id="mqtt-test",
            source_type="mqtt",
            timestamp=1.0,
            event_type="data",
            payload={
                "topic": "tasmota/discovery/C4DD573EB84C/config",
                "value": {
                    "fn": ["Gosund UP111 Prusa3 Tasmota"],
                    "mac": "C4DD573EB84C",
                    "md": "Gosund UP111",
                    "state": ["OFF", "ON", "TOGGLE", "HOLD"],
                    "t": "gosund/paul-the-prusa-3",
                    "ft": "UCL/OPS/107/EM/%topic%",
                    "tp": ["cmnd", "stat", "tele"],
                    "rl": [1, 0, 0, 0],
                },
            },
        )
    )
    initial_profiles = runtime.profiles()
    assert len(initial_profiles) == 1
    assert initial_profiles[0]["operations"][0]["topic"] == (
        "UCL/OPS/107/EM/gosund/paul-the-prusa-3/POWER"
    )
    runtime.ingest_event(
        SourceEvent(
            source_id="mqtt-test",
            source_type="mqtt",
            timestamp=2.0,
            event_type="data",
            payload={
                "topic": "UCL/OPS/107/EM/gosund/paul-the-prusa-3/STATE",
                "value": {"POWER": "ON", "UptimeSec": 100},
            },
        )
    )

    profiles = runtime.profiles()

    assert len(profiles) == 1
    assert profiles[0]["display_name"] == "Gosund UP111 Prusa3 Tasmota"
    assert profiles[0]["operations"] == [
        {
            "topic": "UCL/OPS/107/EM/gosund/paul-the-prusa-3/POWER",
            "action": "set",
            "sensor_key": "",
            "accepted_values": ["HOLD", "OFF", "ON", "TOGGLE"],
            "confidence": 0.98,
            "first_seen": 0.0,
            "last_seen": 2.0,
            "runtime_device_id": profiles[0]["runtime_device_id"],
            "source": "explicit MQTT discovery metadata",
        }
    ]


def test_runtime_exposes_homie_settable_property_operation(tmp_path: Path) -> None:
    runtime = DiscoverRuntime(registry_path=tmp_path / "registry.json")
    runtime.ingest_event(
        SourceEvent(
            source_id="mqtt-test",
            source_type="mqtt",
            timestamp=1.0,
            event_type="data",
            payload={
                "topic": "homie/5/fan-001/$description",
                "value": {
                    "name": "Desk fan",
                    "id": "fan-001",
                    "nodes": {
                        "control": {
                            "properties": {
                                "power": {
                                    "datatype": "enum",
                                    "format": "OFF,ON",
                                    "settable": True,
                                }
                            }
                        }
                    },
                },
            },
        )
    )

    profiles = runtime.profiles()

    assert len(profiles) == 1
    assert profiles[0]["display_name"] == "Desk fan"
    assert profiles[0]["classification"]["method"] == "explicit MQTT discovery metadata"
    assert profiles[0]["operations"][0]["topic"] == "homie/5/fan-001/control/power/set"
    assert profiles[0]["operations"][0]["accepted_values"] == ["OFF", "ON"]


def test_runtime_merges_packet_sniff_mqtt_identity_with_topic_profile(tmp_path: Path) -> None:
    runtime = DiscoverRuntime(registry_path=tmp_path / "registry.json")
    runtime.ingest_event(
        SourceEvent(
            source_id="mqtt-main",
            source_type="mqtt",
            timestamp=1.0,
            event_type="data",
            payload={"topic": "student/PiCloud/picloud-12/poe", "value": 1},
        )
    )
    runtime.ingest_event(
        SourceEvent(
            source_id="sniff-lab",
            source_type="packet_sniff",
            timestamp=2.0,
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

    profiles = runtime.profiles()

    assert len(profiles) == 1
    identifiers = profiles[0]["identifiers"]
    assert "sniff-lab|picloud-12" in identifiers["mqtt_client_id"]
    assert profiles[0]["connections"]["ip"] == ["192.168.1.44"]
    assert profiles[0]["connections"]["mac"] == ["AA:BB:CC:DD:EE:FF"]


def test_runtime_keeps_tasmota_devices_separate_from_sensor_snapshots(
    tmp_path: Path,
) -> None:
    runtime = DiscoverRuntime(registry_path=tmp_path / "registry.json")
    for index, mac in enumerate(("C4DD573EB84C", "70039F90DE47")):
        topic_name = f"gosund/device-{index}"
        runtime.ingest_event(
            SourceEvent(
                source_id="mqtt-test",
                source_type="mqtt",
                timestamp=float(index + 1),
                event_type="data",
                payload={
                    "topic": f"tasmota/discovery/{mac}/config",
                    "value": {
                        "fn": [f"Gosund device {index}"],
                        "mac": mac,
                        "t": topic_name,
                        "ft": "UCL/OPS/107/EM/%topic%",
                        "tp": ["cmnd", "stat", "tele"],
                        "rl": [1],
                        "state": ["OFF", "ON", "TOGGLE"],
                    },
                },
            )
        )
        runtime.ingest_event(
            SourceEvent(
                source_id="mqtt-test",
                source_type="mqtt",
                timestamp=float(index + 10),
                event_type="data",
                payload={
                    "topic": f"tasmota/discovery/{mac}/sensors",
                    "value": {
                        "sn": {
                            "Time": "2026-06-25T23:00:30",
                            "ENERGY": {"Power": 10},
                        }
                    },
                },
            )
        )

    profiles = runtime.profiles()

    assert len(profiles) == 2
    assert all(len(profile["operations"]) == 1 for profile in profiles)


def test_runtime_batches_many_events_into_one_profile_refresh(tmp_path: Path) -> None:
    runtime = DiscoverRuntime(
        registry_path=tmp_path / "registry.json",
        profile_refresh_interval_s=1.0,
    )
    runtime._running = True

    for index in range(100):
        runtime.ingest_event(
            SourceEvent(
                source_id="mqtt-test",
                source_type="mqtt",
                timestamp=float(index),
                event_type="data",
                payload={
                    "topic": f"site/room-{index:03d}/sensor/value",
                    "value": index,
                },
            )
        )

    assert runtime.status()["profile_refresh_count"] == 0
    assert runtime._maybe_refresh_profiles(force=True)
    assert runtime.status()["profile_refresh_count"] == 1
    assert len(runtime.profiles()) == 100


def test_runtime_constructor_does_not_run_registry_compaction_on_caller_thread(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def slow_compaction(_self):
        time.sleep(0.25)
        return 0

    monkeypatch.setattr(
        PersistentDeviceRegistry,
        "compact_mqtt_channels",
        slow_compaction,
    )
    started = time.perf_counter()
    DiscoverRuntime(registry_path=tmp_path / "registry.json")

    assert time.perf_counter() - started < 0.1
