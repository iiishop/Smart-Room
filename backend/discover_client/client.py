"""Discover Client orchestrator."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

from discover_client.output import OutputQueue
from discover_client.source import Source, SourceConfig, SourceEvent
from discover_client.sources import get as get_source_class

logger = logging.getLogger(__name__)


class DiscoverClient:
    """Owns the set of active sources and their lifecycle."""

    def __init__(self, output: OutputQueue | None = None) -> None:
        self.output = output or OutputQueue()
        self._subscribers: list[Callable[[SourceEvent], None]] = []
        self._sources: dict[str, Source] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    async def start(self, enabled: list[SourceConfig]) -> None:
        """Instantiate and activate all enabled sources concurrently."""
        tasks = []
        enabled_configs = [config for config in enabled if config.enabled]
        for config in enabled_configs:
            tasks.append(self._start_one(config))

        if not tasks:
            return

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for config, result in zip(enabled_configs, results):
            if isinstance(result, Exception):
                logger.error("Failed to start source '%s': %s", config.source_id, result)

    async def _start_one(self, config: SourceConfig) -> None:
        cls = get_source_class(config.source_type)
        source = cls(config, emit=self._on_event)
        self._sources[config.source_id] = source
        await source.start()

    async def stop(self) -> None:
        """Gracefully shut down all active sources."""
        stops = [source.stop() for source in self._sources.values()]
        if stops:
            await asyncio.gather(*stops, return_exceptions=True)
        self._sources.clear()

    async def add_source(self, config: SourceConfig) -> None:
        """Register and start a source at runtime."""
        await self._start_one(config)

    async def remove_source(self, source_id: str) -> None:
        """Stop and remove a source by id."""
        source = self._sources.pop(source_id, None)
        if source:
            await source.stop()

    def subscribe(self, callback: Callable[[SourceEvent], None]) -> None:
        """Register a subscriber callback for all events."""
        self._subscribers.append(callback)

    def _on_event(self, event: SourceEvent) -> None:
        """Route events to the output queue and all subscribers."""
        self.output.put(event)
        for subscriber in self._subscribers:
            try:
                subscriber(event)
            except Exception:
                logger.exception("Subscriber callback failed")
