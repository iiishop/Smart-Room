import asyncio
import logging

from ha_client.api.exceptions import HAConnectionError
from ha_client.api.rest import HARestClient
from ha_client.api.websocket import HAWebSocketClient
from ha_client.config.settings import HAConfig

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self, config: HAConfig):
        self._config = config
        self._rest = HARestClient(config)
        self._ws = HAWebSocketClient(config)
        self._reconnect_task: asyncio.Task | None = None
        self._online_event = asyncio.Event()
        self._shutdown = False
        self._ws_started = False

    @property
    def rest(self) -> HARestClient:
        return self._rest

    @property
    def ws(self) -> HAWebSocketClient:
        return self._ws

    @property
    def online(self) -> bool:
        return self._ws.connected

    @property
    def reconnect_interval(self) -> float:
        return self._config.reconnect_interval

    async def start(self):
        self._shutdown = False
        self._online_event.clear()

        if not await self._rest.check_connection():
            logger.warning("REST connection check failed, but continuing")

        await self._connect_ws()

        if not self._ws.connected:
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())
        else:
            self._online_event.set()

    async def _connect_ws(self) -> bool:
        try:
            await self._ws.connect()
            self._online_event.set()
            return True
        except HAConnectionError as e:
            logger.warning(f"WebSocket connection failed: {e}")
            return False

    async def _reconnect_loop(self):
        attempt = 0
        while not self._shutdown:
            await asyncio.sleep(self._get_backoff(attempt))
            if self._shutdown:
                break
            if self._ws.connected:
                attempt = 0
                self._online_event.set()
                await asyncio.sleep(self._config.reconnect_interval)
                continue

            attempt += 1
            logger.info(
                f"Reconnect attempt {attempt} "
                f"(interval: {self._get_backoff(attempt):.1f}s)"
            )
            if await self._connect_ws():
                attempt = 0

    def _get_backoff(self, attempt: int) -> float:
        if attempt == 0:
            return self._config.reconnect_interval
        return min(self._config.reconnect_interval * (2 ** (attempt - 1)), 300.0)

    async def stop(self):
        self._shutdown = True
        self._online_event.clear()

        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
        self._reconnect_task = None

        await self._ws.disconnect()
        await self._rest.close()
        logger.info("ConnectionManager stopped")
