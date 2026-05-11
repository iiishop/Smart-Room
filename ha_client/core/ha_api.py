from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

import aiohttp

from ..config import HAConfig
from .event_bus import EventBus, EventType

logger = logging.getLogger(__name__)


class HAClient:
    def __init__(self, config: HAConfig, event_bus: EventBus) -> None:
        self._config = config
        self._event_bus = event_bus
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._ws_msg_id: int = 0
        self._running: bool = False
        self._connected: bool = False
        self._reconnect_task: Optional[asyncio.Task[Any]] = None

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        if self._running:
            return
        self._running = True
        self._session = aiohttp.ClientSession()
        await self._connect_websocket()

    async def disconnect(self) -> None:
        self._running = False
        if self._reconnect_task:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session:
            await self._session.close()
            self._session = None
        if self._connected:
            self._connected = False
            await self._event_bus.emit(EventType.DISCONNECTED)

    async def rest_get(self, path: str) -> dict[str, Any]:
        if not self._session:
            return {}
        headers = {"Authorization": f"Bearer {self._config.token}"}
        try:
            async with self._session.get(
                f"{self._config.url}{path}",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self._config.request_timeout),
                ssl=self._config.verify_ssl,
            ) as resp:
                return await resp.json()
        except Exception as e:
            logger.error("REST GET %s failed: %s", path, e)
            return {}

    async def rest_post(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        if not self._session:
            return {}
        headers = {
            "Authorization": f"Bearer {self._config.token}",
            "Content-Type": "application/json",
        }
        try:
            async with self._session.post(
                f"{self._config.url}{path}",
                headers=headers,
                json=data,
                timeout=aiohttp.ClientTimeout(total=self._config.request_timeout),
                ssl=self._config.verify_ssl,
            ) as resp:
                return await resp.json()
        except Exception as e:
            logger.error("REST POST %s failed: %s", path, e)
            return {}

    async def call_service(self, domain: str, service: str, entity_id: str, **extra: Any) -> None:
        data: dict[str, Any] = {"entity_id": entity_id}
        data.update(extra)
        await self.rest_post(f"/api/services/{domain}/{service}", data)

    async def _connect_websocket(self) -> None:
        while self._running:
            try:
                async with self._session.ws_connect(  # type: ignore[union-attr]
                    self._config.ws_url,
                    timeout=self._config.websocket_timeout,
                    ssl=self._config.verify_ssl,
                ) as ws:
                    self._ws = ws
                    auth_msg = await ws.receive_json()
                    if auth_msg.get("type") != "auth_required":
                        logger.error("Unexpected WS first message: %s", auth_msg)
                        await self._handle_disconnect()
                        continue

                    await ws.send_json({"type": "auth", "access_token": self._config.token})
                    auth_result = await ws.receive_json()
                    if auth_result.get("type") != "auth_ok":
                        logger.error("WS auth failed: %s", auth_result)
                        await self._event_bus.emit(EventType.ERROR, message=f"WebSocket auth failed: {auth_result.get('message', 'unknown')}")
                        await self._handle_disconnect()
                        continue

                    await self._subscribe_state_changes(ws)

                    self._connected = True
                    await self._event_bus.emit(EventType.CONNECTED, url=self._config.url)

                    await self._read_loop(ws)

            except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError) as e:
                logger.warning("WebSocket connection error: %s", e)
            except Exception as e:
                logger.exception("WebSocket unexpected error: %s", e)
                await self._event_bus.emit(EventType.ERROR, message=str(e))

            await self._handle_disconnect()

            if self._running:
                delay = self._config.reconnect_delay
                logger.info("Reconnecting in %.1fs...", delay)
                await asyncio.sleep(delay)

    async def _subscribe_state_changes(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self._ws_msg_id += 1
        await ws.send_json({
            "id": self._ws_msg_id,
            "type": "subscribe_events",
            "event_type": "state_changed",
        })

    async def _read_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    await self._handle_ws_message(data)
                except json.JSONDecodeError:
                    logger.warning("Invalid WS JSON: %s", msg.data)
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break

    async def _handle_ws_message(self, data: dict[str, Any]) -> None:
        msg_type = data.get("type")

        if msg_type == "event":
            event_data = data.get("event", {})
            event_type = event_data.get("event_type")
            if event_type == "state_changed":
                new_state = event_data.get("data", {}).get("new_state", {})
                if new_state:
                    await self._event_bus.emit(
                        EventType.STATE_CHANGED,
                        entity_id=new_state.get("entity_id", ""),
                        state=new_state.get("state", ""),
                        attributes=new_state.get("attributes", {}),
                    )

        elif msg_type == "result" and not data.get("success"):
            error_msg = data.get("error", {}).get("message", "unknown error")
            logger.error("WS command failed: %s", error_msg)

    async def _handle_disconnect(self) -> None:
        if self._connected:
            self._connected = False
            await self._event_bus.emit(EventType.DISCONNECTED)
        self._ws = None
