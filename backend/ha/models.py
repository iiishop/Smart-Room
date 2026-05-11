from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DeviceState:
    entity_id: str
    state: str
    attributes: dict = field(default_factory=dict)
    last_updated: str = ""
    last_changed: str = ""

    @classmethod
    def from_api(cls, data: dict) -> DeviceState:
        return cls(
            entity_id=data.get("entity_id", ""),
            state=data.get("state", "unknown"),
            attributes=data.get("attributes", {}),
            last_updated=data.get("last_updated", ""),
            last_changed=data.get("last_changed", ""),
        )


@dataclass
class ServiceCall:
    domain: str
    service: str
    target_entity: str | None = None
    service_data: dict | None = None
