"""Home Assistant WebSocket API asynchronous client."""

import asyncio
import json
import logging
from collections.abc import Callable, Awaitable

import aiohttp

from ha_client.api.exceptions import HAConnectionError, HAAuthError, HAServiceError
from ha_client.config.settings import HAConfig
from ha_client.models.entity import EntityState

logger = logging.getLogger(__name__)

StateCallback = Callable[[EntityState], Awaitable[None]]
EventCallback = Callable[[dict], Awaitable[None]]


class HAWebSocketClient:
    """Async WebSocket client for Home Assistant."""

    def __init__(self, config: HAConfig):
        self._config = config
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._msg_id: int = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._state_callbacks: list[StateCallback] = []
        self._event_callbacks: dict[str, list[EventCallback]] = {}
        self._receive_task: asyncio.Task | None = None
        self._connected: bool = False
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._connected

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    def _ws_url(self) -> str:
        base = self._config.base_url
        if base.startswith("https://"):
            return base.replace("https://", "wss://", 1) + "/api/websocket"
        return base.replace("http://", "ws://", 1) + "/api/websocket"

    async def connect(self) -> bool:
        if self._connected:
            return True

        try:
            self._session = aiohttp.ClientSession()
            self._ws = await self._session.ws_connect(self._ws_url())

            auth_msg = await self._ws.receive_json()

            if auth_msg.get("type") != "auth_required":
                raise HAConnectionError(
                    f"Unexpected auth message: {auth_msg.get('type')}"
                )

            await self._ws.send_json({
                "type": "auth",
                "access_token": self._config.token,
            })

            auth_response = await self._ws.receive_json()

            if auth_response.get("type") != "auth_ok":
                raise HAAuthError(
                    f"Authentication failed: {auth_response.get('message', 'unknown error')}"
                )

            self._connected = True
            self._receive_task = asyncio.create_task(self._receive_loop())
            logger.info("WebSocket connected and authenticated")
            return True

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            self._connected = False
            raise HAConnectionError(f"WebSocket connection failed: {e}") from e

    async def disconnect(self) -> None:
        async with self._lock:
            self._connected = False

            if self._receive_task:
                self._receive_task.cancel()
                try:
                    await self._receive_task
                except asyncio.CancelledError:
                    pass
                self._receive_task = None

            if self._ws:
                await self._ws.close()
                self._ws = None

            if self._session:
                await self._session.close()
                self._session = None

            for future in self._pending.values():
                if not future.done():
                    future.set_exception(HAConnectionError("Disconnected"))
            self._pending.clear()
            logger.info("WebSocket disconnected")

    async def _receive_loop(self) -> None:
        try:
            while self._connected and self._ws:
                msg = await self._ws.receive_json()
                await self._handle_message(msg)
        except (aiohttp.ClientError, asyncio.CancelledError):
            pass
        finally:
            self._connected = False

    async def _handle_message(self, msg: dict) -> None:
        msg_type = msg.get("type")

        if msg_type == "result":
            msg_id = msg.get("id")
            if msg_id and msg_id in self._pending:
                future = self._pending.pop(msg_id)
                if not future.done():
                    if msg.get("success", True):
                        future.set_result(msg.get("result"))
                    else:
                        future.set_exception(
                            HAServiceError(msg.get("error", {}).get("message", "Unknown error"))
                        )

        elif msg_type == "event":
            event_data = msg.get("event", {})
            event_type = event_data.get("event_type", "")

            if event_type == "state_changed":
                new_state_data = event_data.get("data", {}).get("new_state")
                if new_state_data:
                    entity_state = EntityState.from_ha_response(new_state_data)
                    for cb in self._state_callbacks:
                        try:
                            await cb(entity_state)
                        except Exception:
                            logger.exception("State callback error")

            if event_type in self._event_callbacks:
                for cb in self._event_callbacks[event_type]:
                    try:
                        await cb(event_data)
                    except Exception:
                        logger.exception("Event callback error")

    async def _send_and_wait(self, message: dict) -> dict:
        msg_id = self._next_id()
        message["id"] = msg_id
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future

        try:
            await self._ws.send_json(message)
            return await future
        except aiohttp.ClientError as e:
            self._pending.pop(msg_id, None)
            raise HAConnectionError(f"WebSocket send failed: {e}") from e

    async def subscribe_state_changes(self, entity_id: str | None = None) -> int:
        if not self._connected:
            raise HAConnectionError("Not connected")

        if entity_id:
            result = await self._send_and_wait({
                "type": "subscribe_trigger",
                "trigger": {
                    "platform": "state",
                    "entity_id": entity_id,
                },
            })
        else:
            result = await self._send_and_wait({
                "type": "subscribe_events",
                "event_type": "state_changed",
            })

        return result.get("id", 0) if isinstance(result, dict) else 0

    async def unsubscribe(self, subscription_id: int) -> None:
        if not self._connected:
            return
        try:
            await self._send_and_wait({
                "type": "unsubscribe_events",
                "subscription": subscription_id,
            })
        except HAConnectionError:
            pass

    def on_state_change(self, callback: StateCallback) -> None:
        if callback not in self._state_callbacks:
            self._state_callbacks.append(callback)

    def off_state_change(self, callback: StateCallback) -> None:
        try:
            self._state_callbacks.remove(callback)
        except ValueError:
            pass

    def on_event(self, event_type: str, callback: EventCallback) -> None:
        if event_type not in self._event_callbacks:
            self._event_callbacks[event_type] = []
        if callback not in self._event_callbacks[event_type]:
            self._event_callbacks[event_type].append(callback)

    def off_event(self, event_type: str, callback: EventCallback) -> None:
        if event_type in self._event_callbacks:
            try:
                self._event_callbacks[event_type].remove(callback)
            except ValueError:
                pass

    async def call_service(
        self,
        domain: str,
        service: str,
        service_data: dict | None = None,
        target: dict | None = None,
    ) -> bool:
        if not self._connected:
            raise HAConnectionError("Not connected")

        message: dict = {
            "type": "call_service",
            "domain": domain,
            "service": service,
        }
        if service_data:
            message["service_data"] = service_data
        if target:
            message["target"] = target

        await self._send_and_wait(message)
        return True
