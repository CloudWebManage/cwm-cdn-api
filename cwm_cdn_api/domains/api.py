from .. import db


async def create(tenant_id, domain, cert=None, key=None):
    async with db.connection_cursor() as (conn, cur):
        if await get(tenant_id, domain, cur=cur) is not None:
            raise Exception('Domain already exists')
        await cur.execute(
            'INSERT INTO tenant_domains (tenant_id, domain, cert, key) VALUES (%s, %s, %s, %s)',
            (tenant_id, domain, cert, key)
        )
        await conn.commit()
        return await get(tenant_id, domain, cur=cur)


async def get(tenant_id, domain, cur=None):
    async with db.connection_cursor(cur=cur) as (conn, cur):
        await cur.execute(
            'SELECT tenant_id, domain, cert FROM tenant_domains WHERE tenant_id = %s AND domain = %s',
            (tenant_id, domain)
        )
        row = await cur.fetchone()
        if row:
            return {
                'tenant_id': row['tenant_id'],
                'domain': row['domain'],
                'cert': row['cert'],
            }
        else:
            return None


async def list_iterator(tenant_id):
    async with db.connection_cursor() as (conn, cur):
        await cur.execute('SELECT domain FROM tenant_domains WHERE tenant_id = %s', (tenant_id,))
        async for row in cur:
            yield row['domain']
