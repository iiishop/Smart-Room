from __future__ import annotations

import requests


class HAAPIError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(message)

    def __str__(self) -> str:
        if self.status_code is not None:
            return f"[HTTP {self.status_code}] {super().__str__()}"
        return super().__str__()


class HARestClient:
    def __init__(self, rest_url: str, token: str):
        self._base_url = rest_url.rstrip("/")
        self._token = token
        self._timeout: float = 10.0

    @property
    def timeout(self) -> float:
        return self._timeout

    @timeout.setter
    def timeout(self, value: float):
        self._timeout = value

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: dict | None = None) -> requests.Response:
        url = f"{self._base_url}{path}"
        try:
            resp = requests.get(
                url, headers=self._headers(), params=params, timeout=self._timeout
            )
            return resp
        except requests.Timeout as e:
            raise HAAPIError(f"Request timed out: {self._base_url}{path}") from e
        except requests.ConnectionError as e:
            raise HAAPIError(f"Connection failed: {self._base_url}") from e
        except requests.RequestException as e:
            raise HAAPIError(f"Request error: {e}") from e

    def _raise_for_status(self, resp: requests.Response):
        if resp.status_code >= 400:
            raise HAAPIError(
                f"HTTP error: {resp.reason}",
                status_code=resp.status_code,
            )

    def get_all_states(self) -> list[dict]:
        resp = self._get("/api/states")
        self._raise_for_status(resp)
        return resp.json()

    def get_entity_state(self, entity_id: str) -> dict:
        resp = self._get(f"/api/states/{entity_id}")
        self._raise_for_status(resp)
        return resp.json()

    def get_history(
        self,
        entity_id: str,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> list[dict]:
        params = {}
        if start_time is not None:
            params["filter_entity_id"] = entity_id
            params["start_time"] = start_time
        else:
            params["filter_entity_id"] = entity_id
        if end_time is not None:
            params["end_time"] = end_time

        resp = self._get("/api/history/period", params=params)
        self._raise_for_status(resp)
        data: list[list[dict]] = resp.json()
        if data:
            return data[0]
        return []

    def render_template(self, template: str) -> str:
        url = f"{self._base_url}/api/template"
        try:
            resp = requests.post(
                url,
                headers=self._headers(),
                json={"template": template},
                timeout=self._timeout,
            )
        except requests.Timeout as e:
            raise HAAPIError(f"Request timed out: /api/template") from e
        except requests.ConnectionError as e:
            raise HAAPIError(f"Connection failed: {self._base_url}") from e
        except requests.RequestException as e:
            raise HAAPIError(f"Request error: {e}") from e

        self._raise_for_status(resp)
        return resp.text

    def health_check(self) -> bool:
        try:
            resp = requests.get(
                f"{self._base_url}/api/",
                headers=self._headers(),
                timeout=self._timeout,
            )
            return resp.status_code == 200
        except Exception:
            return False
