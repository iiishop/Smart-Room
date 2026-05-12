from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ha_client.core.event_bus import EventBus, EventType
from wifi_positioning.tracker.models import DeviceState, SmoothedPosition


class DeviceTracker:
    def __init__(self, event_bus: EventBus, offline_timeout_seconds: int = 60):
        self._event_bus = event_bus
        self._offline_timeout = timedelta(seconds=offline_timeout_seconds)
        self._devices: dict[str, DeviceState] = {}

    def update_position(self, position: SmoothedPosition) -> None:
        self.mark_stale_offline(now=position.timestamp)

        existing = self._devices.get(position.mac)
        was_present = existing.present if existing else False

        device = DeviceState(
            mac=position.mac,
            name=position.name or (existing.name if existing else position.mac),
            x=position.x,
            y=position.y,
            direction=position.direction,
            distance=position.distance,
            confidence=position.confidence,
            timestamp=position.timestamp,
            zone=position.zone,
            present=True,
        )
        self._devices[position.mac] = device

        if existing is None:
            self._event_bus.emit(EventType.DEVICE_ADDED, mac=position.mac, device=device)

        self._event_bus.emit(
            EventType.STATE_CHANGED,
            mac=position.mac,
            old_state="home" if was_present else "not_home",
            new_state=device.location_name,
            device=device,
        )

    def update_presence(self, mac: str, present: bool) -> None:
        device = self._devices.get(mac)
        if device is None:
            return
        old_state = device.location_name
        device.present = present
        self._event_bus.emit(
            EventType.STATE_CHANGED,
            mac=mac,
            old_state=old_state,
            new_state=device.location_name,
            device=device,
        )

    def get_device(self, mac: str) -> DeviceState | None:
        self.mark_stale_offline()
        return self._devices.get(mac)

    def list_devices(self) -> list[DeviceState]:
        self.mark_stale_offline()
        return list(self._devices.values())

    def mark_stale_offline(self, now: datetime | None = None) -> None:
        current = now or datetime.now(UTC)
        for device in self._devices.values():
            if current - device.timestamp > self._offline_timeout and device.present:
                device.present = False
