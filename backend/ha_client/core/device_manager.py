"""Device manager — maintains device registry, discovery, and state sync."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ha_client.core.event_bus import EventBus, EventType
from ha_client.models.device import Device, create_device
from ha_client.models.entity import EntityDomain, EntityState

if TYPE_CHECKING:
    from ha_client.api.connection import ConnectionManager

logger = logging.getLogger(__name__)


class DeviceManager:
    """Maintains device registry, manages device discovery and state cache."""

    def __init__(self, connection_mgr: ConnectionManager, event_bus: EventBus):
        self._connection_mgr = connection_mgr
        self._event_bus = event_bus
        self._devices: dict[str, Device] = {}
        self._sync_active: bool = False

    @property
    def devices(self) -> dict[str, Device]:
        return dict(self._devices)

    def get_device(self, entity_id: str) -> Device | None:
        return self._devices.get(entity_id)

    def get_devices_by_domain(self, domain: EntityDomain) -> list[Device]:
        return [d for d in self._devices.values() if d.domain == domain]

    async def load_devices(self) -> list[Device]:
        rest = self._connection_mgr.rest
        states = await rest.get_states()
        self._devices.clear()

        for entity_state in states:
            device = create_device(entity_state)
            self._devices[device.entity_id] = device

        logger.info("Loaded %d devices", len(self._devices))
        return list(self._devices.values())

    async def start_sync(self) -> None:
        if self._sync_active:
            return

        self._sync_active = True
        ws = self._connection_mgr.ws

        async def on_state_change(entity_state: EntityState) -> None:
            await self._handle_state_change(entity_state)

        ws.on_state_change(on_state_change)

        if ws.connected:
            await ws.subscribe_state_changes()

        logger.info("State sync started")

    async def _handle_state_change(self, entity_state: EntityState) -> None:
        entity_id = entity_state.entity_id

        if entity_id in self._devices:
            self._devices[entity_id].update_state(entity_state)
        else:
            device = create_device(entity_state)
            self._devices[entity_id] = device
            self._event_bus.emit(
                EventType.DEVICE_ADDED, entity_id=entity_id, device=device
            )
            logger.info("New device discovered: %s", entity_id)

        self._event_bus.emit(
            EventType.STATE_CHANGED,
            entity_id=entity_id,
            old_state=None,
            new_state=entity_state.state,
            device=self._devices[entity_id],
        )

    async def stop(self) -> None:
        self._sync_active = False
        self._devices.clear()
        logger.info("Device manager stopped")
