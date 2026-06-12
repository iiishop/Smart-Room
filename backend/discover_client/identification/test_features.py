from discover_client.identification.device import Device
from discover_client.identification.features import FeatureExtractor


def test_feature_extractor_detects_mqtt_capabilities_protocol_and_vendor_hint() -> None:
    extractor = FeatureExtractor()

    device = Device(device_id="device-1")
    device.topic_prefixes = {"govee/H5179/a1b2c3d4e5f6/temperature", "govee/H5179/a1b2c3d4e5f6/humidity"}
    device.payload_keys = {"unit", "value"}
    device.total_evidence_count = 36

    features = extractor.extract(device)

    assert features.device_id == "device-1"
    assert features.capabilities == ["数值型数据", "温度传感", "湿度传感"]
    assert features.protocols == ["MQTT"]
    assert features.vendor_hints == ["govee"]
    assert features.topic_prefixes == [
        "govee/H5179/a1b2c3d4e5f6/humidity",
        "govee/H5179/a1b2c3d4e5f6/temperature",
    ]
    assert features.evidence_count == 36


def test_feature_extractor_detects_matter_and_vendor_fallbacks() -> None:
    extractor = FeatureExtractor()

    device = Device(device_id="device-3")
    device.service_types = {"_matter._tcp.local.", "_home-assistant._tcp.local."}
    device.vendor = "TP-Link Systems Inc."
    device.hostnames = {"58044F9ADC05.local."}
    device.total_evidence_count = 7

    features = extractor.extract(device)

    assert features.capabilities == []
    assert features.protocols == ["HomeAssistant", "Matter"]
    assert features.vendor_hints == ["TP-Link Systems Inc."]
    assert features.topic_prefixes == []
    assert features.evidence_count == 7
