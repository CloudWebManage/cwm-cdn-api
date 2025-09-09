import sys
import asyncclick as click

from .. import common


@click.group()
async def main():
    pass


@main.command()
@click.argument('tenant_id')
@click.argument('domain')
@click.option('--cert', default=None)
@click.option('--key', default=None)
async def create(**kwargs):
    from .api import create
    common.json_print(await create(**kwargs))


@main.command()
@click.argument('tenant_id')
@click.argument('domain')
async def get(**kwargs):
    from .api import get
    common.json_print(await get(**kwargs))


@main.command(name='list')
@click.argument('tenant_id')
async def list_(tenant_id):
    from .api import list_iterator
    num_domains = 0
    async for domain in list_iterator(tenant_id):
        print(domain)
        num_domains += 1
    print(f'Total domains: {num_domains}', file=sys.stderr)


@main.command()
@click.argument('zones_dir')
@click.option('--daemon', is_flag=True)
async def zone_writer(zones_dir, daemon):
    from . import zone_writer
    await zone_writer.main(zones_dir, daemon)
