import os
import time
import json
import subprocess
from glob import glob

import pytest


TEST_TENANT_NAME = "tenant1"

TEST_TENANT = {
    "TENANT_NAME": TEST_TENANT_NAME,
}

TEST_DOMAIN0 = {
    "D0_NAME": "test1.example.com",
    "D0_CERT": "cert1",
    "D0_KEY": "key1",
}

TEST_ORIGIN0 = {
    "O0_URL": "http://origin.example.com",
}


def test_replace_keys(tenant_nginx_entrypoint):
    assert tenant_nginx_entrypoint.replace_keys(
        "hello __NAME__, welcome to __PLACE__!",
        {
            "__NAME__": "Alice",
            "__PLACE__": "Wonderland",
        }
    ) == "hello Alice, welcome to Wonderland!"


def test_parse_configs(tenant_nginx_entrypoint):
    domains, origins = tenant_nginx_entrypoint.parse_configs({
        "D0_NAME": "test1.example.com",
        "D0_CERT": "cert1",
        "D0_KEY": "key1",
        "D0_FOO": "bar1",
        "D1_NAME": "test2.example.com",
        "D1_FOO": "bar2",
        "D5_XYZ": "AAA",
        "O1_AAA": "bbb",
        "O2_BBB": "ccc",
        "PATH": "...",
        "C3_ZZZ": "...",
        "CA4_YYY": "...",
        "DA5_QQQ": "...",
        "D6_WWW": "www",
        "D6_aaa-bbb": "ccc",
        "D5_ZZZ": "zzz",
    })
    assert domains == [
        {
            "NAME": "test1.example.com",
            "CERT": "cert1",
            "KEY": "key1",
            "FOO": "bar1",
        },
        {
            "NAME": "test2.example.com",
            "FOO": "bar2",
        },
        {
            "XYZ": "AAA",
            "ZZZ": "zzz",
        },
        {
            "WWW": "www",
            "AAA-BBB": "ccc",
        },
    ]
    assert origins == [
        {
            "AAA": "bbb",
        },
        {
            "BBB": "ccc",
        },
    ]


def assert_domain_server_config(i, certs_path, cert, key):
    cert_files = set([f.split("/")[-1] for f in glob(os.path.join(certs_path, "tls*"))])
    assert f"tls{i}.crt" in cert_files
    assert f"tls{i}.key" in cert_files
    with open(os.path.join(certs_path, f"tls{i}.crt")) as f:
        assert f.read() == cert
        assert os.fstat(f.fileno()).st_mode & 0o777 == 0o600
    with open(os.path.join(certs_path, f"tls{i}.key")) as f:
        assert f.read() == key
        assert os.fstat(f.fileno()).st_mode & 0o777 == 0o600


def expected_domain_server_config(tenant_nginx_entrypoint, i, certs_path, name, tenant_name, access_log_config='access_log off;', tls_protocols='TLSv1.2 TLSv1.3', redirect=False):
    cert_path = os.path.join(certs_path, f"tls{i}.crt")
    key_path = os.path.join(certs_path, f"tls{i}.key")
    https_config = tenant_nginx_entrypoint.replace_keys(tenant_nginx_entrypoint.DOMAIN_CONF_TEMPLATE, {
        "__SERVER_NAME__": name,
        "__CERT_PATH__": cert_path,
        "__KEY_PATH__": key_path,
        "__TLS_PROTOCOLS__": tls_protocols,
        "__TENANT_NAME__": tenant_name,
        "__SERVER_NGINX_CONFIG__": "",
        "__LOCATION_NGINX_CONFIG__": "",
        "__CDN_CACHE_ROUTER__": tenant_nginx_entrypoint.CDN_CACHE_ROUTER,
        "__ACCESS_LOG_CONFIG__": access_log_config,
    })
    if redirect:
        http_location_config = "return 308 https://$host$request_uri;"
    else:
        http_location_config = tenant_nginx_entrypoint.replace_keys(tenant_nginx_entrypoint.DOMAIN_HTTP_PROXY_LOCATION_CONFIG_TEMPLATE, {
            "__TENANT_NAME__": tenant_name,
            "__CDN_CACHE_ROUTER__": tenant_nginx_entrypoint.CDN_CACHE_ROUTER,
            "__ACCESS_LOG_CONFIG__": access_log_config,
        }).strip()
    http_config = tenant_nginx_entrypoint.replace_keys(tenant_nginx_entrypoint.DOMAIN_HTTP_CONF_TEMPLATE, {
        "__SERVER_NAME__": name,
        "__ACME_CHALLENGE_ROOT__": tenant_nginx_entrypoint.ACME_CHALLENGE_ROOT,
        "__HTTP_LOCATION_CONFIG__": http_location_config,
    })
    return "\n".join([https_config, http_config])


