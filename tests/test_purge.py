import pytest

from cwm_cdn_api import purge


TENANT_OBJECT = {
    "spec": {
        "domains": [{"name": "example.com"}, {"name": "cdn.example.com"}],
    }
}


def test_normalize_purge_selectors_paths_prefixes_and_urls():
    selectors = purge.normalize_purge_selectors({
        "paths": ["/index.html?x=1"],
        "prefixes": ["/assets/"],
        "urls": ["https://example.com/a/b?c=d"],
    }, TENANT_OBJECT)
    assert selectors == [
        {"type": "path", "path": "/index.html", "query": "x=1"},
        {"type": "prefix", "path": "/assets/", "query": None},
        {"type": "path", "path": "/a/b", "query": "c=d"},
    ]


@pytest.mark.parametrize("body,msg", [
    ({}, "at least one"),
    ({"unknown": []}, "Unsupported"),
    ({"paths": ["https://example.com/a"]}, "scheme or host"),
    ({"prefixes": ["/"]}, "purge-everything"),
    ({"prefixes": ["/assets"]}, "end with"),
    ({"urls": ["https://evil.example/a"]}, "not configured"),
])
def test_normalize_purge_selectors_rejects_invalid_input(body, msg):
    with pytest.raises(ValueError, match=msg):
        purge.normalize_purge_selectors(body, TENANT_OBJECT)


@pytest.mark.asyncio
async def test_purge_local_not_configured(monkeypatch):
    monkeypatch.setattr(purge.config, "CACHE_ADMIN_ENDPOINTS", "")
    result = await purge.purge_local("op", "tenant", everything=True)
    assert result["status"] == "not_configured"
    assert result["success"] is False


@pytest.mark.asyncio
async def test_purge_local_partial_failure(monkeypatch):
    class Response:
        def __init__(self, status_code, text):
            self.status_code = status_code
            self.text = text
            self.headers = {"content-type": "application/json"}

        def json(self):
            return {"success": self.status_code == 200, "selectorsApplied": 1 if self.status_code == 200 else 0}

    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if "cache1" in url:
            return Response(200, '{"success":true}')
        return Response(500, "boom")

    monkeypatch.setattr(purge.config, "CACHE_ADMIN_ENDPOINTS", "cache1=http://cache1,cache2=http://cache2")
    monkeypatch.setattr(purge.config, "CACHE_ADMIN_BEARER_TOKEN", "secret")
    monkeypatch.setattr(purge.requests, "post", fake_post)
    result = await purge.purge_local("op", "tenant", selectors=[{"type": "path", "path": "/x", "query": None}])
    assert result["status"] == "partial"
    assert result["success"] is False
    assert calls[0][1]["headers"] == {"Authorization": "Bearer secret"}


@pytest.mark.asyncio
async def test_purge_local_requires_bearer_token(monkeypatch):
    monkeypatch.setattr(purge.config, "CACHE_ADMIN_ENDPOINTS", "cache1=http://cache1")
    monkeypatch.setattr(purge.config, "CACHE_ADMIN_BEARER_TOKEN", "")
    result = await purge.purge_local("op", "tenant", everything=True)
    assert result["status"] == "not_configured"
    assert "CACHE_ADMIN_BEARER_TOKEN" in result["message"]


@pytest.mark.asyncio
async def test_secondary_propagation_preserves_partial_status(monkeypatch):
    class Response:
        status_code = 207
        text = '{"success":false,"status":"partial"}'
        headers = {"content-type": "application/json"}

        def json(self):
            return {"success": False, "status": "partial"}

    def fake_post(*args, **kwargs):
        return Response()

    monkeypatch.setattr(purge.config, "IS_PRIMARY", True)
    monkeypatch.setattr(purge.config, "ALLOWED_PRIMARY_KEY", "primary")
    monkeypatch.setattr(purge.config, "SECONDARIES_JSON", '[{"name":"sec","url":"http://secondary"}]')
    monkeypatch.setattr(purge.requests, "post", fake_post)
    results = await purge.propagate_to_secondaries("op", "/purge", "tenant", {"paths": ["/x"]})
    assert results == [{
        "popId": "sec",
        "status": "partial",
        "success": False,
        "statusCode": 207,
        "message": '{"success":false,"status":"partial"}',
        "response": {"success": False, "status": "partial"},
        "latencySeconds": results[0]["latencySeconds"],
    }]
