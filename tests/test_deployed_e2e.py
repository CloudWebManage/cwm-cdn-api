import os
import uuid
import subprocess

import orjson
import dotenv
import pytest
import requests
import itertools
from retry import retry
from prometheus_client.parser import text_string_to_metric_families


dotenv.load_dotenv()


PRIMARY_API_URL = os.getenv("PRIMARY_API_URL")
PRIMARY_API_USERNAME = os.getenv("PRIMARY_API_USERNAME")
PRIMARY_API_PASSWORD = os.getenv("PRIMARY_API_PASSWORD")
PRIMARY_EDGE_DOMAIN = os.getenv("PRIMARY_EDGE_DOMAIN")
PRIMARY_KUBECONFIG = os.getenv("PRIMARY_KUBECONFIG")
SECONDARY_API_URL = os.getenv("SECONDARY_API_URL")
SECONDARY_API_USERNAME = os.getenv("SECONDARY_API_USERNAME")
SECONDARY_API_PASSWORD = os.getenv("SECONDARY_API_PASSWORD")
SECONDARY_EDGE_DOMAIN = os.getenv("SECONDARY_EDGE_DOMAIN")
SECONDARY_KUBECONFIG = os.getenv("SECONDARY_KUBECONFIG")


EXPECTED_CACHE_IMAGE_TAG = '160863a93c382f3ba70b8bd74bd7b16584b25124'
EXPECTED_CACHE_ROUTER_IMAGE_TAG = '160863a93c382f3ba70b8bd74bd7b16584b25124'
EXPECTED_EDGE_COREDNS_IMAGE_TAG = '986f04c2e15e147d00bdd51e8c51bcef3644b13ff806be7d2ff1b261d6dfbae1'
EXPECTED_EDGE_NGINX_IMAGE_TAG = '08370351309932e3feba7de76114b9fb416b114d'
EXPECTED_EDGE_ZONEWRITER_IMAGE_TAG = 'latest'
EXPECTED_OPERATOR_IMAGE_TAG = '565cdc982f91ffc5d5d6362584323c681320b570'

HTTPBIN_URL = os.getenv('HTTPBIN_URL') or 'https://httpbin.org'


def get_pods_image_tag(pods):
    image_tags = set()
    for pod in pods:
        image_tags.add(pod.get('image_tag'))
    return image_tags.pop() if len(image_tags) == 1 else ', '.join(image_tags)


def cdn_request_primary(method, path, **kwargs):
    return requests.request(
        method, f'{PRIMARY_API_URL}/{path}', timeout=10,
        auth=(PRIMARY_API_USERNAME, PRIMARY_API_PASSWORD), **kwargs
    )


