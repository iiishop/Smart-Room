from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class WifiModuleConfig:
    ha_url: str = field(
        default_factory=lambda: os.getenv("HA_URL", "http://homeassistant.local:8123")
    )
    ha_token: str = field(
        default_factory=lambda: os.getenv("HA_ACCESS_TOKEN", "")
    )
    ha_verify_ssl: bool = field(
        default_factory=lambda: os.getenv("HA_VERIFY_SSL", "true").lower() == "true"
    )
    ha_request_timeout: int = field(
        default_factory=lambda: int(os.getenv("HA_REQUEST_TIMEOUT", "10"))
    )

    poll_interval_seconds: float = field(
        default_factory=lambda: float(os.getenv("WIFI_POLL_INTERVAL", "1.0"))
    )

    mqtt_broker_host: str = field(
        default_factory=lambda: os.getenv("MQTT_BROKER_HOST", "localhost")
    )
    mqtt_broker_port: int = field(
        default_factory=lambda: int(os.getenv("MQTT_BROKER_PORT", "1883"))
    )
    mqtt_username: str = field(
        default_factory=lambda: os.getenv("MQTT_USERNAME", "")
    )
    mqtt_password: str = field(
        default_factory=lambda: os.getenv("MQTT_PASSWORD", "")
    )
    mqtt_topic_prefix: str = field(
        default_factory=lambda: os.getenv("MQTT_TOPIC_PREFIX", "wifi")
    )
    mqtt_client_id: str = field(
        default_factory=lambda: os.getenv("MQTT_CLIENT_ID", "smart-room-wifi-collector")
    )
    mqtt_qos: int = field(
        default_factory=lambda: int(os.getenv("MQTT_QOS", "1"))
    )
    mqtt_retain: bool = field(
        default_factory=lambda: os.getenv("MQTT_RETAIN", "false").lower() == "true"
    )

    device_offline_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("WIFI_DEVICE_OFFLINE_TIMEOUT", "60.0"))
    )

    router_type: str = field(
        default_factory=lambda: os.getenv("WIFI_ROUTER_TYPE", "")
    )
    router_host: str = field(
        default_factory=lambda: os.getenv("WIFI_ROUTER_HOST", "192.168.1.1")
    )
    router_username: str = field(
        default_factory=lambda: os.getenv("WIFI_ROUTER_USERNAME", "admin")
    )
    router_password: str = field(
        default_factory=lambda: os.getenv("WIFI_ROUTER_PASSWORD", "")
    )

    target_macs: list[str] = field(default_factory=list)

    def __post_init__(self):
        target_macs_env = os.getenv("WIFI_TARGET_MACS", "")
        if target_macs_env and not self.target_macs:
            self.target_macs = [
                mac.strip().lower()
                for mac in target_macs_env.split(",")
                if mac.strip()
            ]

    @property
    def ha_enabled(self) -> bool:
        return bool(self.ha_url and self.ha_token)

    @property
    def mqtt_enabled(self) -> bool:
        return bool(self.mqtt_broker_host)

    @property
    def router_enabled(self) -> bool:
        return bool(self.router_type and self.router_host)
