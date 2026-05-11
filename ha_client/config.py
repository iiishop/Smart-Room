from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class HAConfig:
    url: str = "http://homeassistant.local:8123"
    token: str = ""
    verify_ssl: bool = False
    websocket_timeout: float = 30.0
    request_timeout: float = 10.0
    reconnect_delay: float = 3.0
    max_reconnect_attempts: int = 0
    log_max_lines: int = 500

    @property
    def ws_url(self) -> str:
        return self.url.replace("http://", "ws://").replace("https://", "wss://") + "/api/websocket"


def load_config(path: str = "config.yaml") -> HAConfig:
    config_path = Path(path)
    if not config_path.exists():
        return HAConfig()

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    env_url = os.environ.get("HA_URL")
    env_token = os.environ.get("HA_TOKEN")

    return HAConfig(
        url=env_url or data.get("url", "http://homeassistant.local:8123"),
        token=env_token or data.get("token", ""),
        verify_ssl=data.get("verify_ssl", False),
        websocket_timeout=float(data.get("websocket_timeout", 30.0)),
        request_timeout=float(data.get("request_timeout", 10.0)),
        reconnect_delay=float(data.get("reconnect_delay", 3.0)),
        max_reconnect_attempts=int(data.get("max_reconnect_attempts", 0)),
        log_max_lines=int(data.get("log_max_lines", 500)),
    )
