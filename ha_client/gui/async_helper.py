import asyncio
import logging
import tkinter as tk
from concurrent.futures import Future

logger = logging.getLogger(__name__)


class AsyncTkHelper:
    def __init__(self, tk_root: tk.Tk, interval: float = 0.05):
        self._root = tk_root
        self._interval = interval
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False
        self._tasks: set[asyncio.Task] = set()

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            raise RuntimeError("AsyncTkHelper not started")
        return self._loop

    def start(self):
        if self._running:
            return
        self._running = True
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        def _tick():
            if not self._running:
                return
            try:
                self._loop.call_soon(self._loop.stop)
                self._loop.run_forever()
            except Exception as e:
                logger.error(f"Async tick error: {e}")
            self._root.after(int(self._interval * 1000), _tick)

        _tick()

    def stop(self):
        self._running = False
        if self._loop and not self._loop.is_closed():
            for task in list(self._tasks):
                task.cancel()
            self._tasks.clear()

            async def _cleanup():
                pass

            try:
                self._loop.run_until_complete(_cleanup())
            except Exception:
                pass
            self._loop.close()
        self._loop = None

    def run_task(self, coro) -> None:
        if self._loop is None:
            raise RuntimeError("AsyncTkHelper not started")

        async def _wrapper():
            try:
                return await coro
            except Exception as e:
                logger.error(f"Async task error: {e}")
                raise

        task = self._loop.create_task(_wrapper())
        self._tasks.add(task)
        task.add_done_callback(lambda t: self._tasks.discard(t))
