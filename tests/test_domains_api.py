from cwm_cdn_api.tenants import api as tenants_api
from cwm_cdn_api.domains import api as domains_api
import pytest


async def test_crud(cwm_test_db):
    tenant_id = 'tenant1'
    domain = 'test.example.com'
    await tenants_api.create(tenant_id)
    created_domain = await domains_api.create(tenant_id, domain, cert='cert', key='key')
    assert created_domain == {
        'tenant_id': tenant_id,
        'domain': domain,
        'cert': 'cert',
    }
    assert [d async for d in domains_api.list_iterator(tenant_id)] == [domain]
    assert await domains_api.get(tenant_id, domain) == created_domain


async def test_validation_and_uniqueness(cwm_test_db):
    tenant1 = 'tenant1'
    tenant2 = 'tenant2'
    await tenants_api.create(tenant1)
    await tenants_api.create(tenant2)

    for bad_domain in ['.example.com', 'example.com.', 'EXAMPLE.com', 'example.com']:
        with pytest.raises(ValueError):
            await domains_api.create(tenant1, bad_domain)

    domain = 'unique.example.com'
    await domains_api.create(tenant1, domain)
    with pytest.raises(Exception):
        await domains_api.create(tenant1, domain)
    with pytest.raises(Exception):
        await domains_api.create(tenant2, domain)


async def test_nonexistent_tenant(cwm_test_db):
    with pytest.raises(Exception):
        await domains_api.create('missing-tenant', 'foo.example.com')
