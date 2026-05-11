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
        http_url = self.url.rstrip("/")
        if http_url.startswith("https://"):
            return http_url.replace("https://", "wss://", 1) + "/api/websocket"
        return http_url.replace("http://", "ws://", 1) + "/api/websocket"

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

    return HAConfig(
        url=data.get("url", HAConfig.url),
        token=data.get("token", HAConfig.token),
        verify_ssl=data.get("verify_ssl", HAConfig.verify_ssl),
        reconnect_interval=data.get("reconnect_interval", HAConfig.reconnect_interval),
        request_timeout=data.get("request_timeout", HAConfig.request_timeout),
    )


DEFAULT_CONFIG_TEMPLATE = """# Home Assistant Connection Configuration
url: "http://homeassistant.local:8123"
token: "YOUR_LONG_LIVED_ACCESS_TOKEN_HERE"
verify_ssl: false
reconnect_interval: 10.0
request_timeout: 30.0
"""


def create_default_config(path: str) -> HAConfig:
    config_path = Path(path)
    if config_path.exists():
        return load_config(path)

    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(DEFAULT_CONFIG_TEMPLATE)

    return HAConfig()
