"""Bridge between asyncio event loop and Tkinter main loop for async UI updates."""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import tkinter as tk
from collections.abc import Callable
from typing import Any, Coroutine, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class AsyncTkBridge:
    """Bridges a background asyncio event loop with the Tkinter main thread.

    Creates a dedicated daemon thread running an asyncio event loop for
    executing async coroutines. UI updates are dispatched back to the main
    thread via tkinter virtual events for thread safety.
    """

    _EVENT_FLUSH = "<<AsyncBridgeFlush>>"

    def __init__(self, root: tk.Tk):
        self._root = root
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._root.bind(self._EVENT_FLUSH, self._drain_ui_queue)
        self._start()

    def _start(self) -> None:
        if self._running:
            return
        self._running = True
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="asyncio-bridge", daemon=True)
        self._thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        except Exception:
            logger.exception("AsyncTkBridge event loop crashed")
        finally:
            self._loop.close()

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        return self._loop

    def drain_ui(self) -> None:
        """Process all pending UI callbacks from the internal queue.

        Must be called from the main thread. This is a convenience for test
        environments where root.mainloop() is not running.
        """
        self._drain_ui_queue(None)

    def schedule_ui(self, callback: Callable[[], None]) -> None:
        """Enqueue *callback* for execution on the Tkinter main thread.

        Thread-safe: may be called from any thread.
        """
        self._ui_queue.put(callback)
        try:
            self._root.event_generate(self._EVENT_FLUSH, when="tail")
        except (tk.TclError, RuntimeError):
            pass

    def _drain_ui_queue(self, _event: tk.Event | None = None) -> None:
        while not self._ui_queue.empty():
            try:
                cb = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                cb()
            except Exception:
                logger.exception("AsyncTkBridge: UI callback failed")

    def run_async(
        self,
        coro: Coroutine[Any, Any, T],
        on_result: Callable[[T], None] | None = None,
    ) -> None:
        """Schedule *coro* on the background event loop.

        If *on_result* is provided, it is called on the UI thread with the
        coroutine's return value once it completes.
        """
        if self._loop is None or not self._running:
            logger.warning("AsyncTkBridge: run_async called while not running")
            return

        def _task_done(future: asyncio.Future[T]) -> None:
            try:
                result = future.result()
            except Exception:
                logger.exception("AsyncTkBridge: coroutine failed")
                return
            if on_result is not None:
                self.schedule_ui(lambda r=result: on_result(r))

        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        future.add_done_callback(_task_done)

    def shutdown(self) -> None:
        """Stop the background event loop and join the thread."""
        if not self._running:
            return
        self._running = False

        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)

        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=3.0)

        self._loop = None
        self._thread = None
        try:
            self._root.unbind(self._EVENT_FLUSH)
        except (tk.TclError, RuntimeError):
            pass
        logger.debug("AsyncTkBridge shut down")
