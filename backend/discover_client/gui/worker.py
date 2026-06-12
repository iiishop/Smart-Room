"""Background worker that runs DiscoverClient's asyncio loop."""

import asyncio
import threading

from PySide6.QtCore import QObject, Signal

from discover_client.client import DiscoverClient
from discover_client.source import SourceEvent
from discover_client.config import load_config


class Worker(QObject):
    """Runs DiscoverClient on a background thread, bridges events to Qt."""

    event_received = Signal(str, str, dict)
    status_changed = Signal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._client: DiscoverClient | None = None
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._async_main())
        finally:
            loop.close()

    async def _async_main(self) -> None:
        self._client = DiscoverClient()
        configs = load_config()

        def on_event(event: SourceEvent) -> None:
            self.event_received.emit(
                event.source_id, event.event_type, event.payload
            )
            # Track real connection state from status events
            if event.event_type == "status":
                msg = event.payload.get("msg", "")
                if "connected" in msg or msg == "scanning":
                    self.status_changed.emit(event.source_id, True)
                elif msg == "stopped" or "Disconnected" in str(msg):
                    self.status_changed.emit(event.source_id, False)

        self._client.subscribe(on_event)

        # All enabled sources start as not-yet-connected
        for c in configs:
            if c.enabled:
                self.status_changed.emit(c.source_id, False)

        try:
            await self._client.start(configs)
            while self._running:
                await asyncio.sleep(0.1)
        finally:
            await self._client.stop()
