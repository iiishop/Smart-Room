from __future__ import annotations

import logging
from typing import Any

from ..config import HAConfig
from .event_bus import EventBus
from .ha_api import HAClient

logger = logging.getLogger(__name__)


class DeviceController:
    def __init__(self, config: HAConfig, event_bus: EventBus, ha_client: HAClient) -> None:
        self._config = config
        self._event_bus = event_bus
        self._ha_client = ha_client

    async def light_turn_on(self, entity_id: str, **extra: Any) -> None:
        await self._ha_client.call_service("light", "turn_on", entity_id, **extra)

    async def light_turn_off(self, entity_id: str) -> None:
        await self._ha_client.call_service("light", "turn_off", entity_id)

    async def light_toggle(self, entity_id: str) -> None:
        await self._ha_client.call_service("light", "toggle", entity_id)

    async def light_set_brightness(self, entity_id: str, brightness_pct: int) -> None:
        ha_brightness = max(1, min(255, int(brightness_pct * 255 / 100)))
        await self._ha_client.call_service("light", "turn_on", entity_id, brightness=ha_brightness)

    async def light_set_color(self, entity_id: str, rgb: tuple[int, int, int]) -> None:
        await self._ha_client.call_service("light", "turn_on", entity_id, rgb_color=list(rgb))

    async def switch_turn_on(self, entity_id: str) -> None:
        await self._ha_client.call_service("switch", "turn_on", entity_id)

    async def switch_turn_off(self, entity_id: str) -> None:
        await self._ha_client.call_service("switch", "turn_off", entity_id)

    async def switch_toggle(self, entity_id: str) -> None:
        await self._ha_client.call_service("switch", "toggle", entity_id)
