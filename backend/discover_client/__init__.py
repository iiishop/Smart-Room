"""Discover Client - data-source aggregation layer for Smart Room."""

from discover_client.client import DiscoverClient
from discover_client.source import Source, SourceEvent, SourceConfig
from discover_client.output import OutputQueue
from discover_client.config import load_config, SourceTypeSchema, SCHEMAS
from discover_client.sources import registered_types

__all__ = [
    "DiscoverClient",
    "Source",
    "SourceEvent",
    "SourceConfig",
    "OutputQueue",
    "load_config",
    "SourceTypeSchema",
    "SCHEMAS",
    "registered_types",
]
