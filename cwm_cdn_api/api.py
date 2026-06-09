import os
import re
import subprocess
import tempfile
from copy import deepcopy

import orjson

from .common import async_subprocess_check_output, async_subprocess_status_output
from .config import NAMESPACE, ALLOWED_PRIMARY_KEY, IS_PRIMARY


FORBIDDEN_TENANT_FIELDS = {
    'issuerRef', 'secretName', 'certificate', 'certificates', 'dnsNames',
    'commonName', 'duration', 'renewBefore', 'usages', 'privateKey',
    'keystores', 'subject', 'isCA', 'acme', 'solver', 'solvers',
    'clusterIssuer', 'issuer', 'certificateName', 'generatedSecretName',
}
SUPPORTED_TLS_MODES = {'provided', 'letsencrypt'}
SUPPORTED_TLS_VERSIONS = {'TLSv1.2', 'TLSv1.3'}
DNS_LABEL_RE = re.compile(r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$', re.IGNORECASE)


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


def _find_forbidden_fields(value, path='spec'):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f'{path}.{key}'
            if key in FORBIDDEN_TENANT_FIELDS:
                yield child_path
            yield from _find_forbidden_fields(child, child_path)
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from _find_forbidden_fields(child, f'{path}[{i}]')


def _domain_tls(domain):
    tls = domain.get('tls') or {}
    mode = tls.get('mode') or 'provided'
    min_version = tls.get('minVersion') or 'TLSv1.2'
    max_version = tls.get('maxVersion') or 'TLSv1.3'
    return tls, mode, min_version, max_version


def _is_valid_domain_name(name):
    if not isinstance(name, str) or not name or len(name) > 253:
        return False
    name = name.rstrip('.')
    if not name or '*' in name:
        return False
    labels = name.split('.')
    if len(labels) < 2:
        return False
    return all(DNS_LABEL_RE.match(label) for label in labels)


def validate_spec(spec):
    if not isinstance(spec, dict):
        raise ValueError('Tenant spec must be an object')

    forbidden = sorted(_find_forbidden_fields(spec))
    if forbidden:
        raise ValueError(f'Tenant spec contains internal certificate fields: {", ".join(forbidden)}')

    domains = spec.get('domains')
    if not isinstance(domains, list) or not domains:
        raise ValueError('Tenant spec must include at least one domain')

    for i, domain in enumerate(domains):
        if not isinstance(domain, dict):
            raise ValueError(f'domains[{i}] must be an object')
        if not domain.get('name'):
            raise ValueError(f'domains[{i}].name is required')
        if 'tls' in domain and domain['tls'] is not None and not isinstance(domain['tls'], dict):
            raise ValueError(f'domains[{i}].tls must be an object')
        tls, mode, min_version, max_version = _domain_tls(domain)
        if mode not in SUPPORTED_TLS_MODES:
            raise ValueError(f'domains[{i}].tls.mode must be one of: letsencrypt, provided')
        if min_version not in SUPPORTED_TLS_VERSIONS:
            raise ValueError(f'domains[{i}].tls.minVersion must be TLSv1.2 or TLSv1.3')
        if max_version not in SUPPORTED_TLS_VERSIONS:
            raise ValueError(f'domains[{i}].tls.maxVersion must be TLSv1.2 or TLSv1.3')
        if min_version == 'TLSv1.3' and max_version == 'TLSv1.2':
            raise ValueError(f'domains[{i}].tls.minVersion cannot be greater than maxVersion')
        if mode == 'provided' and (not domain.get('cert') or not domain.get('key')):
            raise ValueError(f'domains[{i}] with tls.mode=provided requires cert and key')
        if mode == 'letsencrypt':
            if domain.get('cert') or domain.get('key'):
                raise ValueError(f'domains[{i}] with tls.mode=letsencrypt must not include cert or key')
            if not _is_valid_domain_name(domain.get('name')):
                raise ValueError(f'domains[{i}].name must be a valid customer-owned domain for tls.mode=letsencrypt')

    origins = spec.get('origins')
    if not isinstance(origins, list) or not origins:
        raise ValueError('Tenant spec must include at least one origin')


def _redacted_domain(domain, tls_status_by_name):
    redacted = {k: v for k, v in domain.items() if k not in ('cert', 'key')}
    tls_status = tls_status_by_name.get(domain.get('name'))
    if tls_status:
        redacted['tlsStatus'] = tls_status
    return redacted


def certificate_resource_details(name, spec):
    details = []
    for i, domain in enumerate(spec.get('domains', [])):
        _, mode, _, _ = _domain_tls(domain)
        if mode != 'letsencrypt':
            continue
        details.append({
            'domain': domain.get('name'),
            'namespace': name,
            'certificateName': _domain_certificate_name(i, domain.get('name', '')),
            'secretName': _domain_certificate_name(i, domain.get('name', '')),
            'issuerRef': {'name': 'letsencrypt', 'kind': 'ClusterIssuer'},
        })
    return details


def _domain_certificate_name(index, domain_name):
    import hashlib
    return f'tenant-domain-{index}-{hashlib.sha256(domain_name.encode()).hexdigest()[:12]}'


def validate_admin_primary_key(primary_key):
    if not primary_key or primary_key != ALLOWED_PRIMARY_KEY:
        raise ValueError('Valid primary_key is required for platform-admin debug output')


async def apply(name, spec):
    await validate_name(name)
    spec = deepcopy(spec)
    primary_key = spec.pop("primaryKey", "")
    if not IS_PRIMARY and primary_key != ALLOWED_PRIMARY_KEY:
        return False, 'Updates are not allowed on this instance'
    try:
        validate_spec(spec)
    except ValueError as e:
        return False, str(e)
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
        tls_status_by_name = {
            status['name']: status for status in o.get('status', {}).get('domainTLS', [])
        }
        return True, {
            'domains': [_redacted_domain(domain, tls_status_by_name) for domain in o['spec'].get('domains', [])],
            'origins': [
                origin for origin in o['spec'].get('origins', [])
            ],
            'domainTLS': o.get('status', {}).get('domainTLS', []),
            'ready': ready,
            'conditions': conditions,
        }
    else:
        return False, output


async def debug_certificates(name, primary_key):
    try:
        validate_admin_primary_key(primary_key)
    except ValueError as e:
        return False, str(e)
    status, output = await async_subprocess_status_output(
        'kubectl', 'get', 'cdntenant.cdn.cloudwm-cdn.com', name, '-n', NAMESPACE, '-o', 'json',
        stderr=subprocess.STDOUT
    )
    if status != 0:
        return False, output
    o = orjson.loads(output)
    return True, {
        'certificates': certificate_resource_details(name, o.get('spec', {})),
    }


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
