from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime
from enum import Enum, auto
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


class EventType(Enum):
    CONNECTED = auto()
    DISCONNECTED = auto()
    STATE_CHANGED = auto()
    ERROR = auto()
    LOG_MESSAGE = auto()


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[EventType, list[Callable[..., Coroutine[Any, Any, None]]]] = defaultdict(list)
        self._sync_subscribers: dict[EventType, list[Callable[..., None]]] = defaultdict(list)

    def subscribe(self, event_type: EventType, callback: Callable[..., Coroutine[Any, Any, None]]) -> None:
        self._subscribers[event_type].append(callback)

    def subscribe_sync(self, event_type: EventType, callback: Callable[..., None]) -> None:
        self._sync_subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: EventType, callback: Callable[..., Any]) -> None:
        if asyncio.iscoroutinefunction(callback):
            subscribers = self._subscribers.get(event_type, [])
            if callback in subscribers:
                subscribers.remove(callback)
        else:
            subscribers = self._sync_subscribers.get(event_type, [])
            if callback in subscribers:
                subscribers.remove(callback)

    async def emit(self, event_type: EventType, **data: Any) -> None:
        for cb in self._sync_subscribers.get(event_type, []):
            try:
                cb(**data)
            except Exception:
                logger.exception("Sync subscriber error for %s", event_type)

        for cb in self._subscribers.get(event_type, []):
            try:
                await cb(**data)
            except Exception:
                logger.exception("Async subscriber error for %s", event_type)

    def emit_sync(self, event_type: EventType, **data: Any) -> None:
        for cb in self._sync_subscribers.get(event_type, []):
            try:
                cb(**data)
            except Exception:
                logger.exception("Sync subscriber error for %s", event_type)