def test_get_domain_server_config(tmpdir, tenant_nginx_entrypoint):
    tenant_name = "tenant1"
    certs_path = tmpdir
    i, domain = 0, {}
    with pytest.raises(AssertionError, match="NAME must be set in all domain configurations"):
        tenant_nginx_entrypoint.get_domain_server_config(i, domain, certs_path, tenant_name, "access_log off;")
    domain = {
        "NAME": "test.example.com",
        "CERT": "cert1",
        "KEY": "key1",
    }
    server_config = tenant_nginx_entrypoint.get_domain_server_config(i, domain, certs_path, tenant_name, "access_log off;")
    assert_domain_server_config(i, certs_path, "cert1", "key1")
    assert server_config == expected_domain_server_config(tenant_nginx_entrypoint, i, certs_path, "test.example.com", tenant_name)
    assert "proxy_set_header X-Forwarded-Proto http;" in server_config
    assert server_config.count(f"proxy_pass {tenant_nginx_entrypoint.CDN_CACHE_ROUTER};") == 2
    domain = {
        "NAME": "le.example.com",
        "TLS_MODE": "letsencrypt",
        "TLS_MIN_VERSION": "TLSv1.3",
        "TLS_MAX_VERSION": "TLSv1.3",
        "REDIRECT_HTTP_TO_HTTPS": "true",
        "CERT_PATH": "/certs/letsencrypt/0/tls.crt",
        "KEY_PATH": "/certs/letsencrypt/0/tls.key",
    }
    server_config = tenant_nginx_entrypoint.get_domain_server_config(i, domain, certs_path, tenant_name, "access_log off;")
    assert "ssl_protocols TLSv1.3;" in server_config
    assert "return 308 https://$host$request_uri;" in server_config
    assert "/.well-known/acme-challenge/" in server_config
    assert "/certs/letsencrypt/0/tls.crt" in server_config
    with pytest.raises(AssertionError, match="Unsupported TLS minVersion: TLSv1.1"):
        tenant_nginx_entrypoint.get_domain_server_config(i, {"NAME": "old.example.com", "CERT": "c", "KEY": "k", "TLS_MIN_VERSION": "TLSv1.1"}, certs_path, tenant_name, "access_log off;")
    domain["FOO"] = "bar"
    domain["BAR"] = "baz"
    with pytest.raises(AssertionError, match="Unknown domain configuration keys: FOO, BAR"):
        tenant_nginx_entrypoint.get_domain_server_config(i, domain, certs_path, tenant_name, "access_log off;")


def test_get_domains_server_configs(tmpdir, tenant_nginx_entrypoint):
    tenant_name = "tenant1"
    certs_path = os.path.join(tmpdir, "certs")
    domains = [
        {
            "NAME": "test1.example.com",
            "CERT": "cert1",
            "KEY": "key1",
        },
        {
            "NAME": "test2.example.com",
            "CERT": "cert2",
            "KEY": "key2",
        },
    ]
    server_configs = tenant_nginx_entrypoint.get_domains_server_configs(domains, certs_path, tenant_name, "access_log off;")
    assert_domain_server_config(0, certs_path, "cert1", "key1")
    assert_domain_server_config(1, certs_path, "cert2", "key2")
    assert server_configs == [
        tenant_nginx_entrypoint.JSON_ESCAPED_LOG_FORMAT,
        expected_domain_server_config(tenant_nginx_entrypoint, 0, certs_path, "test1.example.com", tenant_name),
        expected_domain_server_config(tenant_nginx_entrypoint, 1, certs_path, "test2.example.com", tenant_name),
    ]


@pytest.mark.parametrize("url,expected_host,expected_scheme", [
    ("http://example.com/path", "example.com", "http"),
    ("https://secure.example.com/anotherpath", "secure.example.com", "https"),
    ("https://example.com", "example.com", "https"),
    ("invalid://example.com", None, None),
    ("ftp://example.com", None, None),
    ("example.com/path", None, None),
    ("", None, None),
    ("http", None, None),
    ("http:", None, None),
    ("http:/", None, None),
    ("http://", None, None),
    ("https://", None, None),
])
def test_get_url_host_scheme(tenant_nginx_entrypoint, url, expected_host, expected_scheme):
    if expected_host is None or expected_scheme is None:
        with pytest.raises(Exception, match=f'invalid URL: {url}'):
            tenant_nginx_entrypoint.get_url_host_scheme(url)
    else:
        host, scheme = tenant_nginx_entrypoint.get_url_host_scheme(url)
        assert host == expected_host
        assert scheme == expected_scheme


