from __future__ import annotations

import json
import logging
import socket
from urllib.error import URLError
from urllib import request

from .models import Quest3Config, Quest3WiFiData


class DataRelayer:
    """Relay Quest 3 WiFi data to backend via UDP or HTTP POST."""

    def __init__(self, config: Quest3Config) -> None:
        self._config = config
        self._logger = logging.getLogger(__name__)

    def to_json_bytes(self, data: Quest3WiFiData) -> bytes:
        return json.dumps(data.to_dict(), ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )

    def send_udp(self, data: Quest3WiFiData) -> None:
        packet = self.to_json_bytes(data)
        target = (self._config.backend_host, self._config.backend_udp_port)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(2.0)
                sock.sendto(packet, target)
        except OSError as ex:
            self._logger.warning("UDP relay failed to %s:%s: %s", target[0], target[1], ex)
            raise

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
        try:
            with request.urlopen(req, timeout=5) as resp:  # nosec B310
                return int(resp.status)
        except URLError as ex:
            self._logger.warning("HTTP relay failed to %s: %s", target, ex)
            raise
