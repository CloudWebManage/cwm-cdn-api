import idna
from .. import db
from ..tenants.api import get as get_tenant


async def create(tenant_id, domain, cert=None, key=None):
    if domain.startswith('.') or domain.endswith('.'):
        raise ValueError('Domain must not start or end with a dot')
    encoded_domain = idna.encode(domain).decode().lower()
    if encoded_domain != domain:
        raise ValueError('Domain must be ASCII, lowercase only')
    if len(domain.split('.')) < 3:
        raise ValueError('Must be a subdomain (e.g. sub.example.com)')
    async with db.connection_cursor() as (conn, cur):
        if await get_tenant(tenant_id, cur=cur) is None:
            raise Exception('Tenant does not exist')
        if await get(tenant_id, domain, cur=cur) is not None:
            raise Exception('Domain already exists in this tenant')
        await cur.execute('select 1 from tenant_domains where domain = %s', (domain,))
        if (await cur.fetchone()) is not None:
            raise Exception('Domain already taken in another tenant')
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
