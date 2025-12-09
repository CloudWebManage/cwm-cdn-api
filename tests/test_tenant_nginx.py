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


def test_get_domain_server_config(tmpdir, tenant_nginx_entrypoint):
    tenant_name = "tenant1"
    certs_path = tmpdir
    i, domain = 0, {}
    with pytest.raises(AssertionError, match="NAME, CERT and KEY must be set in all domain configurations"):
        tenant_nginx_entrypoint.get_domain_server_config(i, domain, certs_path, tenant_name, "access_log off;")
    domain = {
        "NAME": "test.example.com",
        "CERT": "cert1",
        "KEY": "key1",
    }
    server_config = tenant_nginx_entrypoint.get_domain_server_config(i, domain, certs_path, tenant_name, "access_log off;")
    assert_domain_server_config(i, certs_path, "cert1", "key1")
    assert server_config == tenant_nginx_entrypoint.replace_keys(tenant_nginx_entrypoint.DOMAIN_CONF_TEMPLATE, {
        "__SERVER_NAME__": "test.example.com",
        "__DOMAIN_NUMBER__": "0",
        "__TENANT_NAME__": tenant_name,
        "__SERVER_NGINX_CONFIG__": "",
        "__LOCATION_NGINX_CONFIG__": "",
        "__CDN_CACHE_ROUTER__": tenant_nginx_entrypoint.CDN_CACHE_ROUTER,
        "__ACCESS_LOG_CONFIG__": "access_log off;",
    })
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
        tenant_nginx_entrypoint.replace_keys(tenant_nginx_entrypoint.DOMAIN_CONF_TEMPLATE, {
            "__SERVER_NAME__": "test1.example.com",
            "__DOMAIN_NUMBER__": "0",
            "__TENANT_NAME__": tenant_name,
            "__SERVER_NGINX_CONFIG__": "",
            "__LOCATION_NGINX_CONFIG__": "",
            "__CDN_CACHE_ROUTER__": tenant_nginx_entrypoint.CDN_CACHE_ROUTER,
            "__ACCESS_LOG_CONFIG__": "access_log off;",
        }),
        tenant_nginx_entrypoint.replace_keys(tenant_nginx_entrypoint.DOMAIN_CONF_TEMPLATE, {
            "__SERVER_NAME__": "test2.example.com",
            "__DOMAIN_NUMBER__": "1",
            "__TENANT_NAME__": tenant_name,
            "__SERVER_NGINX_CONFIG__": "",
            "__LOCATION_NGINX_CONFIG__": "",
            "__CDN_CACHE_ROUTER__": tenant_nginx_entrypoint.CDN_CACHE_ROUTER,
            "__ACCESS_LOG_CONFIG__": "access_log off;",
        })
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
        tenant_nginx_entrypoint.get_origin_server_config({}, tenant_name)
    origin = {
        "URL": "http://origin.example.com",
    }
    server_config = tenant_nginx_entrypoint.get_origin_server_config(origin, tenant_name)
    assert server_config == tenant_nginx_entrypoint.replace_keys(tenant_nginx_entrypoint.ORIGINS_CONF_TEMPLATE, {
        "__TENANT_NAME__": tenant_name,
        "__ORIGIN_URL__": "http://origin.example.com",
        "__ORIGIN_URL_HOST__": "origin.example.com",
        "__ORIGIN_URL_SCHEME__": "http",
        "__LOCATION_NGINX_CONFIG__": "",
        "__SERVER_NGINX_CONFIG__": "",
    })
    origin = {
        "URL": "https://secure-origin.example.com/path",
        "FOO": "bar",
    }
    with pytest.raises(AssertionError, match="Unknown origin configuration keys: FOO"):
        tenant_nginx_entrypoint.get_origin_server_config(origin, tenant_name)


def assert_test_default_conf(tenant_nginx_entrypoint, default_conf, certs_path, access_log_config='access_log off;'):
    assert_domain_server_config(0, certs_path, TEST_DOMAIN0["D0_CERT"], TEST_DOMAIN0["D0_KEY"])
    host, scheme = tenant_nginx_entrypoint.get_url_host_scheme(TEST_ORIGIN0["O0_URL"])
    assert default_conf == "\n".join([
        tenant_nginx_entrypoint.JSON_ESCAPED_LOG_FORMAT,
        tenant_nginx_entrypoint.replace_keys(tenant_nginx_entrypoint.DOMAIN_CONF_TEMPLATE, {
            "__SERVER_NAME__": TEST_DOMAIN0["D0_NAME"],
            "__DOMAIN_NUMBER__": "0",
            "__TENANT_NAME__": TEST_TENANT_NAME,
            "__SERVER_NGINX_CONFIG__": "",
            "__LOCATION_NGINX_CONFIG__": "",
            "__CDN_CACHE_ROUTER__": tenant_nginx_entrypoint.CDN_CACHE_ROUTER,
            "__ACCESS_LOG_CONFIG__": access_log_config,
        }),
        tenant_nginx_entrypoint.replace_keys(tenant_nginx_entrypoint.ORIGINS_CONF_TEMPLATE, {
            "__TENANT_NAME__": TEST_TENANT_NAME,
            "__ORIGIN_URL__": TEST_ORIGIN0["O0_URL"],
            "__ORIGIN_URL_HOST__": host,
            "__ORIGIN_URL_SCHEME__": scheme,
            "__LOCATION_NGINX_CONFIG__": "",
            "__SERVER_NGINX_CONFIG__": "",
        }),
        "include /etc/nginx/metrics.conf;",
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
    with pytest.raises(Exception, match="Exactly one origin configuration is required"):
        tenant_nginx_entrypoint.get_default_conf(certs_path, env)
    env.update({
        **TEST_ORIGIN0,
        "O1_URL": "http://another-origin.example.com",
    })
    with pytest.raises(Exception, match="Exactly one origin configuration is required"):
        tenant_nginx_entrypoint.get_default_conf(certs_path, env)
    env.pop("O1_URL")
    default_conf = tenant_nginx_entrypoint.get_default_conf(certs_path, env)
    assert_test_default_conf(tenant_nginx_entrypoint, default_conf, certs_path)


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
    p = subprocess.Popen([
        "curl", "-kv",
        "--resolve", f"{hostname}:58443:127.0.0.1",
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
        assert subprocess.getstatusoutput("curl -s http://localhost:58080") == (0, "origin")
        assert subprocess.getstatusoutput("curl -s http://localhost:58080 -X POST") == (0, "origin")
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
