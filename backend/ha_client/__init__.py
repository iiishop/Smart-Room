"""Home Assistant Python Client — modular async client for HA REST/WebSocket APIs."""

from ha_client.config.settings import HAConfig, load_config, create_default_config
from ha_client.models.entity import EntityState, EntityDomain
from ha_client.models.device import Device, Light, Switch, Sensor
from ha_client.api.exceptions import HAError, HAConnectionError, HAAuthError, HAResponseError, HAServiceError
from ha_client.api.rest import HARestClient
from ha_client.api.websocket import HAWebSocketClient
from ha_client.api.connection import ConnectionManager
from ha_client.core.event_bus import EventBus, EventType
from ha_client.core.device_manager import DeviceManager
from ha_client.core.controller import DeviceController

__all__ = [
    "HAConfig", "load_config", "create_default_config",
    "EntityState", "EntityDomain",
    "Device", "Light", "Switch", "Sensor",
    "HAError", "HAConnectionError", "HAAuthError", "HAResponseError", "HAServiceError",
    "HARestClient", "HAWebSocketClient", "ConnectionManager",
    "EventBus", "EventType",
    "DeviceManager", "DeviceController",
]
