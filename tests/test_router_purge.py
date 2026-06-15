import orjson
import pytest

from cwm_cdn_api import router


@pytest.mark.asyncio
async def test_purge_endpoint_calls_runtime_without_feature_gate(monkeypatch):
    async def fake_get_tenant_object(name):
        return True, {"spec": {"domains": [{"name": "example.com"}]}}

    async def fake_purge_local(operation_id, tenant_name, selectors=None, everything=False):
        return {"popId": "local", "status": "success", "success": True}

    async def fake_secondaries(*args, **kwargs):
        return []

    monkeypatch.setattr(router.config, "IS_PRIMARY", True)
    monkeypatch.setattr(router.api, "get_tenant_object", fake_get_tenant_object)
    monkeypatch.setattr(router.purge_api, "purge_local", fake_purge_local)
    monkeypatch.setattr(router.purge_api, "propagate_to_secondaries", fake_secondaries)
    response = await router._purge_response("tenant", {}, "", everything=True)
    body = orjson.loads(response.body)
    assert response.status_code == 200
    assert body["success"] is True
    assert body["accepted"] is True
    assert body["results"] == [{
        "pop": "local",
        "popId": "local",
        "status": "success",
        "purgedSelectors": 0,
        "message": "",
        "success": True,
    }]


@pytest.mark.asyncio
async def test_secondary_purge_requires_non_empty_primary_key(monkeypatch):
    monkeypatch.setattr(router.config, "IS_PRIMARY", False)
    monkeypatch.setattr(router.config, "ALLOWED_PRIMARY_KEY", "")
    response = await router._purge_response("tenant", {}, "", everything=True)
    assert response.status_code == 403
    assert orjson.loads(response.body)["success"] is False
