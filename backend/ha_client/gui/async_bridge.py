"""AsyncTkBridge - thread-safe bridge between asyncio and Tkinter main loop."""

from __future__ import annotations

import asyncio
import logging
import tkinter as tk

logger = logging.getLogger(__name__)


class AsyncTkBridge:
    """Bridges async event loop with Tkinter main thread.

    - schedule_ui(): schedule a callback to run on the Tkinter thread
    - run_async(): run an async coroutine from any thread via the event loop
    """

    def __init__(self, root: tk.Tk, event_loop: asyncio.AbstractEventLoop):
        self._root = root
        self._loop = event_loop

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        return self._loop

    def schedule_ui(self, callback, *args):
        """Schedule *callback* to run on the Tkinter main thread via root.after(0)."""
        try:
            self._root.after(0, callback, *args)
        except Exception:
            logger.exception("schedule_ui failed")

    def run_async(self, coro):
        """Run an async coroutine in the event loop, blocking until result.

        Must NOT be called from the Tkinter main thread (would deadlock).
        Use schedule_ui + run_async together from non-UI threads.
        """
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    def run_async_background(self, coro):
        """Schedule a coroutine in the event loop, returns immediately with a Future."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop)
