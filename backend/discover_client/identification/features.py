"""Deterministic semantic feature extraction for deduplicated devices."""

from __future__ import annotations

from dataclasses import dataclass

from discover_client.identification.device import Device

CAPABILITY_KEYWORDS = {
    "temperature": "温度传感",
    "temp": "温度传感",
    "humidity": "湿度传感",
    "humid": "湿度传感",
    "light": "光照传感",
    "lux": "光照传感",
    "motion": "运动检测",
    "pir": "人体感应",
    "switch": "开关控制",
    "relay": "继电器",
    "power": "功耗监测",
    "energy": "能耗监测",
    "battery": "电池供电",
    "voltage": "电压监测",
    "current": "电流监测",
    "co2": "CO2传感",
    "air": "空气质量",
    "door": "门磁",
    "window": "窗磁",
    "leak": "漏水检测",
    "smoke": "烟雾检测",
    "button": "按钮",
    "display": "显示屏",
    "unit": "数值型数据",
    "value": "数值型数据",
    "state": "状态值",
    "status": "状态值",
    "rgb": "RGB灯控",
    "brightness": "亮度控制",
    "on": "布尔状态",
    "off": "布尔状态",
}


@dataclass
class DeviceFeatures:
    device_id: str
    capabilities: list[str]
    protocols: list[str]
    vendor_hints: list[str]
    topic_prefixes: list[str]
    evidence_count: int


class FeatureExtractor:
    """Extract semantic features from accumulated device evidence."""

    def extract(self, device: Device) -> DeviceFeatures:
        tags = set()

        for topic_prefix in device.topic_prefixes:
            self._collect_matches(topic_prefix, tags)

        for key in device.payload_keys:
            self._collect_matches(key, tags)

        protocols = set()
        if device.topic_prefixes:
            protocols.add("MQTT")
        if any("_matter" in service_type for service_type in device.service_types):
            protocols.add("Matter")
        if any("_home-assistant" in service_type for service_type in device.service_types):
            protocols.add("HomeAssistant")

        vendor_hints = set()
        for topic_prefix in device.topic_prefixes:
            vendor = topic_prefix.split("/", 1)[0].strip()
            if vendor and not vendor.replace(".", "").isdigit():
                vendor_hints.add(vendor)
        if device.vendor:
            vendor_hints.add(device.vendor)

        return DeviceFeatures(
            device_id=device.device_id,
            capabilities=sorted(tags),
            protocols=sorted(protocols),
            vendor_hints=sorted(vendor_hints),
            topic_prefixes=sorted(device.topic_prefixes),
            evidence_count=device.total_evidence_count,
        )

    def _collect_matches(self, value: str, tags: set[str]) -> None:
        lowered = value.lower()
        for keyword, tag in CAPABILITY_KEYWORDS.items():
            if keyword in lowered:
                tags.add(tag)
