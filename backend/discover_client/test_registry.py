from pathlib import Path
import os

from discover_client.identification.device import Device
from discover_client.registry import PersistentDeviceRegistry


def _device(*, ip: str, mac: str, topic: str) -> Device:
    return Device(
        device_id="device-1",
        last_seen=10.0,
        ip_addresses={ip},
        mac_addresses={mac},
        topic_prefixes={topic},
        mqtt_identities={f"mqtt-main|{topic}"},
    )


def test_registry_keeps_identity_across_restart_and_ip_change(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    first_registry = PersistentDeviceRegistry(path)
    first_id = first_registry.resolve(
        _device(ip="192.168.1.10", mac="AA:BB:CC:DD:EE:FF", topic="govee/H5179/abc")
    )

    second_registry = PersistentDeviceRegistry(path)
    second_id = second_registry.resolve(
        _device(ip="192.168.1.99", mac="AA:BB:CC:DD:EE:FF", topic="govee/H5179/abc")
    )

    assert second_id == first_id


def test_registry_does_not_use_ip_as_identity(tmp_path: Path) -> None:
    registry = PersistentDeviceRegistry(tmp_path / "registry.json")
    first = registry.resolve(
        _device(ip="192.168.1.20", mac="AA:BB:CC:00:00:01", topic="lab/sensor/one")
    )
    second = registry.resolve(
        _device(ip="192.168.1.20", mac="AA:BB:CC:00:00:02", topic="lab/sensor/two")
    )

    assert first != second


def test_stored_profile_exposes_stable_discovery_time(tmp_path: Path) -> None:
    registry = PersistentDeviceRegistry(tmp_path / "registry.json")
    canonical_id = registry.resolve(
        _device(ip="192.168.1.30", mac="AA:BB:CC:00:00:03", topic="lab/sensor/three")
    )
    registry.update_profile(
        canonical_id,
        {
            "canonical_device_id": canonical_id,
            "display_name": "Sensor three",
            "last_seen": 100.0,
        },
    )

    profile = registry.stored_profiles()[0]
    assert profile["discovered_at"] == registry.created_at(canonical_id)
    assert profile["discovered_at"] > 0


def test_registry_compacts_channel_profiles_into_physical_devices(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    registry = PersistentDeviceRegistry(path)
    prefixes = [
        "UCL/OPSEBO/206/2ByDoorToCommonArea2C2/LMS",
        "UCL/OPSEBO/206/2ByDoorToCommonArea2C2/TPS",
        "UCL/OPSEBO/203/BKitchen/LMS",
        "UCL/OPSEBO/203/BKitchen/TPS",
    ]
    for index, prefix in enumerate(prefixes):
        device = Device(
            device_id=f"device-{index}",
            last_seen=float(index + 1),
            topic_prefixes={prefix},
            mqtt_identities={f"mqtt-main|{prefix}"},
        )
        canonical_id = registry.resolve(device)
        registry.update_profile(
            canonical_id,
            {
                "canonical_device_id": canonical_id,
                "runtime_device_id": device.device_id,
                "display_name": prefix,
                "identifiers": {
                    "mqtt_topic_prefix": [prefix],
                    "mqtt_identity": [f"mqtt-main|{prefix}"],
                },
                "data": {prefix: {"value": index, "timestamp": index + 1}},
                "operations": [],
                "last_seen": index + 1,
                "evidence_count": 1,
            },
        )

    merged = registry.compact_mqtt_channels()
    profiles = registry.stored_profiles()

    assert merged == 2
    assert len(profiles) == 2
    target = next(
        profile
        for profile in profiles
        if "UCL/OPSEBO/206/2ByDoorToCommonArea2C2"
        in profile["identifiers"]["mqtt_entity_prefix"]
    )
    assert set(target["identifiers"]["mqtt_channel"]) == {"LMS", "TPS"}


def test_registry_retries_atomic_replace_when_target_is_temporarily_locked(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "registry.json"
    registry = PersistentDeviceRegistry(path)
    real_replace = os.replace
    attempts = 0

    def flaky_replace(source, target):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("temporarily locked")
        return real_replace(source, target)

    monkeypatch.setattr("discover_client.registry.os.replace", flaky_replace)
    registry.resolve(
        _device(ip="192.168.1.10", mac="AA:BB:CC:DD:EE:FF", topic="lab/device")
    )

    assert attempts == 3
    assert path.exists()


def test_registry_updates_single_channel_profile_and_prunes_legacy_root(
    tmp_path: Path,
) -> None:
    path = tmp_path / "registry.json"
    registry = PersistentDeviceRegistry(path)
    prefixes = [
        "UCL",
        "UCL/OPSEBO/001/Room/TPS",
        "UCL/OPSEBO/002/Room/TPS",
        "UCL/OPSEBO/003/Room/TPS",
        "UCL/OPSEBO/004/Room/TPS",
        "UCL/OPSEBO/005/Room/TPS",
    ]
    for index, prefix in enumerate(prefixes):
        device = Device(
            device_id=f"device-{index}",
            last_seen=float(index + 1),
            topic_prefixes={prefix},
            mqtt_identities={f"mqtt-main|{prefix}"},
        )
        canonical_id = registry.resolve(device)
        registry.update_profile(
            canonical_id,
            {
                "canonical_device_id": canonical_id,
                "runtime_device_id": device.device_id,
                "display_name": prefix,
                "identifiers": {
                    "mqtt_topic_prefix": [prefix],
                    "mqtt_identity": [f"mqtt-main|{prefix}"],
                },
                "connections": {"ip": [], "mac": []},
                "data": {},
                "operations": [],
                "last_seen": index + 1,
                "evidence_count": 1,
            },
        )

    changed = registry.compact_mqtt_channels()
    profiles = registry.stored_profiles()

    assert changed == 1
    assert len(profiles) == 5
    assert all(
        (profile["identifiers"]["mqtt_entity_prefix"][0]).endswith("/Room")
        for profile in profiles
    )
    assert all(profile["identifiers"]["mqtt_channel"] == ["TPS"] for profile in profiles)


def test_registry_repairs_legacy_tasmota_discovery_alias_merges(
    tmp_path: Path,
) -> None:
    path = tmp_path / "registry.json"
    registry = PersistentDeviceRegistry(path)
    canonical_id = "urn:smartroom:device:combined"
    registry._data = {
        "schema_version": 1,
        "devices": {
            canonical_id: {
                "canonical_device_id": canonical_id,
                "created_at": 1.0,
                "last_seen": 2.0,
                "profile": {
                    "canonical_device_id": canonical_id,
                    "display_name": "Gosund device",
                    "identifiers": {
                        "mqtt_topic_prefix": [
                            "UCL/OPS/107/EM/gosund/device-a",
                            "tasmota/discovery/C4DD573EB84C",
                        ],
                        "mqtt_identity": [
                            "1|UCL/OPS/107/EM/gosund/device-a",
                            "1|tasmota/discovery/C4DD573EB84C",
                        ],
                        "mqtt_entity_prefix": [
                            "UCL/OPS/107/EM/gosund/device-a",
                            "tasmota/discovery/C4DD573EB84C",
                        ],
                        "mqtt_entity_identity": [
                            "1|UCL/OPS/107/EM/gosund/device-a",
                            "1|tasmota/discovery/C4DD573EB84C",
                        ],
                        "strong_token": [
                            "c4dd573eb84c",
                            "devicea12345",
                        ],
                    },
                    "data": {},
                    "operations": [],
                    "last_seen": 2.0,
                    "evidence_count": 1,
                },
            },
            "urn:smartroom:device:discovery-only": {
                "canonical_device_id": "urn:smartroom:device:discovery-only",
                "created_at": 1.0,
                "last_seen": 2.0,
                "profile": {
                    "identifiers": {
                        "mqtt_topic_prefix": [
                            "tasmota/discovery/70039F90DE47"
                        ],
                        "mqtt_identity": [
                            "1|tasmota/discovery/70039F90DE47"
                        ],
                    },
                    "data": {},
                    "operations": [],
                    "last_seen": 2.0,
                },
            },
        },
        "aliases": {
            "mqtt_identity:1|tasmota/discovery/c4dd573eb84c": canonical_id,
            "strong_token:c4dd573eb84c": canonical_id,
            "mqtt_identity:1|tasmota/discovery/70039f90de47": "urn:smartroom:device:discovery-only",
            "strong_token:70039f90de47": "urn:smartroom:device:discovery-only",
        },
    }

    changed = registry.compact_mqtt_channels()
    profiles = registry.stored_profiles()

    assert changed >= 1
    assert len(profiles) == 1
    identifiers = profiles[0]["identifiers"]
    assert identifiers["mqtt_topic_prefix"] == [
        "UCL/OPS/107/EM/gosund/device-a"
    ]
    assert identifiers["strong_token"] == ["devicea12345"]
    assert not any("tasmota/discovery" in alias for alias in registry._data["aliases"])
