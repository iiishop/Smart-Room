from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Awaitable

import aiohttp

from ha_client.api.exceptions import HAConnectionError, HAAuthError
from ha_client.config.settings import HAConfig
from ha_client.models.entity import EntityState

logger = logging.getLogger(__name__)

StateCallback = Callable[[EntityState], Awaitable[None]]
EventCallback = Callable[[dict], Awaitable[None]]
JsonDict = dict


class HAWebSocketClient:
    def __init__(self, config: HAConfig):
        self._config = config
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._msg_id = 0
        self._connected = False
        self._authenticated = False

        self._send_queue: asyncio.Queue[JsonDict] = asyncio.Queue()
        self._recv_queue: asyncio.Queue[JsonDict] = asyncio.Queue()
        self._pending: dict[int, asyncio.Future[JsonDict]] = {}

        self._state_callbacks: list[StateCallback] = []
        self._event_callbacks: dict[str, list[EventCallback]] = {}
        self._subscription_map: dict[int, str] = {}

        self._sender_task: asyncio.Task | None = None
        self._receiver_task: asyncio.Task | None = None
        self._reader_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    @property
    def connected(self) -> bool:
        return self._connected and self._authenticated

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    async def connect(self) -> bool:
        if self._connected:
            return True

        try:
            if self._session and not self._session.closed:
                await self._session.close()
            self._session = aiohttp.ClientSession()
            self._ws = await self._session.ws_connect(
                f"{self._config.ws_url}/api/websocket",
                heartbeat=30,
            )

            auth_msg = await self._ws.receive_json()
            if auth_msg is None or auth_msg.get("type") != "auth_required":
                logger.error(f"Unexpected auth message: {auth_msg}")
                return False

            ha_version = auth_msg.get("ha_version", "unknown")
            logger.info(f"Connected to HA {ha_version}, authenticating...")

            await self._ws.send_json({
                "type": "auth",
                "access_token": self._config.token,
            })

            auth_result = await self._ws.receive_json()
            if auth_result.get("type") != "auth_ok":
                logger.error(f"Authentication failed: {auth_result}")
                raise HAAuthError(
                    f"WebSocket auth failed: {auth_result.get('message', 'unknown')}"
                )

            self._connected = True
            self._authenticated = True
            self._stop_event.clear()

            self._reader_task = asyncio.create_task(self._read_loop())
            self._sender_task = asyncio.create_task(self._sender_loop())
            self._receiver_task = asyncio.create_task(self._receiver_loop())

            logger.info("WebSocket connected and authenticated")
            return True

        except aiohttp.ClientError as e:
            logger.error(f"WebSocket connection failed: {e}")
            self._connected = False
            self._authenticated = False
            raise HAConnectionError(f"WebSocket connection failed: {e}") from e
        except Exception as e:
            self._connected = False
            self._authenticated = False
            raise

    async def disconnect(self):
        self._connected = False
        self._authenticated = False
        self._stop_event.set()

        if self._sender_task:
            self._sender_task.cancel()
            self._sender_task = None
        if self._receiver_task:
            self._receiver_task.cancel()
            self._receiver_task = None
        if self._reader_task:
            self._reader_task.cancel()
            self._reader_task = None

        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()

        if self._ws and not self._ws.closed:
            await self._ws.close()
            self._ws = None

        if self._session:
            await self._session.close()
            self._session = None

        logger.info("WebSocket disconnected")

    async def subscribe_state_changes(
        self, entity_id: str | None = None
    ) -> int:
        sub_id = self._next_id()
        self._subscription_map[sub_id] = "state_changed"

        event_type = "state_changed"
        await self._send_command({
            "id": sub_id,
            "type": "subscribe_events",
            "event_type": event_type,
        })

        logger.info(f"Subscribed to state_changed events (id={sub_id})")
        return sub_id

    async def unsubscribe(self, subscription_id: int):
        if subscription_id not in self._subscription_map:
            return

        await self._send_command({
            "id": self._next_id(),
            "type": "unsubscribe_events",
            "subscription": subscription_id,
        })
        self._subscription_map.pop(subscription_id, None)

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
        payload: JsonDict = {
            "domain": domain,
            "service": service,
        }
        if service_data:
            payload["service_data"] = service_data
        if target:
            payload["target"] = target

        try:
            result = await self._send_command({
                "id": self._next_id(),
                "type": "call_service",
                **payload,
            })
            return result.get("success", False)
        except Exception:
            return False

    async def get_services(self) -> dict:
        try:
            result = await self._send_command({
                "id": self._next_id(),
                "type": "get_services",
            })
        except Exception:
            return {}

        if not result.get("success", False):
            return {}

        services = result.get("result")
        return services if isinstance(services, dict) else {}

    async def _send_command(self, message: JsonDict) -> JsonDict:
        msg_id = message.get("id")
        if msg_id is not None:
            future: asyncio.Future[JsonDict] = asyncio.get_running_loop().create_future()
            self._pending[msg_id] = future

        await self._send_queue.put(message)

        if msg_id is not None:
            try:
                result = await asyncio.wait_for(future, timeout=self._config.request_timeout)
                return result
            except asyncio.TimeoutError:
                self._pending.pop(msg_id, None)
                raise HAConnectionError(f"Request {msg_id} timed out")
            except asyncio.CancelledError:
                self._pending.pop(msg_id, None)
                raise

        return {}

    async def _sender_loop(self):
        try:
            while not self._stop_event.is_set():
                try:
                    message = await asyncio.wait_for(
                        self._send_queue.get(), timeout=0.1
                    )
                except asyncio.TimeoutError:
                    continue

                if self._ws and not self._ws.closed:
                    await self._ws.send_json(message)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Sender loop error: {e}")

    async def _read_loop(self):
        try:
            while not self._stop_event.is_set() and self._ws and not self._ws.closed:
                try:
                    msg = await asyncio.wait_for(
                        self._ws.receive_json(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                except (aiohttp.ClientConnectionError, aiohttp.WebSocketError) as e:
                    logger.warning(f"WebSocket read error: {e}")
                    self._connected = False
                    self._authenticated = False
                    break

                if msg is None:
                    continue

                msg_type = msg.get("type")

                if msg_type == "auth_required":
                    logger.warning("Server re-requested auth")
                elif msg_type == "auth_ok":
                    self._authenticated = True
                elif msg_type == "auth_invalid":
                    logger.error("Authentication became invalid")
                    self._authenticated = False
                elif msg_type == "result":
                    msg_id = msg.get("id")
                    if msg_id is not None and msg_id in self._pending:
                        future = self._pending.pop(msg_id)
                        if not future.done():
                            future.set_result(msg)
                elif msg_type == "event":
                    await self._recv_queue.put(msg)
                elif msg_type == "pong":
                    pass
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Read loop error: {e}")
        finally:
            self._connected = False
            self._authenticated = False

    async def _receiver_loop(self):
        try:
            while not self._stop_event.is_set():
                try:
                    msg = await asyncio.wait_for(
                        self._recv_queue.get(), timeout=0.1
                    )
                except asyncio.TimeoutError:
                    continue

                event = msg.get("event", {})
                event_type = event.get("event_type", "")
                event_data = event.get("data", {})

                if event_type == "state_changed":
                    await self._handle_state_change(event_data)

                callbacks = self._event_callbacks.get(event_type, [])
                general_callbacks = self._event_callbacks.get("*", [])
                for cb in callbacks + general_callbacks:
                    try:
                        await cb(msg)
                    except Exception as e:
                        logger.error(f"Event callback error: {e}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Receiver loop error: {e}")

    async def _handle_state_change(self, event_data: dict):
        entity_data = event_data.get("entity", {})
        if not entity_data:
            return

        try:
            entity_state = EntityState.from_ha_json(entity_data)
        except Exception as e:
            logger.error(f"Failed to parse entity state: {e}")
            return

        for cb in self._state_callbacks:
            try:
                await cb(entity_state)
            except Exception as e:
                logger.error(f"State callback error: {e}")
