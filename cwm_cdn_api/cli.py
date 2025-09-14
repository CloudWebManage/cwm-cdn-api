import sys
import logging

import orjson
import asyncclick as click

from . import config, common


logging.basicConfig(
    level=getattr(logging, config.CWM_LOG_LEVEL),
    handlers=[logging.StreamHandler(sys.stderr)]
)


@click.group()
async def main():
    pass


@main.command()
@click.argument('cdn_tenant_name')
@click.argument('cdn_tenant_spec_json')
async def apply(cdn_tenant_name, cdn_tenant_spec_json):
    from . import api
    common.json_print(await api.apply(cdn_tenant_name, orjson.loads(cdn_tenant_spec_json)))


@main.command()
@click.argument('cdn_tenant_name')
async def delete(cdn_tenant_name):
    from . import api
    common.json_print(await api.delete(cdn_tenant_name))


@main.command()
@click.argument('cdn_tenant_name')
async def get(cdn_tenant_name):
    from . import api
    from . import common
    common.json_print(await api.get(cdn_tenant_name))


@main.command(name='list')
async def list_():
    from . import api
    num_tenants = 0
    async for tenant_name in api.list_iterator():
        print(tenant_name)
        num_tenants += 1
    print(f'Total CDN tenants: {num_tenants}', file=sys.stderr)


@main.command()
async def reserved_names():
    from . import api
    async for name in api.reserved_names_iterator():
        print(name)


@main.command()
@click.argument('zones_dir')
async def start_zone_writer(zones_dir):
    from . import zone_writer
    await zone_writer.main(zones_dir, daemon=True)


if __name__ == '__main__':
    main()
