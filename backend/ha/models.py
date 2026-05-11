from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class DeviceState:
    entity_id: str
    state: str
    attributes: dict
    last_updated: str
    last_changed: str

    @classmethod
    def from_api(cls, data: dict) -> "DeviceState":
        return cls(
            entity_id=data["entity_id"],
            state=data["state"],
            attributes=data.get("attributes", {}),
            last_updated=data["last_updated"],
            last_changed=data["last_changed"],
        )


@dataclass
class ServiceCall:
    domain: str
    service: str
    target_entity: Optional[str] = None
    service_data: Optional[dict] = None
