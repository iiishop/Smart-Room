from __future__ import annotations

import json
import socket
from urllib import request

from .models import Quest3Config, Quest3WiFiData


class DataRelayer:
    """Relay Quest 3 WiFi data to backend via UDP or HTTP POST."""

    def __init__(self, config: Quest3Config) -> None:
        self._config = config

    def to_json_bytes(self, data: Quest3WiFiData) -> bytes:
        return json.dumps(data.to_dict(), ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )

    def send_udp(self, data: Quest3WiFiData) -> None:
        packet = self.to_json_bytes(data)
        target = (self._config.backend_host, self._config.backend_udp_port)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(packet, target)

    def send_http(self, data: Quest3WiFiData, endpoint: str | None = None) -> int:
        target = endpoint or self._config.backend_http_endpoint
        if not target:
            raise ValueError("HTTP endpoint is required")

        payload = self.to_json_bytes(data)
        req = request.Request(
            target,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(req, timeout=5) as resp:  # nosec B310
            return int(resp.status)
