"""Home Assistant REST API asynchronous client."""

import logging

import httpx

from ha_client.api.exceptions import (
    HAConnectionError,
    HAAuthError,
    HAResponseError,
    HAServiceError,
)
from ha_client.config.settings import HAConfig
from ha_client.models.entity import EntityState

logger = logging.getLogger(__name__)


class HARestClient:
    """Async HTTP client for Home Assistant REST API."""

    def __init__(self, config: HAConfig):
        self._config = config
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._config.base_url,
                headers=self._config.headers,
                timeout=httpx.Timeout(self._config.request_timeout),
                verify=self._config.verify_ssl,
            )
        return self._client

    async def check_connection(self) -> bool:
        try:
            client = await self._ensure_client()
            response = await client.get("/api/")
            if response.status_code == 401:
                raise HAAuthError("Invalid token")
            return response.status_code < 500
        except httpx.RequestError as e:
            logger.warning("Connection check failed: %s", e)
            return False
        except HAAuthError:
            raise

    async def get_states(self) -> list[EntityState]:
        try:
            client = await self._ensure_client()
            response = await client.get("/api/states")
            if response.status_code == 401:
                raise HAAuthError("Invalid token")
            if response.status_code >= 400:
                raise HAResponseError(f"HTTP {response.status_code}: {response.text[:200]}")
            data = response.json()
            if not isinstance(data, list):
                raise HAResponseError("Expected a list of states")
            return [EntityState.from_ha_response(item) for item in data]
        except httpx.RequestError as e:
            raise HAConnectionError(f"Failed to fetch states: {e}") from e
        except (ValueError, KeyError) as e:
            raise HAResponseError(f"Failed to parse states response: {e}") from e

    async def get_state(self, entity_id: str) -> EntityState | None:
        try:
            client = await self._ensure_client()
            response = await client.get(f"/api/states/{entity_id}")
            if response.status_code == 404:
                return None
            if response.status_code == 401:
                raise HAAuthError("Invalid token")
            if response.status_code >= 400:
                raise HAResponseError(f"HTTP {response.status_code}: {response.text[:200]}")
            data = response.json()
            return EntityState.from_ha_response(data)
        except httpx.RequestError as e:
            raise HAConnectionError(f"Failed to fetch state for {entity_id}: {e}") from e
        except (ValueError, KeyError) as e:
            raise HAResponseError(f"Failed to parse state response: {e}") from e

    async def call_service(
        self,
        domain: str,
        service: str,
        entity_id: str | None = None,
        service_data: dict | None = None,
    ) -> bool:
        payload: dict = {}
        if entity_id:
            payload["entity_id"] = entity_id
        if service_data:
            payload.update(service_data)

        try:
            client = await self._ensure_client()
            response = await client.post(
                f"/api/services/{domain}/{service}",
                json=payload,
            )
            if response.status_code == 401:
                raise HAAuthError("Invalid token")
            if response.status_code >= 400:
                raise HAServiceError(
                    f"Service call {domain}/{service} failed: HTTP {response.status_code}: {response.text[:200]}"
                )
            return True
        except httpx.RequestError as e:
            raise HAConnectionError(f"Service call failed: {e}") from e

    async def turn_on(self, entity_id: str, **kwargs) -> bool:
        domain = entity_id.split(".", 1)[0]
        return await self.call_service(domain, "turn_on", entity_id, kwargs if kwargs else None)

    async def turn_off(self, entity_id: str) -> bool:
        domain = entity_id.split(".", 1)[0]
        return await self.call_service(domain, "turn_off", entity_id)

    async def toggle(self, entity_id: str) -> bool:
        domain = entity_id.split(".", 1)[0]
        return await self.call_service(domain, "toggle", entity_id)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
