import pytest

from cwm_cdn_api.validation import validate_tenant_spec


def test_validate_cache_config_accepts_safe_fields():
    validate_tenant_spec({
        "domains": [],
        "origins": [],
        "cache": {
            "enabled": True,
            "mode": "cache_everything",
            "edgeTtl": "1h",
            "respectOriginCacheControl": True,
            "statusHeader": "X-CWM-Cache-Status",
        },
    })


@pytest.mark.parametrize("spec,msg", [
    ({"cache": {"statusHeader": "Set-Cookie"}}, "sensitive"),
    ({"cache": {"edgeTtl": "60"}}, "duration"),
    ({"cache": {"edgeTtl": "5m"}}, "dynamic TTL"),
    ({"cache": {"respectOriginCacheControl": False}}, "origin-header override"),
    ({"security": {"nginxConfig": "return 200;"}}, "Raw/generated config"),
    ({"security": {"rawNginx": "return 200;"}}, "Raw/generated config"),
    ({"redirects": [{"name": "r", "when": {"path": {"type": "regex", "value": "/x"}}, "to": "/y"}]}, "glob"),
    ({"security": {"urls": {"block": [{"name": "x", "match": {"type": "glob", "path": "/x"}}] * 101}}}, "at most 100"),
    ({"security": {"urls": {"block": [{"name": "x", "match": {"type": "glob", "path": "/x"}}, {"name": "x", "match": {"type": "glob", "path": "/y"}}]}}}, "unique"),
    ({"security": {"rateLimit": {"enabled": True, "requests": 1, "period": "1m", "key": "path", "action": "block"}}}, "key must be clientIp"),
    ({"captcha": {"rules": [{"name": "x", "match": {"type": "glob", "path": "/x"}}]}}, "captcha.enabled"),
    ({"redirects": [{"name": "r", "when": {"path": {"type": "glob", "value": "/x"}, "upstreamStatus": [404, 404]}, "to": "/y"}]}, "duplicate HTTP status"),
    ({"redirects": [{"name": "r", "when": {"path": {"type": "glob", "value": "/x"}}, "to": "/x"}]}, "self-redirect"),
    ({"redirects": [{"name": "r", "when": {"path": {"type": "glob", "value": "/x"}}, "to": "/y; return 200"}]}, "safe"),
    ({"security": {"urls": {"block": [{"name": "x", "match": {"type": "glob", "path": "/x; return 200"}}]}}}, "unsafe characters"),
    ({"redirects": [{"name": "r", "when": {"path": {"type": "glob", "value": "/x { return 200"}}, "to": "/y"}]}, "unsafe characters"),
    ({"redirects": [{"name": "r", "when": {}, "to": "/y"}]}, "at least one matcher"),
    ({"redirects": [{"name": "r", "when": {"path": {"type": "glob", "value": "/x"}}, "to": "/y", "status": 303}]}, "301, 302, 307, 308"),
])
def test_validate_tenant_policy_rejects_unsafe_or_gated_fields(spec, msg):
    with pytest.raises(ValueError, match=msg):
        validate_tenant_spec(spec)


def test_validate_security_captcha_redirect_policy(monkeypatch):
    monkeypatch.setenv("CWM_CDN_TRUSTED_CLIENT_IP_ENABLED", "true")
    validate_tenant_spec({
        "security": {
            "ipAccess": {"allowCidrs": ["10.0.0.0/8"], "blockCidrs": ["192.0.2.0/24"]},
            "methods": {"allow": ["GET", "HEAD"], "block": ["DELETE"]},
            "urls": {"block": [{"name": "admin", "match": {"type": "glob", "path": "/admin/*"}}]},
            "rateLimit": {"enabled": True, "requests": 10, "period": "1m", "burst": 5, "key": "clientIp", "action": "captcha"},
            "request": {"maxBodySize": "1m"},
        },
        "captcha": {
            "enabled": True,
            "provider": "turnstile",
            "siteKey": "site",
            "secretRef": {"name": "turnstile", "key": "secret"},
            "cookieTtl": "10m",
            "rules": [{"name": "protected", "match": {"type": "glob", "path": "/protected/*"}}],
        },
        "redirects": [{
            "name": "old",
            "when": {"path": {"type": "glob", "value": "/old/*"}, "originStatus": [404]},
            "to": "/new/",
            "status": 302,
            "preserveQuery": True,
        }],
    })


def test_validate_rate_limit_captcha_requires_captcha_enabled(monkeypatch):
    monkeypatch.setenv("CWM_CDN_TRUSTED_CLIENT_IP_ENABLED", "true")
    with pytest.raises(ValueError, match="captcha.enabled"):
        validate_tenant_spec({
            "security": {"rateLimit": {"enabled": True, "requests": 1, "period": "1m", "key": "clientIp", "action": "captcha"}},
        })
