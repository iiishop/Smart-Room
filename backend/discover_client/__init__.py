"""Discover Client - data-source aggregation layer for Smart Room."""

from __future__ import annotations

from importlib import import_module

from discover_client.source import Source, SourceConfig, SourceEvent

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


def __getattr__(name: str):
    if name == "DiscoverClient":
        return import_module("discover_client.client").DiscoverClient
    if name == "OutputQueue":
        return import_module("discover_client.output").OutputQueue
    if name in {"load_config", "SourceTypeSchema", "SCHEMAS"}:
        module = import_module("discover_client.config")
        return getattr(module, name)
    if name == "registered_types":
        return import_module("discover_client.sources").registered_types
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
