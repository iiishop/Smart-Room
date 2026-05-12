from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ha_client.core.event_bus import EventBus, EventType
from wifi_positioning.tracker.device_tracker import DeviceTracker
from wifi_positioning.tracker.models import SmoothedPosition
from wifi_positioning.tracker.zone_manager import ZoneManager


def test_zone_manager_classify_inside_outside_and_boundary() -> None:
    manager = ZoneManager(
        {
            "living_room": [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)],
        }
    )

    assert manager.classify(2.0, 1.0) == "living_room"
    assert manager.classify(10.0, 10.0) is None
    assert manager.classify(0.0, 2.0) == "living_room"


def test_device_tracker_add_update_and_timeout() -> None:
    event_bus = EventBus()
    tracker = DeviceTracker(event_bus=event_bus, offline_timeout_seconds=10)
    events: list[EventType] = []

    event_bus.subscribe(EventType.DEVICE_ADDED, lambda **_: events.append(EventType.DEVICE_ADDED))
    event_bus.subscribe(EventType.STATE_CHANGED, lambda **_: events.append(EventType.STATE_CHANGED))

    ts = datetime(2026, 1, 1, tzinfo=UTC)
    tracker.update_position(
        SmoothedPosition(
            mac="aa:bb:cc:dd:ee:ff",
            x=1.2,
            y=2.4,
            direction=90.0,
            distance=3.1,
            confidence=0.95,
            timestamp=ts,
        )
    )

    assert tracker.get_device("aa:bb:cc:dd:ee:ff") is not None
    assert events == [EventType.DEVICE_ADDED, EventType.STATE_CHANGED]

    tracker.update_presence("aa:bb:cc:dd:ee:ff", present=False)
    assert tracker.get_device("aa:bb:cc:dd:ee:ff").location_name == "not_home"

    tracker.mark_stale_offline(now=ts + timedelta(seconds=11))
    assert tracker.get_device("aa:bb:cc:dd:ee:ff").present is False
