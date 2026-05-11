from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .collector import WifiCollector


def create_router(get_collector: Callable[[], WifiCollector | None]) -> APIRouter:
    router = APIRouter(prefix="/api/devices", tags=["wifi"])

    def _collector() -> WifiCollector:
        c = get_collector()
        if c is None:
            raise HTTPException(status_code=503, detail="WiFi collector not initialized")
        return c

    @router.get("/wifi")
    async def get_wifi_devices() -> dict:
        return _collector().get_device_list().model_dump()

    class WifiStatusResponse(BaseModel):
        running: bool
        ha_connected: bool
        mqtt_connected: bool
        device_count: int

    @router.get("/wifi/status", response_model=WifiStatusResponse)
    async def get_wifi_status() -> WifiStatusResponse:
        status = _collector().status
        return WifiStatusResponse(**status)

    class WifiRefreshResponse(BaseModel):
        ok: bool
        message: str

    @router.post("/wifi/refresh", response_model=WifiRefreshResponse)
    async def force_wifi_refresh() -> WifiRefreshResponse:
        try:
            await _collector().force_refresh()
            return WifiRefreshResponse(ok=True, message="WiFi device refresh triggered")
        except Exception as exc:
            return WifiRefreshResponse(ok=False, message=str(exc))

    return router
