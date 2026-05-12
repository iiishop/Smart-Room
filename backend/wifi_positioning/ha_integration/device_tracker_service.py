from __future__ import annotations

from typing import Any

from ha_client.api.rest import HARestClient


class HADeviceTrackerService:
    def __init__(self, rest_client: HARestClient):
        self._rest_client = rest_client

    async def see_device(
        self,
        device_mac: str,
        location_name: str,
        gps: tuple[float, float] | None,
        attributes: dict[str, Any] | None,
    ) -> bool:
        service_data: dict[str, Any] = {
            "mac": device_mac,
            "location_name": location_name,
        }
        if gps is not None:
            service_data["gps"] = [gps[0], gps[1]]
        if attributes:
            service_data["attributes"] = attributes

        return await self._rest_client.call_service(
            domain="device_tracker",
            service="see",
            service_data=service_data,
        )
