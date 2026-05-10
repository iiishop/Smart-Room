from __future__ import annotations

import asyncio
import logging
from typing import Any

from ha_client.api.exceptions import HAConnectionError
from ha_client.api.rest import HARestClient
from ha_client.api.websocket import HAWebSocketClient
from ha_client.config.settings import HAConfig

logger = logging.getLogger(__name__)

MAX_BACKOFF = 300.0


class ConnectionManager:
    def __init__(self, config: HAConfig):
        self._config = config
        self._rest: HARestClient | None = None
        self._ws: HAWebSocketClient | None = None

        self._reconnect_task: asyncio.Task | None = None
        self._backoff = 0.0
        self._stop_reconnect = asyncio.Event()
        self._online_event = asyncio.Event()
        self._offline_event = asyncio.Event()
        self._offline_event.set()
        self._online = False
        self._shutting_down = False

        self._state_change_callbacks: list = []

    @property
    def rest(self) -> HARestClient:
        if self._rest is None:
            self._rest = HARestClient(self._config)
        return self._rest

    @property
    def ws(self) -> HAWebSocketClient:
        if self._ws is None:
            self._ws = HAWebSocketClient(self._config)
        return self._ws

    @property
    def online(self) -> bool:
        return self._online

    @property
    def reconnect_interval(self) -> float:
        return self._config.reconnect_interval

    async def start(self):
        self._shutting_down = False
        self._stop_reconnect.clear()

        success = await self._connect_ws()

        if success:
            self._set_online(True)
        else:
            self._start_reconnect_loop()

    async def stop(self):
        self._shutting_down = True
        self._stop_reconnect.set()

        if self._reconnect_task:
            self._reconnect_task.cancel()
            self._reconnect_task = None

        if self._ws and self._ws.connected:
            await self._ws.disconnect()

        if self._rest:
            await self._rest.close()

        self._set_online(False)
        logger.info("ConnectionManager stopped")

    async def _connect_ws(self) -> bool:
        try:
            if self._ws is None:
                self._ws = HAWebSocketClient(self._config)

            if self._ws.connected:
                return True

            result = await self._ws.connect()
            if result:
                self._ws.on_state_change(self._on_ws_state_change)
                self._backoff = 0.0
            return result
        except HAConnectionError:
            return False
        except Exception as e:
            logger.error(f"WS connect failed: {e}")
            return False

    async def _on_ws_state_change(self, entity_state):
        for cb in self._state_change_callbacks:
            try:
                await cb(entity_state)
            except Exception as e:
                logger.error(f"State change callback error: {e}")

    def on_state_change(self, callback):
        self._state_change_callbacks.append(callback)

    def _set_online(self, online: bool):
        if online == self._online:
            return
        self._online = online
        if online:
            self._online_event.set()
            self._offline_event.clear()
            logger.info("ConnectionManager: online")
        else:
            self._offline_event.set()
            self._online_event.clear()
            logger.warning("ConnectionManager: offline")

    def _start_reconnect_loop(self):
        if self._reconnect_task and not self._reconnect_task.done():
            return

        self._reconnect_task = asyncio.create_task(self._reconnect_loop())
        logger.info("Reconnect loop started")

    async def _reconnect_loop(self):
        while not self._stop_reconnect.is_set() and not self._shutting_down:
            if self._ws and self._ws.connected:
                await self._stop_reconnect.wait()
                continue

            if self._backoff == 0.0:
                self._backoff = self.reconnect_interval
            else:
                self._backoff = min(self._backoff * 2, MAX_BACKOFF)

            logger.info(f"Reconnecting in {self._backoff:.0f}s...")
            try:
                await asyncio.wait_for(
                    self._stop_reconnect.wait(), timeout=self._backoff
                )
                continue
            except asyncio.TimeoutError:
                pass

            if self._shutting_down:
                break

            success = await self._connect_ws()
            if success:
                self._set_online(True)
                logger.info("Reconnect successful, backoff reset")
            else:
                if self._online:
                    self._set_online(False)

    async def wait_online(self, timeout: float | None = None) -> bool:
        try:
            await asyncio.wait_for(self._online_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False
