import pytest

from cwm_cdn_api import api


def pod(name, image="repo/image:tag", ready=True):
    return {
        "metadata": {"name": name, "creationTimestamp": "2026-06-14T00:00:00Z"},
        "spec": {"containers": [{"image": image}]},
        "status": {
            "phase": "Running",
            "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
        },
    }


@pytest.mark.asyncio
async def test_components_status_includes_pop_health(monkeypatch):
    async def fake_get_pods(namespace):
        return {
            "cdn-cache": [pod("router-abc"), pod("cache1-abc"), pod("cache2-abc")],
            "cdn-edge": [pod("cdn-edge-nginx-abc"), pod("cdn-edge-coredns-abc"), pod("cdn-edge-zonewriter-abc")],
            "cwm-cdn-operator-system": [pod("operator-abc")],
        }[namespace]

    monkeypatch.setattr(api, "_get_pods", fake_get_pods)
    monkeypatch.setattr(api, "POP_ID", "pop-test")
    result = await api.components_status()
    assert result["popHealth"]["schemaVersion"] == "cdn_pop_health_v1"
    assert result["popHealth"]["popId"] == "pop-test"
    assert result["popHealth"]["status"] == "healthy"
    assert {check["name"] for check in result["popHealth"]["checks"]} >= {"edge-nginx-ready", "cache-router-ready", "cdn-api-ready"}


def test_secondary_sync_sanitization_redacts_credentials_and_normalizes_latency():
    assert api._sanitize_secondary_sync([
        {
            "name": "sec",
            "url": "https://user:pass@example.com/path",
            "status": "synced",
            "desiredHash": "abc",
            "syncedHash": "abc",
            "latencySeconds": "1.250000",
            "primaryKey": "secret",
        }
    ]) == [
        {
            "name": "sec",
            "status": "synced",
            "desiredHash": "abc",
            "syncedHash": "abc",
            "latencySeconds": 1.25,
            "target": "https://example.com",
        }
    ]
