from cwm_cdn_api.tenants import api as tenants_api
from cwm_cdn_api.origins import api as origins_api
import pytest


async def test_crud(cwm_test_db):
    tenant_id = 'tenant1'
    origin = 'https://origin.example.com'
    await tenants_api.create(tenant_id)
    created_origin = await origins_api.create(tenant_id, origin)
    assert created_origin == {
        'tenant_id': tenant_id,
        'origin_url': origin,
    }
    assert [o async for o in origins_api.list_iterator(tenant_id)] == [origin]
    assert await origins_api.get(tenant_id, origin) == created_origin


async def test_duplicate_and_cross_tenant(cwm_test_db):
    tenant1 = 'tenant1'
    tenant2 = 'tenant2'
    origin = 'https://origin.example.com'
    await tenants_api.create(tenant1)
    await tenants_api.create(tenant2)
    await origins_api.create(tenant1, origin)
    with pytest.raises(Exception):
        await origins_api.create(tenant1, origin)
    created = await origins_api.create(tenant2, origin)
    assert created == {
        'tenant_id': tenant2,
        'origin_url': origin,
    }


async def test_nonexistent_tenant(cwm_test_db):
    with pytest.raises(Exception):
        await origins_api.create('missing-tenant', 'https://missing.example.com')
