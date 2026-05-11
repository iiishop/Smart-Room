import asyncio
import json
import logging
from collections.abc import Callable, Awaitable

import aiohttp

from ha_client.api.exceptions import (
    HAConnectionError,
    HAAuthError,
    HAError,
)
from ha_client.config.settings import HAConfig
from ha_client.models.entity import EntityState

logger = logging.getLogger(__name__)

StateCallback = Callable[[EntityState], Awaitable[None]]
EventCallback = Callable[[dict], Awaitable[None]]


class HAWebSocketClient:
    def __init__(self, config: HAConfig):
        self._config = config
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._msg_id: int = 0
        self._connected: bool = False
        self._running: bool = False
        self._receive_task: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}

        self._state_callbacks: list[StateCallback] = []
        self._event_callbacks: dict[str, list[EventCallback]] = {}
        self._subscriptions: dict[int, str] = {}

    @property
    def connected(self) -> bool:
        return self._connected

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    async def connect(self) -> bool:
        if self._connected:
            return True

        try:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession()

            self._ws = await self._session.ws_connect(
                self._config.ws_url,
                heartbeat=30,
            )

            auth_msg = await self._ws.receive_json()
            if auth_msg.get("type") != "auth_required":
                raise HAConnectionError(
                    f"Unexpected message type: {auth_msg.get('type')}"
                )

            await self._ws.send_json({
                "type": "auth",
                "access_token": self._config.token,
            })

            auth_result = await self._ws.receive_json()
            if auth_result.get("type") != "auth_ok":
                raise HAAuthError(
                    f"WebSocket auth failed: {auth_result.get('message', 'unknown error')}"
                )

            self._connected = True
            self._running = True
            self._receive_task = asyncio.create_task(self._receive_loop())
            logger.info("WebSocket connected and authenticated")
            return True

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise HAConnectionError(f"WebSocket connection failed: {e}") from e
        except HAError:
            raise
        except Exception as e:
            raise HAConnectionError(f"WebSocket unexpected error: {e}") from e

    async def disconnect(self):
        self._running = False
        self._connected = False

        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        self._receive_task = None

        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._ws = None

        self._pending.clear()
        self._subscriptions.clear()

    async def _receive_loop(self):
        while self._running and self._ws and not self._ws.closed:
            try:
                msg = await self._ws.receive_json()
                await self._handle_message(msg)
            except (aiohttp.ClientError, asyncio.TimeoutError):
                if self._running:
                    logger.warning("WebSocket receive error, connection lost")
                    self._connected = False
                    break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"WebSocket receive error: {e}")
                if self._running:
                    self._connected = False
                    break

        self._connected = False

    async def _handle_message(self, msg: dict):
        msg_type = msg.get("type", "")
        msg_id = msg.get("id")

        if msg_id is not None and msg_id in self._pending:
            future = self._pending.pop(msg_id)
            if not future.done():
                if msg_type == "result":
                    future.set_result(msg)
                else:
                    future.set_exception(
                        HAError(msg.get("error", {}).get("message", "Unknown error"))
                    )
            return

        if msg_type == "event":
            event_data = msg.get("event", {})
            event_type = event_data.get("event_type", "")

            if event_type == "state_changed":
                await self._handle_state_changed(event_data.get("data", {}))
            elif event_type in self._event_callbacks:
                for cb in self._event_callbacks[event_type]:
                    try:
                        await cb(event_data.get("data", {}))
                    except Exception as e:
                        logger.error(f"Event callback error: {e}")

    async def _handle_state_changed(self, data: dict):
        new_state_data = data.get("new_state")

        if new_state_data and isinstance(new_state_data, dict):
            try:
                entity = EntityState.from_dict(new_state_data)
                for cb in self._state_callbacks:
                    try:
                        await cb(entity)
                    except Exception as e:
                        logger.error(f"State change callback error: {e}")
            except Exception as e:
                logger.error(f"Failed to parse state changed data: {e}")

    async def subscribe_state_changes(
        self, entity_id: str | None = None
    ) -> int:
        if not self._connected:
            raise HAConnectionError("WebSocket not connected")

        msg_id = self._next_id()
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = future

        payload: dict = {"type": "subscribe_events", "event_type": "state_changed"}
        self._subscriptions[msg_id] = "state_changed"

        await self._ws.send_json({**payload, "id": msg_id})

        try:
            result = await asyncio.wait_for(future, timeout=10.0)
            return msg_id
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise HAConnectionError("Subscribe state_changed timed out")

    async def unsubscribe(self, subscription_id: int):
        self._subscriptions.pop(subscription_id, None)

    def on_state_change(self, callback: StateCallback):
        self._state_callbacks.append(callback)

    def on_event(self, event_type: str, callback: EventCallback):
        if event_type not in self._event_callbacks:
            self._event_callbacks[event_type] = []
        self._event_callbacks[event_type].append(callback)

    async def call_service(
        self,
        domain: str,
        service: str,
        service_data: dict | None = None,
        target: dict | None = None,
    ) -> bool:
        if not self._connected:
            raise HAConnectionError("WebSocket not connected")

        msg_id = self._next_id()
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = future

        payload: dict = {
            "type": "call_service",
            "domain": domain,
            "service": service,
        }
        if service_data:
            payload["service_data"] = service_data
        if target:
            payload["target"] = target

        await self._ws.send_json({**payload, "id": msg_id})

        try:
            await asyncio.wait_for(future, timeout=10.0)
            return True
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise HAConnectionError("Service call timed out")
        except HAError:
            return False
