from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Awaitable

from ha_client.models.entity import EntityState

logger = logging.getLogger(__name__)

StateListener = Callable[[str, EntityState], Awaitable[None]]


class DeviceManager:
    def __init__(self, ws_client=None, rest_client=None):
        self._ws_client = ws_client
        self._rest_client = rest_client
        self._states: dict[str, EntityState] = {}
        self._lock = asyncio.Lock()
        self._listeners: list[StateListener] = []

    @staticmethod
    def _extract_domain(entity_id: str) -> str:
        if "." in entity_id:
            return entity_id.split(".")[0]
        return entity_id

    @staticmethod
    def _to_entity_state(entity_id: str, raw: dict) -> EntityState:
        return EntityState(
            entity_id=entity_id,
            state=raw.get("state", "unknown"),
            attributes=raw.get("attributes", {}),
        )

    async def _notify_listeners(self, entity_id: str, state: EntityState):
        for listener in self._listeners:
            try:
                await listener(entity_id, state)
            except Exception:
                logger.error("Listener error for %s", entity_id, exc_info=True)

    async def update_state(self, entity_id: str, new_state: dict) -> None:
        if isinstance(new_state, EntityState):
            entity_state = new_state
        else:
            entity_state = self._to_entity_state(entity_id, new_state)

        async with self._lock:
            self._states[entity_id] = entity_state

        await self._notify_listeners(entity_id, entity_state)

    async def update_from_snapshot(self, states: list[dict | EntityState]) -> None:
        new_states: dict[str, EntityState] = {}
        for item in states:
            if isinstance(item, EntityState):
                es = item
            elif isinstance(item, dict):
                es = EntityState.from_ha_json(item)
            else:
                continue
            new_states[es.entity_id] = es

        async with self._lock:
            self._states.clear()
            self._states.update(new_states)

        for entity_id, state in new_states.items():
            await self._notify_listeners(entity_id, state)

    async def remove_entity(self, entity_id: str) -> None:
        async with self._lock:
            self._states.pop(entity_id, None)

    async def get_state(self, entity_id: str) -> EntityState | None:
        async with self._lock:
            return self._states.get(entity_id)

    async def get_all_states(self) -> list[EntityState]:
        async with self._lock:
            return list(self._states.values())

    async def get_by_domain(self, domain: str) -> list[EntityState]:
        prefix = f"{domain}."
        async with self._lock:
            return [
                s
                for eid, s in self._states.items()
                if eid == domain or eid.startswith(prefix)
            ]

    def add_listener(self, callback: StateListener) -> None:
        self._listeners.append(callback)

    def remove_listener(self, callback: StateListener) -> None:
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    async def call_service(
        self,
        entity_id: str,
        service: str,
        service_data: dict | None = None,
    ) -> None:
        domain = self._extract_domain(entity_id)
        if not self._ws_client:
            raise RuntimeError("ws_client is required for call_service")

        await self._ws_client.call_service(
            domain=domain,
            service=service,
            target={"entity_id": entity_id},
            service_data=service_data,
        )

    async def get_services(self) -> dict:
        if self._ws_client and hasattr(self._ws_client, "get_services"):
            services = await self._ws_client.get_services()
            if isinstance(services, dict) and services:
                return services

        if self._rest_client and hasattr(self._rest_client, "get_services"):
            services = await self._rest_client.get_services()
            return services if isinstance(services, dict) else {}

        return {}
