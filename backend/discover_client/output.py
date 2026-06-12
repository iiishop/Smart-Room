"""Output mechanism for Discover Client events."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from discover_client.source import SourceEvent


class OutputQueue:
    """An unbounded async queue that sources push events into.

    Downstream modules pull events via async iteration or callback.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[SourceEvent] = asyncio.Queue()

    def put(self, event: SourceEvent) -> None:
        """Push an event into the queue (non-blocking)."""
        self._queue.put_nowait(event)

    async def get(self) -> SourceEvent:
        """Await the next event."""
        return await self._queue.get()

    def __aiter__(self) -> AsyncIterator[SourceEvent]:
        """Iterate over events as they arrive."""
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[SourceEvent]:
        while True:
            yield await self.get()

    @property
    def depth(self) -> int:
        """Current number of un-consumed events."""
        return self._queue.qsize()
