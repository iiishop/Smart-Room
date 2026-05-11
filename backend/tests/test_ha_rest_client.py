from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
import requests

from ha.ha_rest_client import HAAPIError, HARestClient
from ha.models import DeviceState


@pytest.fixture
def client():
    return HARestClient("http://localhost:8123/api", "test_token")


def _mock_response(status_code=200, json_data=None, text=""):
    mock_resp = Mock(spec=requests.Response)
    mock_resp.status_code = status_code
    mock_resp.reason = "OK"
    mock_resp.json.return_value = json_data if json_data is not None else {}
    mock_resp.text = text
    return mock_resp


class TestDeviceState:
    def test_from_api_complete(self):
        data = {
            "entity_id": "light.test",
            "state": "on",
            "attributes": {"friendly_name": "Test Light"},
            "last_updated": "2024-01-01T00:00:00Z",
            "last_changed": "2024-01-01T00:00:00Z",
        }
        ds = DeviceState.from_api(data)
        assert ds.entity_id == "light.test"
        assert ds.state == "on"
        assert ds.attributes == {"friendly_name": "Test Light"}

    def test_from_api_unknown(self):
        ds = DeviceState.from_api({})
        assert ds.entity_id == ""
        assert ds.state == "unknown"
        assert ds.attributes == {}


class TestHARestClient:

    def test_init_defaults(self, client):
        assert client._base_url == "http://localhost:8123/api"
        assert client._token == "test_token"
        assert client.timeout == 10.0

    def test_timeout_setter(self, client):
        client.timeout = 5.0
        assert client.timeout == 5.0

    def test_get_all_states(self, client):
        mock_resp = _mock_response(
            json_data=[
                {"entity_id": "light.test", "state": "on", "attributes": {}}
            ]
        )
        with patch("requests.request", return_value=mock_resp):
            result = client.get_all_states()
            assert len(result) == 1
            assert isinstance(result[0], DeviceState)
            assert result[0].entity_id == "light.test"
            assert result[0].state == "on"

    def test_get_entity_state(self, client):
        mock_resp = _mock_response(
            json_data={"entity_id": "light.test", "state": "on", "attributes": {}}
        )
        with patch("requests.request", return_value=mock_resp):
            result = client.get_entity_state("light.test")
            assert isinstance(result, DeviceState)
            assert result.entity_id == "light.test"
            assert result.state == "on"

    def test_get_history(self, client):
        mock_resp = _mock_response(
            json_data=[
                [
                    {
                        "entity_id": "light.test",
                        "state": "on",
                        "last_updated": "2024-01-01T00:00:00Z",
                    }
                ]
            ]
        )
        with patch("requests.request", return_value=mock_resp):
            result = client.get_history(
                "light.test", "2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z"
            )
            assert len(result) == 1
            assert result[0]["entity_id"] == "light.test"

    def test_render_template(self, client):
        mock_resp = _mock_response(text="rendered result")
        with patch("requests.request", return_value=mock_resp):
            result = client.render_template("{{ states }}")
            assert result == "rendered result"

    def test_health_check_success(self, client):
        mock_resp = _mock_response(status_code=200)
        with patch("requests.get", return_value=mock_resp):
            assert client.health_check() is True

    def test_health_check_failure(self, client):
        mock_resp = _mock_response(status_code=500)
        with patch("requests.get", return_value=mock_resp):
            assert client.health_check() is False

    def test_health_check_exception(self, client):
        with patch("requests.get", side_effect=Exception("boom")):
            assert client.health_check() is False

    def test_connection_error_raises(self, client):
        with patch(
            "requests.request",
            side_effect=requests.exceptions.ConnectionError("refused"),
        ):
            with pytest.raises(HAAPIError) as exc:
                client.get_all_states()
            assert "Connection failed" in str(exc.value)

    def test_timeout_raises(self, client):
        with patch(
            "requests.request",
            side_effect=requests.exceptions.Timeout("timed out"),
        ):
            with pytest.raises(HAAPIError) as exc:
                client.get_all_states()
            assert "timed out" in str(exc.value)

    def test_http_error_raises_with_status(self, client):
        mock_resp = _mock_response(status_code=500, json_data=[])
        mock_resp.reason = "Internal Server Error"
        with patch("requests.request", return_value=mock_resp):
            with pytest.raises(HAAPIError) as exc:
                client.get_all_states()
            assert exc.value.status_code == 500
            assert "HTTP error" in str(exc.value)

    def test_haapierror_str_with_status(self):
        e = HAAPIError("test message", 404)
        assert str(e) == "[HTTP 404] test message"

    def test_haapierror_str_without_status(self):
        e = HAAPIError("test message")
        assert str(e) == "test message"
