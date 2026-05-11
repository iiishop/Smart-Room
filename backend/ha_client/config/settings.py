from __future__ import annotations

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
    def ws_url(self) -> str:
        url = self.url.rstrip("/")
        if url.startswith("https://"):
            return f"wss://{url[8:]}"
        elif url.startswith("http://"):
            return f"ws://{url[7:]}"
        return f"ws://{url}"

    def validate(self) -> None:
        if not self.url:
            raise ValueError("HAConfig.url must not be empty")
        if not self.token:
            raise ValueError("HAConfig.token must not be empty")


def load_config(path: str) -> HAConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    config = HAConfig(
        url=data.get("url", HAConfig.url),
        token=data.get("token", HAConfig.token),
        verify_ssl=data.get("verify_ssl", HAConfig.verify_ssl),
        reconnect_interval=data.get("reconnect_interval", HAConfig.reconnect_interval),
        request_timeout=data.get("request_timeout", HAConfig.request_timeout),
    )
    config.validate()
    return config


def create_default_config(path: str) -> HAConfig:
    config = HAConfig()
    config_path = Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    default_data = {
        "url": "http://homeassistant.local:8123",
        "token": "YOUR_LONG_LIVED_ACCESS_TOKEN",
        "verify_ssl": False,
        "reconnect_interval": 10.0,
        "request_timeout": 30.0,
    }

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(default_data, f, default_flow_style=False, allow_unicode=True)

    return config
