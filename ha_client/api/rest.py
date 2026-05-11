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
    def __init__(self, config: HAConfig):
        self._config = config
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers=self._config.headers,
                verify=self._config.verify_ssl,
                timeout=httpx.Timeout(self._config.request_timeout),
            )
        return self._client

    async def _request(
        self,
        method: str,
        path: str,
        json_data: dict | None = None,
    ) -> dict:
        client = await self._ensure_client()
        url = f"{self._config.url.rstrip('/')}{path}"
        try:
            response = await client.request(method, url, json=json_data)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
            raise HAConnectionError(f"Failed to connect to {url}: {e}") from e
        except httpx.HTTPError as e:
            raise HAConnectionError(f"HTTP error for {url}: {e}") from e

        if response.status_code in (401, 403):
            raise HAAuthError(
                f"Authentication failed ({response.status_code}) for {url}"
            )

        try:
            data = response.json()
        except Exception as e:
            raise HAResponseError(f"Failed to parse JSON response from {url}: {e}") from e

        if response.status_code >= 400:
            raise HAServiceError(
                f"Service error ({response.status_code}) for {url}: {data}"
            )

        return data

    async def get_states(self) -> list[EntityState]:
        data = await self._request("GET", "/api/states")
        entities = []
        for item in data:
            try:
                entities.append(EntityState.from_dict(item))
            except Exception as e:
                logger.warning(f"Failed to parse entity state: {e}")
        return entities

    async def get_state(self, entity_id: str) -> EntityState | None:
        try:
            data = await self._request("GET", f"/api/states/{entity_id}")
            return EntityState.from_dict(data)
        except HAConnectionError:
            raise
        except HAError:
            return None
        except Exception:
            return None

    async def call_service(
        self,
        domain: str,
        service: str,
        entity_id: str | None = None,
        service_data: dict | None = None,
    ) -> bool:
        payload: dict[str, object] = {}
        if entity_id is not None:
            payload["entity_id"] = entity_id
        if service_data:
            payload.update(service_data)

        try:
            await self._request(
                "POST",
                f"/api/services/{domain}/{service}",
                json_data=payload,
            )
            return True
        except (HAConnectionError, HAAuthError):
            raise
        except HAError as e:
            logger.error(f"Service call failed: {e}")
            raise HAServiceError(
                f"Failed to call service {domain}/{service}: {e}"
            ) from e

    async def toggle(self, entity_id: str) -> bool:
        if "." not in entity_id:
            raise HAServiceError(f"Invalid entity_id: {entity_id}")
        domain = entity_id.split(".", 1)[0]
        return await self.call_service(domain, "toggle", entity_id=entity_id)

    async def turn_on(self, entity_id: str, **kwargs) -> bool:
        if "." not in entity_id:
            raise HAServiceError(f"Invalid entity_id: {entity_id}")
        domain = entity_id.split(".", 1)[0]
        return await self.call_service(
            domain, "turn_on", entity_id=entity_id, service_data=kwargs if kwargs else None
        )

    async def turn_off(self, entity_id: str) -> bool:
        if "." not in entity_id:
            raise HAServiceError(f"Invalid entity_id: {entity_id}")
        domain = entity_id.split(".", 1)[0]
        return await self.call_service(domain, "turn_off", entity_id=entity_id)

    async def check_connection(self) -> bool:
        try:
            await self._request("GET", "/api/")
            return True
        except HAError:
            return False
        except Exception:
            return False

    async def close(self):
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None
