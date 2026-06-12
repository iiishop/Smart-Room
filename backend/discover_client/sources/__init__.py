"""Source registry - maps source_type strings to Source subclasses."""

from typing import Type

from discover_client.source import Source

_registry: dict[str, Type[Source]] = {}


def register(source_type: str, cls: Type[Source]) -> None:
    """Register a Source subclass for a given source_type string."""
    if source_type in _registry:
        raise ValueError(f"Source type '{source_type}' is already registered")
    _registry[source_type] = cls


def get(source_type: str) -> Type[Source]:
    """Look up a Source subclass by source_type string."""
    if source_type not in _registry:
        raise KeyError(f"Unknown source type: '{source_type}'")
    return _registry[source_type]


def registered_types() -> list[str]:
    """Return all registered source_type keys."""
    return list(_registry.keys())


from discover_client.sources.mqtt_source import MqttSource
from discover_client.sources.mdns_source import MdnsSource
from discover_client.sources.ssdp_source import SsdpSource

register("mqtt", MqttSource)
register("mdns", MdnsSource)
register("ssdp", SsdpSource)