def test_get_origin_server_config(tenant_nginx_entrypoint):
    tenant_name = "tenant1"
    with pytest.raises(AssertionError, match="URL must be set in the origin configuration"):
        tenant_nginx_entrypoint.get_origin_server_config([{}], tenant_name)
    origin = {
        "URL": "http://origin.example.com",
    }
    server_config = tenant_nginx_entrypoint.get_origin_server_config([origin], tenant_name)
    lua_origins = tenant_nginx_entrypoint.origins_to_lua([
        tenant_nginx_entrypoint.normalize_origin(origin, 0, 1)
    ])
    assert server_config == tenant_nginx_entrypoint.replace_keys(tenant_nginx_entrypoint.ORIGINS_CONF_TEMPLATE, {
        "__TENANT_NAME__": tenant_name,
        "__ORIGINS_LUA__": lua_origins,
        "__PROXY_NEXT_UPSTREAM_TRIES__": "1",
        "__NGINX_RESOLVER_CONFIG__": "",
        "__LOCATION_NGINX_CONFIG__": "",
        "__SERVER_NGINX_CONFIG__": "",
    })
    origin = {
        "URL": "https://secure-origin.example.com/path",
        "FOO": "bar",
    }
    with pytest.raises(AssertionError, match="Unknown origin configuration keys: FOO"):
        tenant_nginx_entrypoint.get_origin_server_config([origin], tenant_name)


def test_get_origin_server_config_multiple_origins(tenant_nginx_entrypoint):
    origins = [
        {
            "URL": "http://origin-a.example.com:8080",
            "NAME": "origin-a",
            "WEIGHT": "2",
            "HEALTHCHECK_PATH": "/healthz",
            "HEALTHCHECK_EXPECTEDSTATUS": "204",
            "HEALTHCHECK_INTERVAL": "5s",
            "HEALTHCHECK_TIMEOUT": "500ms",
            "HEALTHCHECK_HEALTHYTHRESHOLD": "1",
            "HEALTHCHECK_UNHEALTHYTHRESHOLD": "2",
        },
        {
            "URL": "https://origin-b.example.com",
            "NAME": "origin-b",
            "WEIGHT": "1",
            "HEALTHCHECK_ENABLED": "false",
        },
    ]
    server_config = tenant_nginx_entrypoint.get_origin_server_config(origins, "tenant1")
    assert "lua_shared_dict origin_health" in server_config
    assert "balancer_by_lua_block" in server_config
    assert "server 0.0.0.1 max_fails=1 fail_timeout=10s;" in server_config
    assert "proxy_next_upstream error timeout http_500 http_502 http_503 http_504;" in server_config
    assert 'name="origin-a"' in server_config
    assert 'host="origin-a.example.com"' in server_config
    assert 'host_header="origin-a.example.com:8080"' in server_config
    assert 'weight=2' in server_config
    assert 'path="/healthz"' in server_config
    assert 'expected_status=204' in server_config
    assert 'timeout=0.5' in server_config
    assert 'name="origin-b"' in server_config
    assert 'enabled=false' in server_config


def test_get_origin_server_config_preserves_single_origin_path_prefix(tenant_nginx_entrypoint):
    server_config = tenant_nginx_entrypoint.get_origin_server_config([
        {"URL": "https://origin.example.com/base"},
    ], "tenant1")
    assert 'request_uri_prefix="/base"' in server_config
    assert "proxy_pass https://tenant_origin_upstream$origin_request_uri;" in server_config


def test_get_origin_server_config_rejects_path_prefixed_multi_origin(tenant_nginx_entrypoint):
    with pytest.raises(AssertionError, match="Path-prefixed origin URLs are not supported with multiple origins"):
        tenant_nginx_entrypoint.get_origin_server_config([
            {"URL": "https://origin-a.example.com/path"},
            {"URL": "https://origin-b.example.com"},
        ], "tenant1")


