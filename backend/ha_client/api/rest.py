from __future__ import annotations

from typing import Any

import httpx

from ha_client.api.exceptions import (
    HAConnectionError,
    HAAuthError,
    HAResponseError,
    HAServiceError,
)
from ha_client.config.settings import HAConfig
from ha_client.models.entity import EntityState


class HARestClient:
    def __init__(self, config: HAConfig):
        self._config = config
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._config.url.rstrip("/"),
                headers={
                    "Authorization": f"Bearer {self._config.token}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(self._config.request_timeout),
                verify=self._config.verify_ssl,
            )
        return self._client

    async def get_states(self) -> list[EntityState]:
        try:
            client = self._get_client()
            resp = await client.get("/api/states")
            if resp.status_code == 401 or resp.status_code == 403:
                raise HAAuthError(f"Authentication failed: {resp.status_code}")
            if resp.status_code != 200:
                raise HAResponseError(
                    f"Failed to get states: {resp.status_code} {resp.text}"
                )
            data = resp.json()
            return [EntityState.from_ha_json(item) for item in data]
        except httpx.TimeoutException as e:
            raise HAConnectionError(f"Request timed out: {e}") from e
        except httpx.NetworkError as e:
            raise HAConnectionError(f"Network error: {e}") from e
        except (HAConnectionError, HAAuthError, HAResponseError):
            raise
        except Exception as e:
            raise HAConnectionError(f"Unexpected error: {e}") from e

    async def get_state(self, entity_id: str) -> EntityState | None:
        try:
            client = self._get_client()
            resp = await client.get(f"/api/states/{entity_id}")
            if resp.status_code == 404:
                return None
            if resp.status_code == 401 or resp.status_code == 403:
                raise HAAuthError(f"Authentication failed: {resp.status_code}")
            if resp.status_code != 200:
                raise HAResponseError(
                    f"Failed to get state for {entity_id}: {resp.status_code}"
                )
            data = resp.json()
            return EntityState.from_ha_json(data)
        except httpx.TimeoutException as e:
            raise HAConnectionError(f"Request timed out: {e}") from e
        except httpx.NetworkError as e:
            raise HAConnectionError(f"Network error: {e}") from e
        except (HAConnectionError, HAAuthError, HAResponseError):
            raise
        except Exception as e:
            raise HAConnectionError(f"Unexpected error: {e}") from e

    async def call_service(
        self,
        domain: str,
        service: str,
        entity_id: str | None = None,
        service_data: dict[str, Any] | None = None,
    ) -> bool:
        payload: dict[str, Any] = {}
        if entity_id:
            payload["entity_id"] = entity_id
        if service_data:
            payload.update(service_data)

        try:
            client = self._get_client()
            resp = await client.post(
                f"/api/services/{domain}/{service}", json=payload
            )
            if resp.status_code == 401 or resp.status_code == 403:
                raise HAAuthError(f"Authentication failed: {resp.status_code}")
            if resp.status_code == 404:
                raise HAServiceError(
                    f"Service not found: {domain}/{service}"
                )
            if resp.status_code not in (200, 201):
                raise HAServiceError(
                    f"Service call failed: {resp.status_code} {resp.text}"
                )
            return True
        except httpx.TimeoutException as e:
            raise HAConnectionError(f"Request timed out: {e}") from e
        except httpx.NetworkError as e:
            raise HAConnectionError(f"Network error: {e}") from e
        except (HAConnectionError, HAAuthError, HAServiceError):
            raise
        except Exception as e:
            raise HAConnectionError(f"Unexpected error: {e}") from e

    async def toggle(self, entity_id: str) -> bool:
        domain = entity_id.split(".")[0]
        return await self.call_service(domain, "toggle", entity_id=entity_id)

    async def turn_on(self, entity_id: str, **kwargs: Any) -> bool:
        domain = entity_id.split(".")[0]
        return await self.call_service(
            domain, "turn_on", entity_id=entity_id, service_data=kwargs
        )

    async def turn_off(self, entity_id: str) -> bool:
        domain = entity_id.split(".")[0]
        return await self.call_service(domain, "turn_off", entity_id=entity_id)

    async def check_connection(self) -> bool:
        try:
            client = self._get_client()
            resp = await client.get("/api/")
            return resp.status_code == 200
        except Exception:
            return False

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None
