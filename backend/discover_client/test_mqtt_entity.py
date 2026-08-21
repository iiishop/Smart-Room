from discover_client.identification.device import Device
from discover_client.mqtt_entity import group_physical_devices


def _mqtt_device(device_id: str, prefix: str) -> Device:
    return Device(
        device_id=device_id,
        total_evidence_count=1,
        last_seen=1.0,
        topic_prefixes={prefix},
        mqtt_identities={f"broker-1|{prefix}"},
    )


def test_repeated_terminal_schema_is_grouped_as_channels() -> None:
    devices = [
        _mqtt_device("device-1", "UCL/OPSEBO/206/2ByDoorToCommonArea2C2/LMS"),
        _mqtt_device("device-2", "UCL/OPSEBO/206/2ByDoorToCommonArea2C2/TPS"),
        _mqtt_device("device-3", "UCL/OPSEBO/206/2ByDoorToCommonArea2C2/CDS"),
        _mqtt_device("device-4", "UCL/OPSEBO/203/BKitchen/LMS"),
        _mqtt_device("device-5", "UCL/OPSEBO/203/BKitchen/TPS"),
        _mqtt_device("device-6", "UCL/OPSEBO/203/BKitchen/CDS"),
    ]

    grouped = group_physical_devices(devices)

    assert len(grouped) == 2
    first = next(
        item
        for item in grouped
        if "UCL/OPSEBO/206/2ByDoorToCommonArea2C2" in item.mqtt_entity_prefixes
    )
    assert first.mqtt_channels == {"LMS", "TPS", "CDS"}
    assert first.member_device_ids == {"device-1", "device-2", "device-3"}


def test_unique_sibling_device_names_are_not_collapsed() -> None:
    devices = [
        _mqtt_device("device-1", "student/ucfnlwa/lfl/LFL-H2D-939"),
        _mqtt_device("device-2", "student/ucfnlwa/lfl/LFL-H2D-053"),
        _mqtt_device("device-3", "student/ucfnlwa/lfl/LFL-PrusaXL-A"),
    ]

    grouped = group_physical_devices(devices)

    assert len(grouped) == 3
