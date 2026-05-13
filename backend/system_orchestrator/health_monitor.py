from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any


class HealthMonitor:
    def collect(self, services: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "wifi_scanner": self._scanner_health(services.get("wifi_scanner")),
            "location_estimator": self._estimator_health(services.get("location_estimator")),
            "ha_integration": self._ha_health(services.get("ha_integration")),
            "quest3_datasource": self._quest_health(services.get("quest3_datasource")),
        }

    def _scanner_health(self, scanner: Any) -> dict[str, Any]:
        if scanner is None:
            return self._status("unavailable", "service not loaded")
        if self._is_started(scanner) and self._has_data_activity(scanner):
            return self._status("healthy")
        if self._is_started(scanner):
            return self._status("degraded", "started but no recent data")
        return self._status("stopped")

    def _estimator_health(self, estimator: Any) -> dict[str, Any]:
        if estimator is None:
            return self._status("unavailable", "service not loaded")
        if self._is_started(estimator) and self._has_computation_activity(estimator):
            return self._status("healthy")
        if self._is_started(estimator):
            return self._status("degraded", "started but no recent calculation")
        return self._status("stopped")

    def _ha_health(self, integration: Any) -> dict[str, Any]:
        if integration is None:
            return self._status("unavailable", "service not loaded")
        if not self._is_started(integration):
            return self._status("stopped")

        connected = self._read_bool(
            integration,
            keys=("mqtt_connected", "is_mqtt_connected", "connected", "is_connected"),
        )
        if connected:
            return self._status("healthy")
        return self._status("degraded", "MQTT not connected")

    def _quest_health(self, datasource: Any) -> dict[str, Any]:
        if datasource is None:
            return self._status("disabled")
        if not self._is_started(datasource):
            return self._status("stopped")
        connected = self._read_bool(
            datasource,
            keys=("connected", "is_connected", "socket_connected", "stream_connected"),
        )
        if connected:
            return self._status("healthy")
        return self._status("degraded", "Quest3 datasource disconnected")

    def _is_started(self, service: Any) -> bool:
        return self._read_bool(service, keys=("started", "is_started", "running", "is_running"))

    def _has_data_activity(self, service: Any) -> bool:
        return self._read_bool(
            service,
            keys=("has_recent_data", "recent_data", "producing_data"),
        ) or bool(getattr(service, "last_output_at", None))

    def _has_computation_activity(self, service: Any) -> bool:
        return self._read_bool(
            service,
            keys=("has_recent_result", "recent_result", "producing_result"),
        ) or bool(getattr(service, "last_estimate_at", None))

    def _read_bool(self, obj: Any, *, keys: tuple[str, ...]) -> bool:
        for key in keys:
            if not hasattr(obj, key):
                continue
            value = getattr(obj, key)
            try:
                value = value() if callable(value) else value
            except TypeError:
                continue
            if isinstance(value, bool):
                return value
        return False

    def _status(self, status: str, message: str = "") -> dict[str, Any]:
        data: dict[str, Any] = {"status": status}
        if message:
            data["message"] = message
        return data
