"""Core abstractions: Source ABC, SourceEvent, SourceConfig."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class SourceEvent:
    """Unified output envelope for all source types.

    Attributes:
        source_id: Which source produced this (e.g. "mqtt-lab").
        source_type: Source type key (e.g. "mqtt", "mdns").
        timestamp: time.time() when the event was captured.
        event_type: One of "data", "discovery", "error", "status".
        payload: Opaque dict; structure depends on source_type.
    """

    source_id: str
    source_type: str
    timestamp: float
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceConfig:
    """User-facing configuration for one source.

    Attributes:
        source_id: User-chosen name (e.g. "mqtt-lab").
        source_type: Must match a registered Source class (e.g. "mqtt").
        enabled: Whether this source should be started.
        settings: Type-specific dict; validated per source_type.
    """

    source_id: str
    source_type: str
    enabled: bool = True
    settings: dict[str, Any] = field(default_factory=dict)


class Source(ABC):
    """Abstract base for all data sources.

    Each concrete source manages its own connection and event emission.
    The orchestrator only calls start() / stop().
    """

    def __init__(self, config: SourceConfig, emit: Callable[[SourceEvent], None]) -> None:
        self.config = config
        self.source_id = config.source_id
        self.source_type = config.source_type
        self._emit = emit

    @abstractmethod
    async def start(self) -> None:
        """Activate this source. Must not raise unhandled exceptions."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully shut down this source."""
        ...

    def emit(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        """Push a SourceEvent through the output channel."""
        import time

        self._emit(
            SourceEvent(
                source_id=self.source_id,
                source_type=self.source_type,
                timestamp=time.time(),
                event_type=event_type,
                payload=payload or {},
            )
        )
