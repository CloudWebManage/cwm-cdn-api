import sys
import asyncclick as click

from .. import common


@click.group()
async def main():
    pass


@main.command()
@click.argument('tenant_id')
async def create(**kwargs):
    from .api import create
    common.json_print(await create(**kwargs))


@main.command()
@click.argument('tenant_id')
async def get(**kwargs):
    from .api import get
    common.json_print(await get(**kwargs))


@main.command(name='list')
async def list_():
    from .api import list_iterator
    num_tenants = 0
    async for tenant_id in list_iterator():
        print(tenant_id)
        num_tenants += 1
    print(f'Total tenants: {num_tenants}', file=sys.stderr)
