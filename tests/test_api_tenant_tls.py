import orjson
import pytest

from cwm_cdn_api import api


def tenant_spec(**domain_overrides):
    domain = {
        "name": "test.example.com",
        "cert": "cert",
        "key": "key",
        "tls": {"mode": "provided"},
    }
    domain.update(domain_overrides)
    return {
        "domains": [domain],
        "origins": [{"url": "http://origin.example.com"}],
    }


@pytest.mark.parametrize("field", [
    "issuerRef",
    "secretName",
    "clusterIssuer",
    "issuer",
    "certificateName",
])
def test_validate_spec_rejects_internal_certificate_fields(field):
    spec = tenant_spec(**{field: "tenant-provided-secret"})
    with pytest.raises(ValueError, match="internal certificate fields"):
        api.validate_spec(spec)


@pytest.mark.parametrize("version", ["TLSv1", "TLSv1.0", "TLSv1.1"])
def test_validate_spec_rejects_unsupported_tls_versions(version):
    spec = tenant_spec(tls={"mode": "provided", "minVersion": version})
    with pytest.raises(ValueError, match="TLSv1.2 or TLSv1.3"):
        api.validate_spec(spec)


def test_validate_spec_accepts_multiple_domains_and_letsencrypt():
    spec = tenant_spec()
    spec["domains"].append({
        "name": "customer.example.com",
        "tls": {"mode": "letsencrypt", "redirectHttpToHttps": True},
    })
    api.validate_spec(spec)


def test_validate_spec_rejects_letsencrypt_with_inline_certificate():
    spec = tenant_spec(tls={"mode": "letsencrypt"})
    with pytest.raises(ValueError, match="must not include cert or key"):
        api.validate_spec(spec)


@pytest.mark.parametrize("domain", ["localhost", "*.example.com", "-bad.example.com", "bad-.example.com"])
def test_validate_spec_rejects_invalid_letsencrypt_domain(domain):
    spec = tenant_spec(name=domain, cert=None, key=None, tls={"mode": "letsencrypt"})
    with pytest.raises(ValueError, match="valid customer-owned domain"):
        api.validate_spec(spec)


def test_validate_spec_rejects_non_object_tls():
    spec = tenant_spec(tls="letsencrypt")
    with pytest.raises(ValueError, match="tls must be an object"):
        api.validate_spec(spec)


def test_certificate_resource_details_only_includes_letsencrypt_domains():
    spec = tenant_spec()
    spec["domains"].append({
        "name": "customer.example.com",
        "tls": {"mode": "letsencrypt"},
    })
    details = api.certificate_resource_details("tenant1", spec)
    assert details == [{
        "domain": "customer.example.com",
        "namespace": "tenant1",
        "certificateName": api._domain_certificate_name(1, "customer.example.com"),
        "secretName": api._domain_certificate_name(1, "customer.example.com"),
        "issuerRef": {"name": "letsencrypt", "kind": "ClusterIssuer"},
    }]


def test_redacted_domain_includes_tls_status_without_secrets():
    redacted = api._redacted_domain(
        {"name": "test.example.com", "cert": "cert", "key": "key", "tls": {"mode": "provided"}},
        {"test.example.com": {"name": "test.example.com", "mode": "provided", "ready": True}},
    )
    assert "cert" not in redacted
    assert "key" not in redacted
    assert redacted["tlsStatus"]["ready"] is True


@pytest.mark.asyncio
async def test_get_redacts_secrets_and_returns_domain_tls(monkeypatch):
    tenant = {
        "spec": {
            "domains": [{"name": "test.example.com", "cert": "cert", "key": "key"}],
            "origins": [{"url": "http://origin.example.com"}],
        },
        "status": {
            "conditions": [
                {"type": "Progressing", "status": "False"},
                {"type": "Ready", "status": "True"},
                {"type": "Degraded", "status": "False"},
            ],
            "domainTLS": [{"name": "test.example.com", "mode": "provided", "ready": True}],
        },
    }

    async def fake_status_output(*args, **kwargs):
        return 0, orjson.dumps(tenant)

    monkeypatch.setattr(api, "async_subprocess_status_output", fake_status_output)
    success, result = await api.get("tenant1")
    assert success is True
    assert result["ready"] is True
    assert result["domainTLS"] == tenant["status"]["domainTLS"]
    assert "cert" not in result["domains"][0]
    assert "key" not in result["domains"][0]
    assert result["domains"][0]["tlsStatus"]["ready"] is True