def test_normalize_origin_defaults(tenant_nginx_entrypoint):
    origin = tenant_nginx_entrypoint.normalize_origin({"URL": "https://origin.example.com"}, 3, 1)
    assert origin["name"] == "origin-3"
    assert origin["weight"] == 1
    assert origin["health"] == {
        "enabled": True,
        "path": "/",
        "expected_status": 200,
        "interval": 10,
        "timeout": 2,
        "healthy_threshold": 2,
        "unhealthy_threshold": 3,
    }


def assert_test_default_conf(tenant_nginx_entrypoint, default_conf, certs_path, access_log_config='access_log off;'):
    assert_domain_server_config(0, certs_path, TEST_DOMAIN0["D0_CERT"], TEST_DOMAIN0["D0_KEY"])
    lua_origins = tenant_nginx_entrypoint.origins_to_lua([
        tenant_nginx_entrypoint.normalize_origin({"URL": TEST_ORIGIN0["O0_URL"]}, 0, 1)
    ])
    assert default_conf == "\n".join([
        tenant_nginx_entrypoint.HTTP_HASH_CONFIG,
        tenant_nginx_entrypoint.JSON_ESCAPED_LOG_FORMAT,
        expected_domain_server_config(tenant_nginx_entrypoint, 0, certs_path, TEST_DOMAIN0["D0_NAME"], TEST_TENANT_NAME, access_log_config),
        tenant_nginx_entrypoint.replace_keys(tenant_nginx_entrypoint.ORIGINS_CONF_TEMPLATE, {
            "__TENANT_NAME__": TEST_TENANT_NAME,
            "__ORIGINS_LUA__": lua_origins,
            "__PROXY_NEXT_UPSTREAM_TRIES__": "1",
            "__NGINX_RESOLVER_CONFIG__": "",
            "__LOCATION_NGINX_CONFIG__": "",
            "__SERVER_NGINX_CONFIG__": "",
        }),
        tenant_nginx_entrypoint.get_metrics_server_config(),
    ])


def test_get_default_conf(tenant_nginx_entrypoint, tmpdir):
    certs_path = os.path.join(tmpdir, "certs")
    tenant_name = "tenant1"
    env = {
        "TENANT_NAME": tenant_name,
        "WORKER_PROCESSES": "2",
        "PATH": "/usr/sbin:/bin",
    }
    with pytest.raises(Exception, match="At least one domain configuration is required"):
        tenant_nginx_entrypoint.get_default_conf(certs_path, env)
    env.update(TEST_DOMAIN0)
    with pytest.raises(Exception, match="At least one origin configuration is required"):
        tenant_nginx_entrypoint.get_default_conf(certs_path, env)
    env.update({
        **TEST_ORIGIN0,
        "O1_URL": "http://another-origin.example.com/path-prefix",
    })
    with pytest.raises(Exception, match="Path-prefixed origin URLs are not supported with multiple origins"):
        tenant_nginx_entrypoint.get_default_conf(certs_path, env)
    env["O1_URL"] = "http://another-origin.example.com"
    tenant_nginx_entrypoint.get_default_conf(certs_path, env)
    env.pop("O1_URL")
    default_conf = tenant_nginx_entrypoint.get_default_conf(certs_path, env)
    assert_test_default_conf(tenant_nginx_entrypoint, default_conf, certs_path)
    assert "server_names_hash_bucket_size 128;" in default_conf
    assert "server_names_hash_max_size 4096;" in default_conf


@pytest.mark.parametrize("extraenv,assertkwargs", [
    ({},{}),
    ({
        "ENABLE_TENANT_ACCESS_LOGS": "true",
    }, {
        "access_log_config": 'access_log /var/log/nginx/access.logjson json_escaped;'
    })
])
def test_main(tenant_nginx_entrypoint, tmpdir, extraenv, assertkwargs):
    nginx_path = os.path.join(tmpdir, 'nginx')
    os.makedirs(os.path.join(nginx_path, 'conf.d'), exist_ok=True)
    certs_path = os.path.join(tmpdir, 'certs')
    tenant_nginx_entrypoint.main(nginx_path, certs_path, {**TEST_TENANT, **TEST_DOMAIN0, **TEST_ORIGIN0, **extraenv})
    with open(os.path.join(nginx_path, "conf.d/default.conf")) as f:
        default_conf = f.read()
    assert_test_default_conf(tenant_nginx_entrypoint, default_conf, certs_path, **assertkwargs)


