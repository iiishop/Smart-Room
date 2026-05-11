import logging

from ha_client.api.connection import ConnectionManager
from ha_client.core.event_bus import EventBus, EventType
from ha_client.models.device import Device, create_device

logger = logging.getLogger(__name__)


class DeviceManager:
    def __init__(self, connection_mgr: ConnectionManager, event_bus: EventBus):
        self._connection_mgr = connection_mgr
        self._event_bus = event_bus
        self._devices: dict[str, Device] = {}
        self._sync_active: bool = False

    @property
    def devices(self) -> dict[str, Device]:
        return self._devices

    def get_device(self, entity_id: str) -> Device | None:
        return self._devices.get(entity_id)

    def get_devices_by_domain(self, domain) -> list[Device]:
        from ha_client.models.entity import EntityDomain

        if isinstance(domain, EntityDomain):
            domain = domain
        else:
            domain = EntityDomain(domain)
        return [d for d in self._devices.values() if d.domain == domain]

    async def load_devices(self) -> list[Device]:
        rest = self._connection_mgr.rest
        states = await rest.get_states()

        new_devices: dict[str, Device] = {}
        for state in states:
            try:
                device = create_device(state)
                new_devices[device.entity_id] = device
            except Exception as e:
                logger.warning(f"Failed to create device for {state.entity_id}: {e}")

        for entity_id, device in new_devices.items():
            if entity_id not in self._devices:
                self._event_bus.emit(
                    EventType.DEVICE_ADDED,
                    entity_id=entity_id,
                    device=device,
                )

        for entity_id in list(self._devices.keys()):
            if entity_id not in new_devices:
                self._event_bus.emit(
                    EventType.DEVICE_REMOVED,
                    entity_id=entity_id,
                )

        self._devices = new_devices
        logger.info(f"Loaded {len(self._devices)} devices")
        return list(self._devices.values())

    async def start_sync(self):
        if self._sync_active:
            return

        self._sync_active = True

        async def on_state_changed(entity):
            if not self._sync_active:
                return
            try:
                entity_id = entity.entity_id
                if entity_id in self._devices:
                    device = self._devices[entity_id]
                    device.state = entity.state
                    device.attributes = entity.attributes
                    self._event_bus.emit(
                        EventType.STATE_CHANGED,
                        entity_id=entity_id,
                        device=device,
                        state=entity,
                    )
            except Exception as e:
                logger.error(f"State sync error: {e}")

        self._connection_mgr.ws.on_state_change(on_state_changed)
        await self._connection_mgr.ws.subscribe_state_changes()
        logger.info("Device sync started")

    async def stop(self):
        self._sync_active = False
