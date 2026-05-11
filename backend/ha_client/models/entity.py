"""Entity data models for Home Assistant."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto


class EntityDomain(Enum):
    LIGHT = auto()
    SWITCH = auto()
    SENSOR = auto()
    BINARY_SENSOR = auto()
    CLIMATE = auto()
    COVER = auto()
    MEDIA_PLAYER = auto()
    FAN = auto()
    LOCK = auto()
    SCENE = auto()
    AUTOMATION = auto()
    SCRIPT = auto()
    UNKNOWN = auto()

    @classmethod
    def classify(cls, entity_id: str) -> "EntityDomain":
        if "." not in entity_id:
            return cls.UNKNOWN
        prefix = entity_id.split(".", 1)[0]
        mapping = {
            "light": cls.LIGHT,
            "switch": cls.SWITCH,
            "sensor": cls.SENSOR,
            "binary_sensor": cls.BINARY_SENSOR,
            "climate": cls.CLIMATE,
            "cover": cls.COVER,
            "media_player": cls.MEDIA_PLAYER,
            "fan": cls.FAN,
            "lock": cls.LOCK,
            "scene": cls.SCENE,
            "automation": cls.AUTOMATION,
            "script": cls.SCRIPT,
        }
        return mapping.get(prefix, cls.UNKNOWN)


@dataclass
class EntityState:
    entity_id: str
    state: str
    attributes: dict = field(default_factory=dict)
    last_changed: datetime | None = None
    last_updated: datetime | None = None

    @property
    def friendly_name(self) -> str:
        return self.attributes.get("friendly_name", self.entity_id)

    @property
    def domain(self) -> EntityDomain:
        return EntityDomain.classify(self.entity_id)

    @classmethod
    def from_ha_response(cls, data: dict) -> "EntityState":
        def _parse_ts(value: str | None) -> datetime | None:
            if not value:
                return None
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return None

        return cls(
            entity_id=data.get("entity_id", ""),
            state=data.get("state", ""),
            attributes=data.get("attributes", {}),
            last_changed=_parse_ts(data.get("last_changed")),
            last_updated=_parse_ts(data.get("last_updated")),
        )
