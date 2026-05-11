"""Configuration management for Home Assistant client."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class HAConfig:
    url: str = "http://homeassistant.local:8123"
    token: str = ""
    verify_ssl: bool = False
    reconnect_interval: float = 10.0
    request_timeout: float = 30.0

    @property
    def base_url(self) -> str:
        return self.url.rstrip("/")

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }


def load_config(path: str) -> HAConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    ha_data = data.get("home_assistant", data)
    return HAConfig(
        url=ha_data.get("url", HAConfig.url),
        token=ha_data.get("token", HAConfig.token),
        verify_ssl=ha_data.get("verify_ssl", HAConfig.verify_ssl),
        reconnect_interval=ha_data.get("reconnect_interval", HAConfig.reconnect_interval),
        request_timeout=ha_data.get("request_timeout", HAConfig.request_timeout),
    )


def create_default_config(path: str) -> HAConfig:
    default = {
        "home_assistant": {
            "url": "http://homeassistant.local:8123",
            "token": "YOUR_LONG_LIVED_ACCESS_TOKEN",
            "verify_ssl": False,
            "reconnect_interval": 10.0,
            "request_timeout": 30.0,
        }
    }
    config_path = Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(default, f, default_flow_style=False, allow_unicode=True)
    return load_config(path)
