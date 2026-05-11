"""Internal event bus for decoupled component communication."""

import asyncio
import logging
from collections.abc import Callable
from enum import Enum, auto

logger = logging.getLogger(__name__)


class EventType(Enum):
    CONNECTION_CHANGED = auto()
    STATE_CHANGED = auto()
    DEVICE_ADDED = auto()
    DEVICE_REMOVED = auto()
    ERROR = auto()


class EventBus:
    """Simple pub/sub event bus supporting sync and async callbacks."""

    def __init__(self):
        self._subscribers: dict[EventType, list[Callable]] = {}
        self._lock = asyncio.Lock()

    def subscribe(self, event_type: EventType, callback: Callable) -> None:
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        if callback not in self._subscribers[event_type]:
            self._subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: EventType, callback: Callable) -> None:
        if event_type in self._subscribers:
            try:
                self._subscribers[event_type].remove(callback)
            except ValueError:
                pass

    def emit(self, event_type: EventType, **data) -> None:
        callbacks = self._subscribers.get(event_type, [])
        for cb in callbacks:
            try:
                result = cb(**data)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception:
                logger.exception(
                    "Error in event callback for %s", event_type.name
                )
