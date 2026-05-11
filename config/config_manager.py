import json
import os
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_CONFIG = {
    "ha_host": "homeassistant.local",
    "ha_port": 8123,
    "ha_token": "",
    "ha_use_ssl": False,
    "reconnect_interval": 5,
    "reconnect_max_attempts": 10,
    "ping_interval": 30,
}


class ConfigManager:
    def __init__(self, config_path: str = "config.json"):
        self._config_path = Path(config_path)
        self._data = DEFAULT_CONFIG.copy()

        if self._config_path.exists():
            self._load()

    def _load(self):
        try:
            content = self._config_path.read_text(encoding="utf-8")
            loaded = json.loads(content)
            self._data.update(
                {k: v for k, v in loaded.items() if k in DEFAULT_CONFIG}
            )
        except (json.JSONDecodeError, IOError):
            pass

    @property
    def ha_ws_url(self) -> str:
        protocol = "wss" if self._data["ha_use_ssl"] else "ws"
        return f"{protocol}://{self._data['ha_host']}:{self._data['ha_port']}/api/websocket"

    @property
    def ha_rest_url(self) -> str:
        protocol = "https" if self._data["ha_use_ssl"] else "http"
        return f"{protocol}://{self._data['ha_host']}:{self._data['ha_port']}/api"

    @property
    def token(self) -> str:
        env_token = os.environ.get("HA_TOKEN")
        if env_token:
            return env_token
        return self._data.get("ha_token", "")

    @property
    def reconnect_interval(self) -> float:
        return float(self._data["reconnect_interval"])

    @property
    def max_reconnect_attempts(self) -> int:
        return int(self._data["reconnect_max_attempts"])

    @property
    def ping_interval(self) -> float:
        return float(self._data["ping_interval"])

    def validate(self) -> bool:
        if not self.token:
            return False
        for url in (self.ha_ws_url, self.ha_rest_url):
            parsed = urlparse(url)
            if not parsed.hostname or not parsed.port:
                return False
        return True

    def save(self) -> None:
        out = {k: self._data[k] for k in DEFAULT_CONFIG}
        self._config_path.write_text(
            json.dumps(out, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def update(self, **kwargs) -> None:
        for key, value in kwargs.items():
            if key in DEFAULT_CONFIG:
                self._data[key] = value
