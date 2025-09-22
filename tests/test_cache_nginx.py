import os
import subprocess

import pytest


def test_replace_keys(cache_nginx_entrypoint):
    assert cache_nginx_entrypoint.replace_keys(
        "hello __NAME__, welcome to __PLACE__!",
        {
            "__NAME__": "Alice",
            "__PLACE__": "Wonderland",
        }
    ) == "hello Alice, welcome to Wonderland!"


def test_get_default_conf_router(cache_nginx_entrypoint):
    assert cache_nginx_entrypoint.get_default_conf({
        "TYPE": "router",
        "NGINX_UPSTREAM_CACHE_SERVERS": "cache1:80\ncache2:80\n",
    }) == cache_nginx_entrypoint.replace_keys(
        cache_nginx_entrypoint.DEFAULT_CONF_ROUTER_TEMPLATE,
        {
            "__NGINX_HTTP_CONFIGS__": "",
            "__NGINX_SERVER_CONFIGS__": "",
            "__NGINX_LOCATION_CONFIGS__": "",
            "__NGINX_UPSTREAM_CACHE_SERVERS__": "cache1:80\ncache2:80\n",
        }
    )


def test_get_default_conf_cache(cache_nginx_entrypoint):
    assert cache_nginx_entrypoint.get_default_conf({
        "TYPE": "cache",
    }) == cache_nginx_entrypoint.replace_keys(
        cache_nginx_entrypoint.DEFAULT_CONF_CACHE_TEMPLATE,
        {
            "__NGINX_HTTP_CONFIGS__": "",
            "__NGINX_SERVER_CONFIGS__": "",
            "__NGINX_LOCATION_CONFIGS__": "",
            "__NGINX_RESOLVER_CONFIG__": cache_nginx_entrypoint.DEFAULT_NGINX_RESOLVER_CONFIG,
            "__TENANT_PROXY_PASS_DOMAIN__": cache_nginx_entrypoint.DEFAULT_TENANT_PROXY_PASS_DOMAIN,
        }
    )


def test_main(tmpdir, cache_nginx_entrypoint):
    nginx_conf_path = tmpdir
    os.makedirs(os.path.join(nginx_conf_path, "conf.d"))
    cache_nginx_entrypoint.main(nginx_conf_path, {
        "TYPE": "cache",
    })
    with open(os.path.join(nginx_conf_path, "conf.d/default.conf")) as f:
        assert f.read() == cache_nginx_entrypoint.get_default_conf({
            "TYPE": "cache",
        })


def assert_curl_cache_server(tenant_name, expected_output, expected_cache_server):
    p = subprocess.Popen([
        "curl", "-v",
        "-H", f"X-CWMCDN-TENANT-NAME: {tenant_name}",
        f"http://localhost:48180"
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert p.wait() == 0
    assert p.stdout.read().decode().strip() == expected_output
    cache_server = None
    for line in p.stderr.read().decode().splitlines():
        if line.startswith('< X-CWMCDN-Cache-Server: '):
            cache_server = line.strip().split(':')[1].strip()
    assert cache_server == expected_cache_server


@pytest.mark.skipif(os.getenv("E2E") != "yes", reason="Set E2E=yes to run E2E tests")
def test_e2e():
    try:
        subprocess.check_call([
            "docker", "compose", "-f", "test_cache_nginx_compose.yaml",
            "up", "--wait", "--yes", "--build", "--force-recreate", "--remove-orphans",
        ], cwd=os.path.join(os.path.dirname(__file__)))
        assert_curl_cache_server("tenant1", "tenant1", "cache3")
        assert_curl_cache_server("tenant2", "tenant2", "cache3")
        assert_curl_cache_server("tenant3", "tenant3", "cache1")
        assert_curl_cache_server("tenant4", "tenant4", "cache2")
    finally:
        subprocess.call([
            "docker", "compose", "-f", "test_cache_nginx_compose.yaml", "down", "-v"
        ], cwd=os.path.join(os.path.dirname(__file__)))
