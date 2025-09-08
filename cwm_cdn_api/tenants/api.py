from .. import db


async def create(tenant_id):
    async with db.connection_cursor() as (conn, cur):
        if await get(tenant_id, cur=cur) is not None:
            raise Exception('Tenant already exists')
        await cur.execute('INSERT INTO tenants (id) VALUES (%s)', (tenant_id,))
        await conn.commit()
        return await get(tenant_id, cur=cur)


async def get(tenant_id, cur=None):
    async with db.connection_cursor(cur=cur) as (conn, cur):
        await cur.execute('SELECT id FROM tenants WHERE id = %s', (tenant_id,))
        row = await cur.fetchone()
        if row:
            return {
                'tenant_id': row['id'],
            }
        else:
            return None


async def list_iterator():
    async with db.connection_cursor() as (conn, cur):
        await cur.execute('SELECT id FROM tenants')
        async for row in cur:
            yield row['id']
