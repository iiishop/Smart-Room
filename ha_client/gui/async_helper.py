from __future__ import annotations

import asyncio
import logging
import threading
import tkinter as tk
from concurrent.futures import Future
from typing import Any, Coroutine, Optional

logger = logging.getLogger(__name__)


class AsyncTkHelper:
    def __init__(self, tk_root: tk.Tk, interval: float = 0.05) -> None:
        self._root = tk_root
        self._interval = interval
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running: bool = False

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
        return self._loop

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def run_task(self, coro: Coroutine[Any, Any, Any]) -> None:
        def _callback(fut: asyncio.Future[Any]) -> None:
            try:
                fut.result()
            except Exception:
                logger.exception("Async task failed")

        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        future.add_done_callback(_callback)

    def run_task_with_callback(
        self, coro: Coroutine[Any, Any, Any], callback: Any
    ) -> None:
        def _run() -> None:
            future = asyncio.run_coroutine_threadsafe(coro, self.loop)
            future.add_done_callback(lambda f: self._root.after(0, callback, f))

        self._root.after(0, _run)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self._loop.run_forever()
