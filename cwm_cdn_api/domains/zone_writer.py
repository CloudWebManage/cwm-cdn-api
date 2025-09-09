import os
import sys
import shutil
import datetime
import tempfile
import asyncio

import orjson

from .. import db, config


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


async def main_daemon(zone_filepath):
    print('Starting zone writer daemon', file=sys.stderr)
    while True:
        await main(zone_filepath, daemon=False)
        await asyncio.sleep(1)


async def main(zone_filepath, daemon=False):
    if daemon:
        await main_daemon(zone_filepath)
        return
    apex_records = {}
    async with db.connection_cursor() as (conn, cur):
        await cur.execute('select tenant_id, domain from tenant_domains')
        async for row in cur:
            domain = row['domain']
            apex = _apex(domain)
            if apex not in apex_records:
                apex_records[apex] = {}
            domain_without_apex = domain[:-len(apex)].rstrip(".")
            apex_records[apex][domain_without_apex] = row['tenant_id']
    apex_records_json = orjson.dumps(apex_records, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS).decode().strip()
    zone_filepath_json = f'{zone_filepath}.json'
    if os.path.exists(zone_filepath_json):
        with open(zone_filepath_json, 'r') as f:
            if f.read().strip() == apex_records_json:
                return
    num_apex = 0
    num_records = 0
    serial = _serial()
    with tempfile.NamedTemporaryFile('w', delete=False) as tmp:
        for apex, records in apex_records.items():
            num_apex += 1
            tmp.write(_zone_header(apex, serial))
            for domain_without_apex, tenant_id in records.items():
                num_records += 1
                left = '@' if not domain_without_apex else domain_without_apex
                target = f"front.{tenant_id}.svc.cluster.local."
                tmp.write(f"{left} IN CNAME {target}\n")
            tmp.write("\n")
    try:
        os.chmod(tmp.name, 0o755)
        shutil.move(tmp.name, zone_filepath)
    except:
        os.remove(tmp.name)
        raise
    with open(zone_filepath_json, 'w') as f:
        f.write(apex_records_json)
    print(f'Wrote {num_records} records in {num_apex} zones to {zone_filepath}', file=sys.stderr)