def get_api_status(api_url, username, password):
    res = requests.get(f'{api_url}/components-status', auth=(username, password), timeout=10)
    if res.status_code != 200:
        return False, f'Error calling API: {res.status_code} {res.text}'
    res = res.json()

    cache_caches = {k: v for k, v in res.get('cache', {}).items() if k.startswith('cache')}
    if len(cache_caches) != 3:
        return False, f'Expected 3 cache caches, got {len(cache_caches)}'
    cache_image_tag = get_pods_image_tag(list(itertools.chain(*cache_caches.values())))
    if cache_image_tag != EXPECTED_CACHE_IMAGE_TAG:
        return False, f'Cache image tag mismatch: expected {EXPECTED_CACHE_IMAGE_TAG}, got {cache_image_tag}'

    cache_router = res.get('cache', {}).get('router', [])
    if len(cache_router) != 1:
        return False, f'Expected 1 router, got {len(cache_router)}'
    cache_router_image_tag = get_pods_image_tag(cache_router)
    if cache_router_image_tag != EXPECTED_CACHE_ROUTER_IMAGE_TAG:
        return False, f'Cache router image tag mismatch: expected {EXPECTED_CACHE_ROUTER_IMAGE_TAG}, got {cache_router_image_tag}'

    edge_coredns = res.get('edge', {}).get('coredns', [])
    if len(edge_coredns) != 3:
        return False, f'Expected 3 edge coredns, got {len(edge_coredns)}'
    edge_coredns_image_tag = get_pods_image_tag(edge_coredns)
    if edge_coredns_image_tag != EXPECTED_EDGE_COREDNS_IMAGE_TAG:
        return False, f'Edge coredns image tag mismatch: expected {EXPECTED_EDGE_COREDNS_IMAGE_TAG}, got {edge_coredns_image_tag}'

    edge_nginx = res.get('edge', {}).get('nginx', [])
    if len(edge_nginx) != 3:
        return False, f'Expected 3 edge nginx, got {len(edge_nginx)}'
    edge_nginx_image_tag = get_pods_image_tag(edge_nginx)
    if edge_nginx_image_tag != EXPECTED_EDGE_NGINX_IMAGE_TAG:
        return False, f'Edge nginx image tag mismatch: expected {EXPECTED_EDGE_NGINX_IMAGE_TAG}, got {edge_nginx_image_tag}'

    edge_zonewriter = res.get('edge', {}).get('zonewriter', [])
    if len(edge_zonewriter) != 3:
        return False, f'Expected 3 edge zonewriter, got {len(edge_zonewriter)}'
    edge_zonewriter_image_tag = get_pods_image_tag(edge_zonewriter)
    if edge_zonewriter_image_tag != EXPECTED_EDGE_ZONEWRITER_IMAGE_TAG:
        return False, f'Edge zonewriter image tag mismatch: expected {EXPECTED_EDGE_ZONEWRITER_IMAGE_TAG}, got {edge_zonewriter_image_tag}'

    operator = res.get('operator', [])
    if len(operator) != 1:
        return False, f'Expected 1 operator, got {len(operator)}'
    operator_image_tag = get_pods_image_tag(operator)
    if operator_image_tag != EXPECTED_OPERATOR_IMAGE_TAG:
        return False, f'Operator image tag mismatch: expected {EXPECTED_OPERATOR_IMAGE_TAG}, got {operator_image_tag}'

    return True, ""


def edge_request(edge_ip, tenant_domain, path):
    cmd = ['curl', '-vk', '--resolve', f'{tenant_domain}:443:{edge_ip}', '-H', f'Host: {tenant_domain}', f'https://{tenant_domain}/{path}']
    print(' '.join(cmd))
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode == 0, p.stderr, p.stdout


class AnythingEdgeRequestRetryException(Exception):
    pass


@retry(AnythingEdgeRequestRetryException, tries=20, delay=3)
def check_anything_edge_request(edge_ip, tenant_domain):
    ok, err, out = edge_request(edge_ip, tenant_domain, f'anything/{tenant_domain}')
    if not ok:
        print(f'Edge request failed, retrying... Error: {err}')
        raise AnythingEdgeRequestRetryException(f'Edge request failed: {err}')
    status_ok = False
    for line in err.splitlines():
        if line.strip() == '< HTTP/1.1 200 OK':
            status_ok = True
            break
    if not status_ok:
        print(f'Edge request did not return 200 OK, retrying... Error: {err}')
        raise AnythingEdgeRequestRetryException(f'Edge request did not return 200 OK: {err}')
    res = orjson.loads(out)
    assert res['url'] == f'{HTTPBIN_URL}/anything/{tenant_domain}', 'Unexpected URL in response'


def get_node_external_ip(kubeconfig, node_name):
    res = orjson.loads(subprocess.check_output(['kubectl', '--kubeconfig', kubeconfig, 'get', 'nodes', node_name, '-o', 'json']))
    for addr in res['status']['addresses']:
        if addr['type'] == 'ExternalIP':
            return addr['address']
    return None


def get_pod_name_on_node(kubeconfig, node_name, namespace_name, label_selector):
    res = orjson.loads(subprocess.check_output([
        'kubectl', '--kubeconfig', kubeconfig, 'get', 'pods', '-n', namespace_name, '-l', label_selector, '-o', 'json',
        '--field-selector', f'spec.nodeName={node_name}'
    ]))
    assert len(res['items']) == 1, f'Expected 1 pod on node {node_name} with label {label_selector}, got {len(res["items"])}'
    return res['items'][0]['metadata']['name']


