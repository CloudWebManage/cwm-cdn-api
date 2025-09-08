from cwm_cdn_api.tenants import api as tenants_api


async def test_crud(cwm_test_db):
    tenant_id = 'test-tenant-1'
    created_tenant = await tenants_api.create(tenant_id)
    assert created_tenant == {
        'tenant_id': tenant_id,
    }
    assert [t async for t in tenants_api.list_iterator()] == [tenant_id]
    assert (await tenants_api.get(tenant_id)) == created_tenant
