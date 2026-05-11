"""Device models for Home Assistant entities."""

from dataclasses import dataclass, field

from ha_client.models.entity import EntityDomain, EntityState


class Device:
    """Base class for all Home Assistant devices."""

    def __init__(self, entity_state: EntityState):
        self._state = entity_state
        self.supported_features: set[int] = set()

    @property
    def entity_id(self) -> str:
        return self._state.entity_id

    @property
    def name(self) -> str:
        return self._state.friendly_name

    @property
    def domain(self) -> EntityDomain:
        return self._state.domain

    @property
    def state(self) -> str:
        return self._state.state

    @property
    def attributes(self) -> dict:
        return self._state.attributes

    @property
    def is_on(self) -> bool:
        return self.state.lower() == "on"

    @property
    def is_available(self) -> bool:
        return self.state.lower() != "unavailable"

    def update_state(self, entity_state: EntityState) -> None:
        self._state = entity_state

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(entity_id={self.entity_id!r}, state={self.state!r})"


class Switch(Device):
    """Simple on/off switch device."""

    @property
    def is_on(self) -> bool:
        return self.state.lower() == "on"


class Light(Device):
    """Light device with brightness, color temperature, and RGB color support."""

    @property
    def is_on(self) -> bool:
        return self.state.lower() == "on"

    @property
    def brightness(self) -> int | None:
        val = self.attributes.get("brightness")
        if val is not None:
            return int(val)
        return None

    @property
    def brightness_pct(self) -> int | None:
        b = self.brightness
        if b is not None:
            return round(b / 255 * 100)
        return None

    @property
    def color_temp(self) -> int | None:
        val = self.attributes.get("color_temp")
        if val is not None:
            return int(val)
        return None

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        val = self.attributes.get("rgb_color")
        if val and len(val) == 3:
            return (int(val[0]), int(val[1]), int(val[2]))
        return None

    @property
    def hs_color(self) -> tuple[float, float] | None:
        val = self.attributes.get("hs_color")
        if val and len(val) == 2:
            return (float(val[0]), float(val[1]))
        return None

    @property
    def supported_color_modes(self) -> list[str]:
        return self.attributes.get("supported_color_modes", [])

    @property
    def min_mireds(self) -> int | None:
        val = self.attributes.get("min_mireds")
        if val is not None:
            return int(val)
        return None

    @property
    def max_mireds(self) -> int | None:
        val = self.attributes.get("max_mireds")
        if val is not None:
            return int(val)
        return None


class Sensor(Device):
    """Sensor device that provides a numeric or string reading."""

    @property
    def value(self) -> str:
        return self.state

    @property
    def unit(self) -> str | None:
        return self.attributes.get("unit_of_measurement")

    @property
    def device_class(self) -> str | None:
        return self.attributes.get("device_class")


_device_factory_map: dict[EntityDomain, type[Device]] = {
    EntityDomain.LIGHT: Light,
    EntityDomain.SWITCH: Switch,
    EntityDomain.SENSOR: Sensor,
}


def create_device(entity_state: EntityState) -> Device:
    domain = entity_state.domain
    device_cls = _device_factory_map.get(domain, Device)
    return device_cls(entity_state)