def get_metric_sample_labels_from_exec(kubeconfig, pod_name, namespace_name):
    out = subprocess.check_output([
        'kubectl', '--kubeconfig', kubeconfig, 'exec', '-n', namespace_name, pod_name, '--',
        'curl', '-s', 'http://localhost:9999/metrics'
    ], text=True)
    sample_labels = {}
    for metric in text_string_to_metric_families(out):
        for sample in metric.samples:
            labels_str = ','.join(f'{k}={sample.labels[k]}' for k in sorted(sample.labels))
            sample_labels.setdefault(sample.name, {})[labels_str] = sample.value
    return sample_labels


@pytest.mark.skipif(os.getenv("DEPLOYED_E2E") != "yes", reason="Set DEPLOYED_E2E=yes to run deployed E2E tests on live environment")
def test():
    if not PRIMARY_API_URL or not PRIMARY_API_USERNAME or not PRIMARY_API_PASSWORD:
        print('Skipping E2E Tests - primary api creds missing')
        return
    if not SECONDARY_API_URL or not SECONDARY_API_USERNAME or not SECONDARY_API_PASSWORD:
        print('Skipping E2E Tests - secondary api creds missing')
        return
    print(f'Starting Deployed E2E Tests on Primary: {PRIMARY_API_URL} and Secondary: {SECONDARY_API_URL}')
    should_fail = False
    status, message = get_api_status(PRIMARY_API_URL, PRIMARY_API_USERNAME, PRIMARY_API_PASSWORD)
    if not status:
        print(f'WARNING! Primary API status check failed: {message}')
        should_fail = True
    status, message = get_api_status(SECONDARY_API_URL, SECONDARY_API_USERNAME, SECONDARY_API_PASSWORD)
    if not status:
        print(f'WARNING! Secondary API status check failed: {message}')
        should_fail = True
    with open(os.path.join(os.path.dirname(__file__), 'test_tenant.json'), 'r') as f:
        tenant = orjson.loads(f.read())
    tenant_name = 'de2e-' + uuid.uuid4().hex + '-t'
    print(f'Using test tenant name: {tenant_name}')
    tenant['domains'][0]['name'] = tenant_domain = f'{tenant_name}.example.com'
    tenant['origins'][0]['url'] = HTTPBIN_URL
    res = cdn_request_primary('GET', 'get', params={'cdn_tenant_name': tenant_name})
    assert res.status_code != 200, 'Test tenant should not exist yet'
    res = cdn_request_primary('POST', 'apply', params={'cdn_tenant_name': tenant_name}, json=tenant)
    assert res.status_code == 200, f'Error applying test tenant: {res.status_code} {res.text}'
    try:
        # collect data before making a request to the tenant
        primary_node_external_ip = get_node_external_ip(PRIMARY_KUBECONFIG, 'cdn1')
        print(f'Primary edge node cdn1 external IP: {primary_node_external_ip}')
        secondary_node_external_ip = get_node_external_ip(SECONDARY_KUBECONFIG, 'cdn1')
        print(f'Secondary edge node cdn1 external IP: {secondary_node_external_ip}')
        primary_edge_nginx_pod_name = get_pod_name_on_node(PRIMARY_KUBECONFIG, 'cdn1', 'cdn-edge', 'app=cdn-edge-nginx')
        print(f'Primary edge nginx pod on cdn1: {primary_edge_nginx_pod_name}')
        secondary_edge_nginx_pod_name = get_pod_name_on_node(SECONDARY_KUBECONFIG, 'cdn1', 'cdn-edge', 'app=cdn-edge-nginx')
        print(f'Secondary edge nginx pod on cdn1: {secondary_edge_nginx_pod_name}')
        primary_edge_nginx_metrics_before = get_metric_sample_labels_from_exec(PRIMARY_KUBECONFIG, primary_edge_nginx_pod_name, 'cdn-edge')
        secondary_edge_nginx_metrics_before = get_metric_sample_labels_from_exec(SECONDARY_KUBECONFIG, secondary_edge_nginx_pod_name, 'cdn-edge')

        # make requests to the tenant on both primary and secondary edges
        check_anything_edge_request(primary_node_external_ip, tenant_domain)
        check_anything_edge_request(secondary_node_external_ip, tenant_domain)

        # collect data after making requests to the tenant
        primary_edge_nginx_metrics_after = get_metric_sample_labels_from_exec(PRIMARY_KUBECONFIG, primary_edge_nginx_pod_name, 'cdn-edge')
        secondary_edge_nginx_metrics_after = get_metric_sample_labels_from_exec(SECONDARY_KUBECONFIG, secondary_edge_nginx_pod_name, 'cdn-edge')
        primary_tenant_metrics_after = get_metric_sample_labels_from_exec(PRIMARY_KUBECONFIG, 'deploy/tenant', tenant_name)
        secondary_tenant_metrics_after = get_metric_sample_labels_from_exec(SECONDARY_KUBECONFIG, 'deploy/tenant', tenant_name)

        # verify the data

        # cdn edge connections
        primary_tenant_stream_connections_before = primary_edge_nginx_metrics_before.get('nginx_stream_connections_total', {}).get(f'server_name={tenant_domain}') or 0
        primary_tenant_stream_connections_after = primary_edge_nginx_metrics_after.get('nginx_stream_connections_total', {}).get(f'server_name={tenant_domain}') or 0
        assert primary_tenant_stream_connections_after - primary_tenant_stream_connections_before >= 1, \
            f'Primary edge nginx stream connections did not increase for tenant {tenant_domain}: before={primary_tenant_stream_connections_before}, after={primary_tenant_stream_connections_after}'
        secondary_tenant_stream_connections_before = secondary_edge_nginx_metrics_before.get('nginx_stream_connections_total', {}).get(f'server_name={tenant_domain}') or 0
        secondary_tenant_stream_connections_after = secondary_edge_nginx_metrics_after.get('nginx_stream_connections_total', {}).get(f'server_name={tenant_domain}') or 0
        assert secondary_tenant_stream_connections_after - secondary_tenant_stream_connections_before >= 1, \
            f'Secondary edge nginx stream connections did not increase for tenant {tenant_domain}: before={secondary_tenant_stream_connections_before}, after={secondary_tenant_stream_connections_after}'

        # tenant front http requests
        primary_tenant_front_requests = primary_tenant_metrics_after.get('nginx_http_requests_total', {}).get(f'host={tenant_domain},status=200') or 0
        assert primary_tenant_front_requests >= 1, \
            f'Primary tenant front http requests metric not found or zero for tenant {tenant_domain}'
        secondary_tenant_front_requests = secondary_tenant_metrics_after.get('nginx_http_requests_total', {}).get(f'host={tenant_domain},status=200') or 0
        assert secondary_tenant_front_requests >= 1, \
            f'Secondary tenant front http requests metric not found or zero for tenant {tenant_domain}'

        # tenant origin http requests
        primary_tenant_origin_requests = primary_tenant_metrics_after.get('nginx_http_requests_total', {}).get(f'host=_,status=200') or 0
        assert primary_tenant_origin_requests >= 1, \
            f'Primary tenant origin http requests metric not found or zero for tenant {tenant_domain}'
        secondary_tenant_origin_requests = secondary_tenant_metrics_after.get('nginx_http_requests_total', {}).get(f'host=_,status=200') or 0
        assert secondary_tenant_origin_requests >= 1, \
            f'Secondary tenant origin http requests metric not found or zero for tenant {tenant_domain}'
    finally:
        res = cdn_request_primary('POST', 'delete', params={'cdn_tenant_name': tenant_name})
        assert res.status_code == 200, f'Error deleting test tenant: {res.status_code} {res.text}'
        print('Cleaned up test tenant')
    if should_fail:
        pytest.fail('Deployed E2E Tests failed, see messages above')
