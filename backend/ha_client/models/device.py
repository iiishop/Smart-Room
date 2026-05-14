from __future__ import annotations

from dataclasses import dataclass, field

from ha_client.models.entity import EntityDomain, EntityState


@dataclass
class Device:
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

    def update_state(self, entity_state: EntityState) -> None:
        self.state = entity_state.state
        self.attributes = dict(entity_state.attributes)


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


def create_device(entity_state: EntityState) -> Device:
    domain = entity_state.domain
    attrs = entity_state.attributes
    features_raw = attrs.get("supported_features", 0)
    if isinstance(features_raw, int):
        features = {features_raw} if features_raw else set()
    elif isinstance(features_raw, (list, tuple, set)):
        features = set(features_raw)
    else:
        features = set()

    if domain == EntityDomain.LIGHT:
        return Light(
            entity_id=entity_state.entity_id,
            name=entity_state.friendly_name,
            state=entity_state.state,
            attributes=attrs,
            brightness=attrs.get("brightness"),
            color_temp=attrs.get("color_temp"),
            rgb_color=tuple(attrs["rgb_color"]) if attrs.get("rgb_color") and len(attrs["rgb_color"]) == 3 else None,
            hs_color=tuple(attrs["hs_color"]) if attrs.get("hs_color") and len(attrs["hs_color"]) == 2 else None,
            min_mireds=attrs.get("min_mireds", 153),
            max_mireds=attrs.get("max_mireds", 500),
            supported_features=features,
        )
    elif domain == EntityDomain.SWITCH:
        return Switch(
            entity_id=entity_state.entity_id,
            name=entity_state.friendly_name,
            state=entity_state.state,
            attributes=attrs,
            supported_features=features,
        )
    elif domain == EntityDomain.SENSOR:
        return Sensor(
            entity_id=entity_state.entity_id,
            name=entity_state.friendly_name,
            state=entity_state.state,
            attributes=attrs,
            unit_of_measurement=attrs.get("unit_of_measurement"),
            device_class=attrs.get("device_class"),
        )
    else:
        return Device(
            entity_id=entity_state.entity_id,
            name=entity_state.friendly_name,
            domain=domain,
            state=entity_state.state,
            attributes=attrs,
            supported_features=features,
        )
