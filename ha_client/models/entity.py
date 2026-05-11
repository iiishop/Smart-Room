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


_DOMAIN_MAP: dict[str, EntityDomain] = {d.value: d for d in EntityDomain}


@dataclass
class EntityState:
    entity_id: str
    state: str
    attributes: dict[str, object] = field(default_factory=dict)
    last_changed: datetime | None = None
    last_updated: datetime | None = None

    @property
    def friendly_name(self) -> str:
        return str(self.attributes.get("friendly_name", self.entity_id))

    @property
    def domain(self) -> "EntityDomain":
        return classify_entity(self.entity_id)

    @staticmethod
    def from_dict(data: dict[str, object]) -> "EntityState":
        last_changed_str = data.get("last_changed")
        last_updated_str = data.get("last_updated")

        def _parse_dt(val: object) -> datetime | None:
            if isinstance(val, str) and val:
                try:
                    return datetime.fromisoformat(val.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    return None
            return None

        return EntityState(
            entity_id=str(data.get("entity_id", data.get("id", ""))),
            state=str(data.get("state", "")),
            attributes=dict(data.get("attributes", {}) or {}),
            last_changed=_parse_dt(last_changed_str),
            last_updated=_parse_dt(last_updated_str),
        )


def classify_entity(entity_id: str) -> EntityDomain:
    if "." not in entity_id:
        return EntityDomain.UNKNOWN
    prefix = entity_id.split(".", 1)[0]
    return _DOMAIN_MAP.get(prefix, EntityDomain.UNKNOWN)
