from .. import db
from ..tenants import api as tenants_api


async def create(tenant_id, origin_url):
    async with db.connection_cursor() as (conn, cur):
        if not await tenants_api.get(tenant_id):
            raise Exception('Tenant not found')
        if await get(tenant_id, origin_url, cur=cur) is not None:
            raise Exception('Origin already exists')
        await cur.execute(
            'INSERT INTO tenant_origins (tenant_id, origin_url) VALUES (%s, %s)',
            (tenant_id, origin_url)
        )
        await conn.commit()
        return await get(tenant_id, origin_url, cur=cur)


async def get(tenant_id, origin_url, cur=None):
    async with db.connection_cursor(cur=cur) as (conn, cur):
        await cur.execute(
            'SELECT tenant_id, origin_url FROM tenant_origins WHERE tenant_id = %s AND origin_url = %s',
            (tenant_id, origin_url)
        )
        row = await cur.fetchone()
        if row:
            return {
                'tenant_id': row['tenant_id'],
                'origin_url': row['origin_url'],
            }
        else:
            return None


async def list_iterator(tenant_id):
    async with db.connection_cursor() as (conn, cur):
        await cur.execute('SELECT origin_url FROM tenant_origins WHERE tenant_id = %s', (tenant_id,))
        async for row in cur:
            yield row['origin_url']
