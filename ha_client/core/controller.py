import logging

from ha_client.api.connection import ConnectionManager
from ha_client.api.exceptions import HAServiceError
from ha_client.core.event_bus import EventBus, EventType

logger = logging.getLogger(__name__)


class DeviceController:
    def __init__(self, connection_mgr: ConnectionManager, event_bus: EventBus):
        self._connection_mgr = connection_mgr
        self._event_bus = event_bus

    async def turn_on(self, entity_id: str, **kwargs) -> bool:
        try:
            result = await self._connection_mgr.rest.turn_on(entity_id, **kwargs)
            return result
        except HAServiceError as e:
            self._event_bus.emit(
                EventType.ERROR,
                message=f"turn_on failed for {entity_id}: {e}",
                entity_id=entity_id,
            )
            raise

    async def turn_off(self, entity_id: str) -> bool:
        try:
            result = await self._connection_mgr.rest.turn_off(entity_id)
            return result
        except HAServiceError as e:
            self._event_bus.emit(
                EventType.ERROR,
                message=f"turn_off failed for {entity_id}: {e}",
                entity_id=entity_id,
            )
            raise

    async def toggle(self, entity_id: str) -> bool:
        try:
            result = await self._connection_mgr.rest.toggle(entity_id)
            return result
        except HAServiceError as e:
            self._event_bus.emit(
                EventType.ERROR,
                message=f"toggle failed for {entity_id}: {e}",
                entity_id=entity_id,
            )
            raise

    async def set_brightness(self, entity_id: str, brightness: int) -> bool:
        return await self.turn_on(entity_id, brightness=brightness)

    async def set_color(self, entity_id: str, rgb: tuple[int, int, int]) -> bool:
        return await self.turn_on(entity_id, rgb_color=list(rgb))

    async def call_service(
        self,
        domain: str,
        service: str,
        entity_id: str | None = None,
        data: dict | None = None,
    ) -> bool:
        try:
            return await self._connection_mgr.rest.call_service(
                domain, service, entity_id=entity_id, service_data=data
            )
        except HAServiceError as e:
            self._event_bus.emit(
                EventType.ERROR,
                message=f"Service call failed for {domain}/{service}: {e}",
            )
            raise