def assert_curl_issuer(hostname, expected_output, expected_issuer, *args):
    docker_host_addr = os.getenv("E2E_DOCKER_HOST_ADDR", "127.0.0.1")
    p = subprocess.Popen([
        "curl", "-kv",
        "--resolve", f"{hostname}:58443:{docker_host_addr}",
        "-H", f"Host: {hostname}",
        f"https://{hostname}:58443",
        *args
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert p.wait() == 0
    assert p.stdout.read().decode().strip() == expected_output
    issuer = None
    for line in p.stderr.read().decode().splitlines():
        if line.startswith('*  issuer: CN='):
            issuer = line.strip().split('=')[1]
    assert issuer == expected_issuer


@pytest.mark.skipif(os.getenv("E2E") != "yes", reason="Set E2E=yes to run E2E tests")
@pytest.mark.parametrize("testconf", [
    {},
    {
        "access_logs": True
    },
    {
        "access_logs": True,
        "elasticsearch": True,
    },
])
def test_e2e(testconf):
    try:
        dynamic_env = {}
        if testconf.get("access_logs"):
            dynamic_env["ENABLE_TENANT_ACCESS_LOGS"] = "true"
            if testconf.get("elasticsearch"):
                dynamic_env["ENABLE_ES_SINK"] = "true"
                dynamic_env["ES_ENDPOINTS"] = "[\\\"http://elasticsearch:9200\\\"]"
        with open(os.path.join(os.path.dirname(__file__), "test_tenant_nginx_dynamic.env"), "w") as f:
            for k, v in dynamic_env.items():
                f.write(f'{k}="{v}"\n')
            f.write("\n")
        cmd = [
            "docker", "compose", "-f", "test_tenant_nginx_compose.yaml",
            "up", "--wait", "--yes", "--build", "--force-recreate", "--remove-orphans", "tenant-nginx",
        ]
        if testconf.get("elasticsearch"):
            cmd.append("elasticsearch")
        subprocess.check_call(cmd, cwd=os.path.join(os.path.dirname(__file__)))
        time.sleep(5)
        assert_curl_issuer("test1.example.com", "cache-router", "test1.example.com")
        assert_curl_issuer("test2.aaa.bbb", "cache-router", "test2.aaa.bbb")
        assert_curl_issuer("test1.example.com", "origin", "test1.example.com", "-X", "POST")
        assert_curl_issuer("test2.aaa.bbb", "origin", "test2.aaa.bbb", "-X", "DELETE")
        docker_host_addr = os.getenv("E2E_DOCKER_HOST_ADDR", "localhost")
        assert subprocess.getstatusoutput(f"curl -s http://{docker_host_addr}:58080") == (0, "origin")
        assert subprocess.getstatusoutput(f"curl -s http://{docker_host_addr}:58080 -X POST") == (0, "origin")
        if testconf.get("access_logs"):
            got_it = False
            for i in range(60):
                time.sleep(1)
                if testconf.get("elasticsearch"):
                    out = None
                    try:
                        out = subprocess.check_output([
                            "docker", "compose", "-f", "test_tenant_nginx_compose.yaml", "exec", "elasticsearch", "curl", "localhost:9200/_search?index=vector-*",
                        ], text=True, cwd=os.path.join(os.path.dirname(__file__)))
                        res = json.loads(out)
                    except:
                        print(out)
                        res = None
                    if res:
                        try:
                            hits = res["hits"]["hits"]
                        except:
                            hits = []
                        if len(hits) >= 4:
                            lines = [hit["_source"] for hit in hits]
                            got_it = True
                            break
                else:
                    lines = subprocess.check_output([
                        "docker", "compose", "-f", "test_tenant_nginx_compose.yaml", "logs", "--tail=4", "--no-log-prefix", "tenant-nginx"
                    ], text=True, cwd=os.path.join(os.path.dirname(__file__)))
                    got_it = True
                    for line in lines.splitlines():
                        if not line.startswith("{"):
                            got_it = False
                    if got_it:
                        lines = [json.loads(line) for line in lines.splitlines()]
                        break
            assert got_it
            assert [line["request"] for line in lines] == [
                "GET / HTTP/1.1",
                "GET / HTTP/1.1",
                "POST / HTTP/1.1",
                "DELETE / HTTP/1.1",
            ]
    except:
        subprocess.call([
            "docker", "compose", "-f", "test_tenant_nginx_compose.yaml", "logs"
        ], cwd=os.path.join(os.path.dirname(__file__)))
        raise
    finally:
        subprocess.call([
            "docker", "compose", "-f", "test_tenant_nginx_compose.yaml", "down", "-v"
        ], cwd=os.path.join(os.path.dirname(__file__)))
