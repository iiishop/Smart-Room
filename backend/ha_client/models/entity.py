from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class EntityDomain(Enum):
    LIGHT = "light"
    SWITCH = "switch"
    SENSOR = "sensor"
    BINARY_SENSOR = "binary_sensor"
    CLIMATE = "climate"
    COVER = "cover"
    MEDIA_PLAYER = "media_player"
    FAN = "fan"
    LOCK = "lock"
    SCENE = "scene"
    AUTOMATION = "automation"
    SCRIPT = "script"
    UNKNOWN = "unknown"

    @classmethod
    def classify(cls, entity_id: str) -> EntityDomain:
        if "." not in entity_id:
            return cls.UNKNOWN
        prefix = entity_id.split(".")[0]
        try:
            return cls(prefix)
        except ValueError:
            return cls.UNKNOWN


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
    def from_ha_json(cls, data: dict) -> EntityState:
        last_changed = None
        last_updated = None
        if "last_changed" in data and data["last_changed"]:
            last_changed = datetime.fromisoformat(data["last_changed"])
        if "last_updated" in data and data["last_updated"]:
            last_updated = datetime.fromisoformat(data["last_updated"])

        return cls(
            entity_id=data.get("entity_id", ""),
            state=data.get("state", "unknown"),
            attributes=data.get("attributes", {}),
            last_changed=last_changed,
            last_updated=last_updated,
        )
