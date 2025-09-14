import os
import sys
import shutil
import datetime
import tempfile
import asyncio
import signal
import subprocess

import orjson

from .config import NAMESPACE
from .common import async_subprocess_status_output


def _serial():
    return datetime.datetime.utcnow().strftime("%Y%m%d%H")


def _apex(domain):
    parts = domain.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain


def _zone_header(apex, serial):
    apex_dot = apex if apex.endswith(".") else apex + "."
    return (
        f"$ORIGIN {apex_dot}\n"
        f"$TTL 60\n"
        f"@ IN SOA ns1.{apex_dot} hostmaster.{apex_dot} ({serial} 120 60 1209600 60)\n"
        f"@ IN NS ns1.{apex_dot}\n"
        f"ns1 IN A 127.0.0.1\n"
    )


async def main_daemon(zones_dir):
    print(f'Starting zone writer daemon, writing to {zones_dir}', file=sys.stderr)
    os.makedirs(zones_dir, exist_ok=True)
    os.chmod(zones_dir, 0o755)
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    while True:
        await main(zones_dir, daemon=False)
        if stop.is_set():
            break
        await asyncio.sleep(1)


async def iterate_tenant_domains():
    status, output = await async_subprocess_status_output(
        'kubectl', 'get', 'cdntenant.cdn.cloudwm-cdn.com', '-ojson', '-n', NAMESPACE,
        stderr=subprocess.STDOUT
    )
    if status == 0:
        for tenant in orjson.loads(output).get('items', []):
            yield tenant['metadata']['name'], [domain['name'] for domain in tenant['spec']['domains']]
    else:
        raise Exception(output)


async def main(zones_dir, daemon=False):
    if daemon:
        await main_daemon(zones_dir)
        return
    apex_records = {}
    async for tenant_name, domains in iterate_tenant_domains():
        for domain in domains:
            apex = _apex(domain)
            if apex not in apex_records:
                apex_records[apex] = {}
            domain_without_apex = domain[:-len(apex)].rstrip(".")
            apex_records[apex][domain_without_apex] = tenant_name
    apex_records_json = orjson.dumps(apex_records, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS).decode().strip()
    zone_filepath_json = f'{zones_dir}.json'
    if os.path.exists(zone_filepath_json):
        with open(zone_filepath_json, 'r') as f:
            if f.read().strip() == apex_records_json:
                return
    num_apex = 0
    num_records = 0
    serial = _serial()
    db_files = set([d for d in os.listdir(zones_dir) if d.endswith('.db')])
    for apex, records in apex_records.items():
        num_apex += 1
        with tempfile.NamedTemporaryFile('w', delete=False) as tmp:
            tmp.write(_zone_header(apex, serial))
            for domain_without_apex, tenant_id in records.items():
                num_records += 1
                left = '@' if not domain_without_apex else domain_without_apex
                target = f"tenant.{tenant_id}.svc.cluster.local."
                tmp.write(f"{left} IN CNAME {target}\n")
            tmp.write("\n")
        try:
            os.chmod(tmp.name, 0o755)
            shutil.move(tmp.name, f'{zones_dir}/{apex}.db')
            if f'{apex}.db' in db_files:
                db_files.remove(f'{apex}.db')
        except:
            os.remove(tmp.name)
            raise
    for old_db in db_files:
        if os.path.exists(f'{zones_dir}/{old_db}'):
            os.remove(f'{zones_dir}/{old_db}')
    with open(zone_filepath_json, 'w') as f:
        f.write(apex_records_json)
    print(f'Wrote {num_records} records in {num_apex} zones to {zones_dir}', file=sys.stderr)
