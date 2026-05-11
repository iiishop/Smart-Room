"""Connection manager for Home Assistant — unified REST + WebSocket lifecycle."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from ha_client.api.exceptions import HAConnectionError
from ha_client.api.rest import HARestClient
from ha_client.api.websocket import HAWebSocketClient
from ha_client.config.settings import HAConfig

if TYPE_CHECKING:
    from ha_client.core.event_bus import EventBus, EventType

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Unified connection manager for REST and WebSocket connections."""

    def __init__(self, config: HAConfig, event_bus: EventBus | None = None):
        self._config = config
        self._rest: HARestClient = HARestClient(config)
        self._ws: HAWebSocketClient = HAWebSocketClient(config)
        self._event_bus = event_bus
        self._online: bool = False
        self._reconnect_task: asyncio.Task | None = None
        self._stop_event: asyncio.Event = asyncio.Event()

    @property
    def rest(self) -> HARestClient:
        return self._rest

    @property
    def ws(self) -> HAWebSocketClient:
        return self._ws

    @property
    def online(self) -> bool:
        return self._online

    async def start(self) -> None:
        self._stop_event.clear()
        await self._connect_all()
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None
        await self._disconnect_all()

    async def _connect_all(self) -> bool:
        from ha_client.core.event_bus import EventType

        try:
            await self._rest.check_connection()
            await self._ws.connect()
            self._online = True
            if self._event_bus:
                self._event_bus.emit(EventType.CONNECTION_CHANGED, connected=True)
            logger.info("All connections established")
            return True
        except HAConnectionError as e:
            logger.warning("Connection failed: %s", e)
            self._online = False
            if self._event_bus:
                self._event_bus.emit(EventType.CONNECTION_CHANGED, connected=False)
            return False

    async def _disconnect_all(self) -> None:
        from ha_client.core.event_bus import EventType

        self._online = False
        try:
            await self._ws.disconnect()
        except Exception:
            pass
        try:
            await self._rest.close()
        except Exception:
            pass
        if self._event_bus:
            self._event_bus.emit(EventType.CONNECTION_CHANGED, connected=False)

    async def _reconnect_loop(self) -> None:
        while not self._stop_event.is_set():
            if not self._online:
                logger.info("Attempting reconnection...")
                await self._connect_all()
            await asyncio.sleep(self._config.reconnect_interval)
