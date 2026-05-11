"""Device controller — encapsulates REST service calls for device control."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ha_client.api.exceptions import HAError
from ha_client.core.event_bus import EventType

if TYPE_CHECKING:
    from ha_client.core.device_manager import DeviceManager

logger = logging.getLogger(__name__)


class DeviceController:
    """Device control interface, wraps REST service calls via DeviceManager."""

    def __init__(self, device_manager: DeviceManager):
        self._device_manager = device_manager
        self._event_bus = device_manager.event_bus

    @property
    def _rest(self):
        return self._device_manager.connection_mgr.rest

    async def turn_on(self, entity_id: str, **kwargs) -> bool:
        try:
            result = await self._rest.turn_on(entity_id, **kwargs)
            self._event_bus.emit(
                EventType.STATE_CHANGED,
                entity_id=entity_id,
                action="turn_on",
            )
            return result
        except HAError as e:
            self._event_bus.emit(EventType.ERROR, entity_id=entity_id, error=str(e))
            raise

    async def turn_off(self, entity_id: str) -> bool:
        try:
            result = await self._rest.turn_off(entity_id)
            self._event_bus.emit(
                EventType.STATE_CHANGED,
                entity_id=entity_id,
                action="turn_off",
            )
            return result
        except HAError as e:
            self._event_bus.emit(EventType.ERROR, entity_id=entity_id, error=str(e))
            raise

    async def toggle(self, entity_id: str) -> bool:
        try:
            result = await self._rest.toggle(entity_id)
            self._event_bus.emit(
                EventType.STATE_CHANGED,
                entity_id=entity_id,
                action="toggle",
            )
            return result
        except HAError as e:
            self._event_bus.emit(EventType.ERROR, entity_id=entity_id, error=str(e))
            raise

    async def set_brightness(self, entity_id: str, brightness: int) -> bool:
        domain = entity_id.split(".", 1)[0]
        b = max(0, min(255, brightness))
        try:
            result = await self._rest.call_service(
                domain, "turn_on", entity_id, {"brightness": b}
            )
            self._event_bus.emit(
                EventType.STATE_CHANGED,
                entity_id=entity_id,
                action="set_brightness",
                brightness=b,
            )
            return result
        except HAError as e:
            self._event_bus.emit(EventType.ERROR, entity_id=entity_id, error=str(e))
            raise

    async def set_color(self, entity_id: str, rgb: tuple[int, int, int]) -> bool:
        domain = entity_id.split(".", 1)[0]
        try:
            result = await self._rest.call_service(
                domain,
                "turn_on",
                entity_id,
                {"rgb_color": list(rgb)},
            )
            self._event_bus.emit(
                EventType.STATE_CHANGED,
                entity_id=entity_id,
                action="set_color",
                rgb=rgb,
            )
            return result
        except HAError as e:
            self._event_bus.emit(EventType.ERROR, entity_id=entity_id, error=str(e))
            raise

    async def call_service(
        self,
        domain: str,
        service: str,
        entity_id: str | None = None,
        data: dict | None = None,
    ) -> bool:
        try:
            result = await self._rest.call_service(
                domain, service, entity_id, data
            )
            self._event_bus.emit(
                EventType.STATE_CHANGED,
                entity_id=entity_id,
                action=f"{domain}/{service}",
            )
            return result
        except HAError as e:
            self._event_bus.emit(EventType.ERROR, entity_id=entity_id, error=str(e))
            raise
