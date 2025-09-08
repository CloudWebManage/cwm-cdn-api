import sys
import asyncclick as click

from .. import common


@click.group()
async def main():
    pass


@main.command()
@click.argument('tenant_id')
@click.argument('origin_url')
async def create(**kwargs):
    from .api import create
    common.json_print(await create(**kwargs))


@main.command()
@click.argument('tenant_id')
@click.argument('origin_url')
async def get(**kwargs):
    from .api import get
    common.json_print(await get(**kwargs))


@main.command(name='list')
@click.argument('tenant_id')
async def list_(tenant_id):
    from .api import list_iterator
    num_origins = 0
    async for origin_url in list_iterator(tenant_id):
        print(origin_url)
        num_origins += 1
    print(f'Total origins: {num_origins}', file=sys.stderr)
