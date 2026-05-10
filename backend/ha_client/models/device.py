from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field

from ha_client.models.entity import EntityDomain


@dataclass
class Device(ABC):
    entity_id: str
    name: str
    domain: EntityDomain = EntityDomain.UNKNOWN
    state: str = "unknown"
    supported_features: set[int] = field(default_factory=set)
    attributes: dict = field(default_factory=dict)

    @property
    def is_on(self) -> bool:
        return self.state == "on"

    @property
    def is_available(self) -> bool:
        return self.state != "unavailable"


@dataclass
class Light(Device):
    brightness: int | None = None
    color_temp: int | None = None
    rgb_color: tuple[int, int, int] | None = None
    hs_color: tuple[float, float] | None = None
    min_mireds: int = 153
    max_mireds: int = 500

    def __post_init__(self):
        self.domain = EntityDomain.LIGHT

    @property
    def brightness_pct(self) -> int:
        if self.brightness is None:
            return 0
        return round(self.brightness / 2.55)


@dataclass
class Switch(Device):
    def __post_init__(self):
        self.domain = EntityDomain.SWITCH


@dataclass
class Sensor(Device):
    unit_of_measurement: str | None = None
    device_class: str | None = None

    def __post_init__(self):
        self.domain = EntityDomain.SENSOR

    @property
    def numeric_value(self) -> float | None:
        try:
            return float(self.state)
        except (ValueError, TypeError):
            return None
