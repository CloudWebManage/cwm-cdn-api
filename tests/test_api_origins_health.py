import orjson
import pytest
from fastapi.testclient import TestClient

from cwm_cdn_api import api
from cwm_cdn_api.app import app


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


def test_validate_origins_rejects_invalid_scheme():
    with pytest.raises(ValueError, match="Invalid origin URL"):
        api.validate_origins([{"url": "ftp://origin.example.com"}])


def test_validate_origins_rejects_path_prefixed_multi_origin():
    with pytest.raises(ValueError, match="Path-prefixed origin URLs are not supported with multiple origins"):
        api.validate_origins([
            {"url": "https://origin-a.example.com/path"},
            {"url": "https://origin-b.example.com"},
        ])


def test_check_origin_health_defaults_and_schema(monkeypatch):
    requested = {}

    def fake_get(url, timeout, allow_redirects):
        requested.update({
            "url": url,
            "timeout": timeout,
            "allow_redirects": allow_redirects,
        })
        return FakeResponse(200)

    monkeypatch.setattr(api.requests, "get", fake_get)
    result = api.check_origin_health({"url": "https://origin.example.com"}, 0)

    assert requested == {
        "url": "https://origin.example.com/",
        "timeout": 2,
        "allow_redirects": False,
    }
    assert set(result) == {"name", "url", "healthy", "statusCode", "latencyMs", "checkedAt", "message"}
    assert result["name"] == "origin-0"
    assert result["healthy"] is True
    assert result["statusCode"] == 200
    assert result["message"] == ""


def test_check_origin_health_reports_unexpected_status(monkeypatch):
    monkeypatch.setattr(api.requests, "get", lambda *args, **kwargs: FakeResponse(503))
    result = api.check_origin_health({
        "name": "origin-a",
        "url": "http://origin.example.com",
        "healthCheck": {"path": "/healthz", "expectedStatus": 204},
    }, 0)
    assert result["name"] == "origin-a"
    assert result["healthy"] is False
    assert result["statusCode"] == 503
    assert result["message"] == "unexpected status 503"


def test_check_origin_health_disabled():
    result = api.check_origin_health({
        "name": "origin-a",
        "url": "http://origin.example.com",
        "healthCheck": {"enabled": False},
    }, 0)
    assert result["healthy"] is True
    assert result["statusCode"] is None
    assert result["message"] == "health check disabled"


async def test_origins_health_loads_tenant_and_checks_origins(monkeypatch):
    tenant = {
        "spec": {
            "origins": [
                {"name": "origin-a", "url": "http://origin-a.example.com", "healthCheck": {"enabled": False}},
                {"name": "origin-b", "url": "http://origin-b.example.com", "healthCheck": {"enabled": False}},
            ]
        }
    }

    async def fake_status_output(*args, **kwargs):
        return 0, orjson.dumps(tenant)

    monkeypatch.setattr(api, "async_subprocess_status_output", fake_status_output)
    success, origins = await api.origins_health("tenant1")

    assert success is True
    assert [origin["name"] for origin in origins] == ["origin-a", "origin-b"]
    assert all(origin["healthy"] for origin in origins)


async def test_apply_rejects_invalid_origin_without_kubectl(monkeypatch):
    async def fake_validate_name(name):
        return None

    async def fail_kubectl(*args, **kwargs):
        raise AssertionError("kubectl should not be called")

    monkeypatch.setattr(api, "validate_name", fake_validate_name)
    monkeypatch.setattr(api, "async_subprocess_status_output", fail_kubectl)
    success, message = await api.apply("tenant1", {
        "domains": [],
        "origins": [{"url": "not-a-url"}],
    })

    assert success is False
    assert "Invalid origin URL" in message


def test_origins_health_endpoint_schema(monkeypatch):
    async def fake_origins_health(name):
        return True, [{
            "name": "origin-a",
            "url": "https://origin.example.com",
            "healthy": True,
            "statusCode": 200,
            "latencyMs": 34,
            "checkedAt": "2026-06-08T00:00:00Z",
            "message": "",
        }]

    monkeypatch.setattr(api, "origins_health", fake_origins_health)
    response = TestClient(app()).get("/origins-health", params={"cdn_tenant_name": "tenant1"})

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "tenant": "tenant1",
        "origins": [{
            "name": "origin-a",
            "url": "https://origin.example.com",
            "healthy": True,
            "statusCode": 200,
            "latencyMs": 34,
            "checkedAt": "2026-06-08T00:00:00Z",
            "message": "",
        }],
        "msg": None,
    }
