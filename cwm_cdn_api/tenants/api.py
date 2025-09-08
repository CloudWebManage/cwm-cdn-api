import re

from .. import db


TENANT_ID_RE = re.compile(r"^[a-z]([-a-z0-9]{0,53}[a-z0-9])?$")


def validate_tenant_id(tenant_id):
    if not tenant_id:
        raise ValueError("Tenant ID must not be empty")
    if len(tenant_id) < 3:
        raise ValueError("Tenant ID must be at least 3 characters long")
    if len(tenant_id) > 55:
        raise ValueError("Tenant ID must be at most 55 characters long")
    if not TENANT_ID_RE.match(tenant_id):
        raise ValueError("Tenant ID contains invalid characters, must start with a letter, end with a letter or digit, and contain only lowercase letters, digits, or hyphens")


async def create(tenant_id):
    validate_tenant_id(tenant_id)
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
