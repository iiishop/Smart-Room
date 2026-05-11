from dataclasses import dataclass, field

from ha_client.models.entity import EntityDomain, EntityState, classify_entity


@dataclass
class Device:
    entity_id: str
    name: str
    domain: EntityDomain
    state: str
    supported_features: set[int] = field(default_factory=set)
    attributes: dict[str, object] = field(default_factory=dict)

    @property
    def is_on(self) -> bool:
        return self.state.lower() == "on"

    @property
    def is_available(self) -> bool:
        return self.state.lower() != "unavailable"


@dataclass
class Light(Device):
    brightness: int | None = None
    color_temp: int | None = None
    rgb_color: tuple[int, int, int] | None = None
    hs_color: tuple[float, float] | None = None

    def __post_init__(self):
        self.domain = EntityDomain.LIGHT


@dataclass
class Switch(Device):
    def __post_init__(self):
        self.domain = EntityDomain.SWITCH


@dataclass
class Sensor(Device):
    unit: str = ""
    device_class: str = ""

    def __post_init__(self):
        self.domain = EntityDomain.SENSOR


_DEVICE_FACTORY: dict[EntityDomain, type[Device]] = {
    EntityDomain.LIGHT: Light,
    EntityDomain.SWITCH: Switch,
    EntityDomain.SENSOR: Sensor,
}


def create_device(entity_state: EntityState) -> Device:
    domain = classify_entity(entity_state.entity_id)
    device_cls = _DEVICE_FACTORY.get(domain, Device)

    attrs = entity_state.attributes

    if device_cls is Light:
        brightness = attrs.get("brightness")
        color_temp = attrs.get("color_temp")
        rgb_color = attrs.get("rgb_color")
        hs_color = attrs.get("hs_color")
        return Light(
            entity_id=entity_state.entity_id,
            name=entity_state.friendly_name,
            state=entity_state.state,
            supported_features=set(attrs.get("supported_features", []) or []),
            attributes=attrs,
            brightness=int(brightness) if brightness is not None else None,
            color_temp=int(color_temp) if color_temp is not None else None,
            rgb_color=tuple(rgb_color) if rgb_color else None,
            hs_color=tuple(hs_color) if hs_color else None,
        )
    elif device_cls is Switch:
        return Switch(
            entity_id=entity_state.entity_id,
            name=entity_state.friendly_name,
            state=entity_state.state,
            supported_features=set(attrs.get("supported_features", []) or []),
            attributes=attrs,
        )
    elif device_cls is Sensor:
        return Sensor(
            entity_id=entity_state.entity_id,
            name=entity_state.friendly_name,
            state=entity_state.state,
            supported_features=set(attrs.get("supported_features", []) or []),
            attributes=attrs,
            unit=str(attrs.get("unit_of_measurement", "")),
            device_class=str(attrs.get("device_class", "")),
        )
    else:
        return Device(
            entity_id=entity_state.entity_id,
            name=entity_state.friendly_name,
            domain=domain,
            state=entity_state.state,
            supported_features=set(attrs.get("supported_features", []) or []),
            attributes=attrs,
        )
