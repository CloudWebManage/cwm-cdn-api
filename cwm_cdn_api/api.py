import os
import subprocess
import tempfile

import orjson

from .common import async_subprocess_check_output, async_subprocess_status_output
from .config import NAMESPACE, ALLOWED_PRIMARY_KEY, IS_PRIMARY


async def reserved_names_iterator():
    tenants = set([n async for n in list_iterator()])
    for n in (await async_subprocess_check_output('kubectl', 'get', 'ns', '-oname')).splitlines():
        if n.startswith('namespace/'):
            n = n.split('/', 1)[1]
            if n not in tenants:
                yield n


async def validate_name(name):
    async for reserved_name in reserved_names_iterator():
        if name == reserved_name:
            raise ValueError(f'Tenant name "{name}" is not allowed')


async def apply(name, spec):
    await validate_name(name)
    primary_key = spec.pop("primaryKey", "")
    if not IS_PRIMARY and primary_key != ALLOWED_PRIMARY_KEY:
        return False, 'Updates are not allowed on this instance'
    o = {
        'apiVersion': 'cdn.cloudwm-cdn.com/v1',
        'kind': 'CdnTenant',
        'metadata': {
            'name': name,
            'namespace': NAMESPACE,
        },
        'spec': spec,
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, f'{name}.json'), 'wb') as f:
            f.write(orjson.dumps(o))
        status, output = await async_subprocess_status_output(
            'kubectl', 'apply', '-f', f'{name}.json',
            stderr=subprocess.STDOUT, cwd=tmpdir
        )
    return (status == 0), output


async def delete(name, primary_key=""):
    if not IS_PRIMARY and primary_key != ALLOWED_PRIMARY_KEY:
        return False, 'Deletes are not allowed on this instance'
    status, output = await async_subprocess_status_output(
        'kubectl', 'delete', 'cdntenant.cdn.cloudwm-cdn.com', name, '-n', NAMESPACE, '--wait=false',
        stderr=subprocess.STDOUT
    )
    return (status == 0), output


async def get(name):
    status, output = await async_subprocess_status_output(
        'kubectl', 'get', 'cdntenant.cdn.cloudwm-cdn.com', name, '-n', NAMESPACE, '-o', 'json',
        stderr=subprocess.STDOUT
    )
    if status == 0:
        o = orjson.loads(output)
        conditions = {
            condition['type']: condition
            for condition in o.get('status', {}).get('conditions', [])
            if condition['type'] != 'SecondariesSynced' or IS_PRIMARY
        }
        ready = (
            conditions.get("Progressing", {}).get("status") == "False"
            and conditions.get("Ready", {}).get("status") == "True"
            and conditions.get("Degraded", {}).get("status") == "False"
        )
        return True, {
            'domains': [{
                k: v for k, v in domain.items() if k not in ('cert', 'key')
            } for domain in o['spec'].get('domains', [])],
            'origins': [
                origin for origin in o['spec'].get('origins', [])
            ],
            'ready': ready,
            'conditions': conditions,
        }
    else:
        return False, output


async def list_iterator():
    status, output = await async_subprocess_status_output(
        'kubectl', 'get', 'cdntenant.cdn.cloudwm-cdn.com', '-oname', '-n', NAMESPACE,
        stderr=subprocess.STDOUT
    )
    if status == 0:
        for name in output.splitlines():
            if name:
                yield name.split('/', 1)[1]
    else:
        raise Exception(output)


def parse_pod_status(pod):
    creation_timestamp = pod['metadata']['creationTimestamp']
    image = pod['spec']['containers'][0]['image']
    image_tag = image.split(':')[-1] if ':' in image else 'latest'
    status_phase = pod['status']['phase']
    return {
        'creation_timestamp': creation_timestamp,
        'image_tag': image_tag,
        'status_phase': status_phase,
    }


async def components_status():
    res = {
        'cache': {},
        'edge': {},
        'operator': [],
    }
    for pod in orjson.loads(await async_subprocess_check_output(
        'kubectl', '-n', 'cdn-cache', 'get', 'pods', '-o', 'json'
    ))['items']:
        res['cache'].setdefault(pod['metadata']['name'].split('-')[0], []).append(parse_pod_status(pod))
    for pod in orjson.loads(await async_subprocess_check_output(
        'kubectl', '-n', 'cdn-edge', 'get', 'pods', '-o', 'json'
    ))['items']:
        res['edge'].setdefault(pod['metadata']['name'].split('-')[2], []).append(parse_pod_status(pod))
    for pod in orjson.loads(await async_subprocess_check_output(
        'kubectl', '-n', 'cwm-cdn-operator-system', 'get', 'pods', '-o', 'json'
    ))['items']:
        res['operator'].append(parse_pod_status(pod))
    return res
