#!/usr/bin/python3
import os
import re
from copy import deepcopy


CDN_CACHE_ROUTER = os.getenv("CDN_CACHE_ROUTER", "http://router.cdn-cache")
ACME_CHALLENGE_ROOT = os.getenv("ACME_CHALLENGE_ROOT", "/var/lib/cwm-cdn/acme-challenges")
TLS_VERSIONS = ("TLSv1.2", "TLSv1.3")


DOMAIN_CONF_TEMPLATE = '''
server {
    listen 443 ssl;
    server_name  __SERVER_NAME__;
    ssl_certificate __CERT_PATH__;
    ssl_certificate_key __KEY_PATH__;
    ssl_protocols __TLS_PROTOCOLS__;
    __SERVER_NGINX_CONFIG__
    location / {
        __ACCESS_LOG_CONFIG__
        proxy_pass __CDN_CACHE_ROUTER__;
        proxy_set_header X-CWMCDN-Tenant-Name __TENANT_NAME__;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        __LOCATION_NGINX_CONFIG__
        
        if ($request_method !~ ^(GET|HEAD)$ ) {
            proxy_pass http://127.0.0.1:80;
        }
    }
}
'''


DOMAIN_HTTP_CONF_TEMPLATE = '''
server {
    listen 80;
    server_name  __SERVER_NAME__;
    location ^~ /.well-known/acme-challenge/ {
        access_log off;
        root __ACME_CHALLENGE_ROOT__;
        try_files $uri =404;
    }
    location / {
        __HTTP_LOCATION_CONFIG__
    }
}
'''


DOMAIN_HTTP_PROXY_LOCATION_CONFIG_TEMPLATE = '''
        __ACCESS_LOG_CONFIG__
        proxy_pass __CDN_CACHE_ROUTER__;
        proxy_set_header X-CWMCDN-Tenant-Name __TENANT_NAME__;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto http;
'''


ORIGINS_CONF_TEMPLATE = '''
server {
    listen 80 default_server;
    server_name  _;
    __SERVER_NGINX_CONFIG__
    location / {
        access_log off;
        proxy_pass __ORIGIN_URL__;
        proxy_set_header Host __ORIGIN_URL_HOST__;
        proxy_set_header X-Forwarded-Proto __ORIGIN_URL_SCHEME__;
        proxy_ssl_server_name on;
        
        set_real_ip_from  172.0.0.0/8;
        set_real_ip_from  10.0.0.0/8;
        real_ip_header    X-Forwarded-For;
        real_ip_recursive on;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        
        __LOCATION_NGINX_CONFIG__
    }
}
'''

JSON_ESCAPED_LOG_FORMAT = '''
log_format json_escaped escape=json
  '{'
    '"time_local":"$time_local",'
    '"remote_addr":"$remote_addr",'
    '"request":"$request",'
    '"status":$status,'
    '"body_bytes_sent":$body_bytes_sent,'
    '"http_referer":"$http_referer",'
    '"http_user_agent":"$http_user_agent",'
    '"request_time":$request_time,'
    '"upstream_addr":"$upstream_addr",'
    '"upstream_status":"$upstream_status",'
    '"upstream_response_time":"$upstream_response_time"'
  '}';
'''


HTTP_HASH_CONFIG = '''
server_names_hash_bucket_size 128;
server_names_hash_max_size 4096;
'''

CONFIG_PARSE_REGEX = re.compile(r'^([A-Z])(\d+)_(.+)$')


def replace_keys(base, d):
    out = base
    for k, v in d.items():
        out = out.replace(k, v)
    return out


def parse_bool(value):
    return str(value).lower() in ("1", "true", "yes", "on")


def tls_protocols(min_version, max_version):
    assert min_version in TLS_VERSIONS, f"Unsupported TLS minVersion: {min_version}"
    assert max_version in TLS_VERSIONS, f"Unsupported TLS maxVersion: {max_version}"
    assert TLS_VERSIONS.index(min_version) <= TLS_VERSIONS.index(max_version), "TLS minVersion cannot be greater than maxVersion"
    return " ".join(TLS_VERSIONS[TLS_VERSIONS.index(min_version):TLS_VERSIONS.index(max_version) + 1])


def parse_configs(env):
    domains, origins = {}, {}
    for k, v in env.items():
        match = re.match(CONFIG_PARSE_REGEX, k)
        if match:
            d = None
            if match.group(1) == "D":
                d = domains
            elif match.group(1) == "O":
                d = origins
            if d is not None:
                d.setdefault(match.group(2), {})[match.group(3).upper()] = v
    return list(domains.values()), list(origins.values())


def get_domain_server_config(i, domain, certs_path, tenant_name, access_log_config):
    domain = deepcopy(domain)
    assert "NAME" in domain, "NAME must be set in all domain configurations"
    name = domain.pop("NAME")
    tls_mode = domain.pop("TLS_MODE", "provided")
    tls_min_version = domain.pop("TLS_MIN_VERSION", "TLSv1.2")
    tls_max_version = domain.pop("TLS_MAX_VERSION", "TLSv1.3")
    redirect_http_to_https = parse_bool(domain.pop("REDIRECT_HTTP_TO_HTTPS", "false"))
    assert tls_mode in ("provided", "letsencrypt"), f"Unsupported TLS mode: {tls_mode}"
    protocols = tls_protocols(tls_min_version, tls_max_version)
    if tls_mode == "provided":
        assert "CERT" in domain and "KEY" in domain, "CERT and KEY must be set for provided TLS domain configurations"
        cert, key = domain.pop("CERT"), domain.pop("KEY")
        cert_path = os.path.join(certs_path, f"tls{i}.crt")
        key_path = os.path.join(certs_path, f"tls{i}.key")
        with open(cert_path, "w") as f:
            f.write(cert)
        os.chmod(cert_path, 0o600)
        with open(key_path, "w") as f:
            f.write(key)
        os.chmod(key_path, 0o600)
    else:
        cert_path = domain.pop("CERT_PATH", os.path.join(certs_path, "letsencrypt", str(i), "tls.crt"))
        key_path = domain.pop("KEY_PATH", os.path.join(certs_path, "letsencrypt", str(i), "tls.key"))
    server_nginx_config = ""
    location_nginx_config = ""
    # TODO: pop other configs here and add to server/location nginx configs
    assert len(domain) == 0, f"Unknown domain configuration keys: {', '.join(domain.keys())}"
    server_config = DOMAIN_CONF_TEMPLATE
    server_config = replace_keys(server_config, {
        "__SERVER_NAME__": name,
        "__CERT_PATH__": cert_path,
        "__KEY_PATH__": key_path,
        "__TLS_PROTOCOLS__": protocols,
        "__TENANT_NAME__": tenant_name,
        "__SERVER_NGINX_CONFIG__": server_nginx_config,
        "__LOCATION_NGINX_CONFIG__": location_nginx_config,
        "__CDN_CACHE_ROUTER__": CDN_CACHE_ROUTER,
        "__ACCESS_LOG_CONFIG__": access_log_config if access_log_config else '',
    })
    if redirect_http_to_https:
        http_location_config = "return 308 https://$host$request_uri;"
    else:
        http_location_config = replace_keys(DOMAIN_HTTP_PROXY_LOCATION_CONFIG_TEMPLATE, {
            "__TENANT_NAME__": tenant_name,
            "__CDN_CACHE_ROUTER__": CDN_CACHE_ROUTER,
            "__ACCESS_LOG_CONFIG__": access_log_config if access_log_config else '',
        }).strip()
    http_server_config = replace_keys(DOMAIN_HTTP_CONF_TEMPLATE, {
        "__SERVER_NAME__": name,
        "__ACME_CHALLENGE_ROOT__": ACME_CHALLENGE_ROOT,
        "__HTTP_LOCATION_CONFIG__": http_location_config,
    })
    return "\n".join([server_config, http_server_config])


def get_domains_server_configs(domains, certs_path, tenant_name, access_log_config):
    server_configs = [JSON_ESCAPED_LOG_FORMAT]
    os.makedirs(certs_path, exist_ok=True)
    for i, domain in enumerate(domains):
        server_configs.append(get_domain_server_config(i, domain, certs_path, tenant_name, access_log_config))
    return server_configs


def get_url_host_scheme(url):
    assert url.startswith('http'), f'invalid URL: {url}'
    try:
        host, scheme = url.split("://", 1)[1].split("/", 1)[0], ("https" if url.startswith("https") else "http")
    except Exception as e:
        raise Exception(f'invalid URL: {url}') from e
    assert host, f'invalid URL: {url}'
    return host, scheme

def get_origin_server_config(origin, tenant_name):
    assert "URL" in origin, "URL must be set in the origin configuration"
    url = origin.pop("URL")
    location_nginx_config = ""
    server_nginx_config = ""
    # TODO: pop other configs here and add to server/location nginx configs
    assert len(origin) == 0, f"Unknown origin configuration keys: {', '.join(origin.keys())}"
    server_config = ORIGINS_CONF_TEMPLATE
    host, scheme = get_url_host_scheme(url)
    server_config = replace_keys(server_config, {
        "__TENANT_NAME__": tenant_name,
        "__ORIGIN_URL__": url,
        "__ORIGIN_URL_HOST__": host,
        "__ORIGIN_URL_SCHEME__": scheme,
        "__LOCATION_NGINX_CONFIG__": location_nginx_config,
        "__SERVER_NGINX_CONFIG__": server_nginx_config,
    })
    return server_config


def get_metrics_server_config():
    return 'include /etc/nginx/metrics.conf;'


def get_default_conf(certs_path, env):
    tenant_name = env["TENANT_NAME"]
    domain_access_log_path = "/var/log/nginx/access.logjson" if env.get("ENABLE_TENANT_ACCESS_LOGS") in ("1", "true", "yes") else ""
    if domain_access_log_path:
        domain_access_log_config = f'access_log {domain_access_log_path} json_escaped;'
    else:
        domain_access_log_config = 'access_log off;'
    domains, origins = parse_configs(env)
    assert len(domains) > 0, "At least one domain configuration is required"
    assert len(origins) == 1, "Exactly one origin configuration is required"
    return "\n".join([
        HTTP_HASH_CONFIG,
        *get_domains_server_configs(domains, certs_path, tenant_name, domain_access_log_config),
        get_origin_server_config(origins[0], tenant_name),
        get_metrics_server_config()
    ])


def main(nginx_conf_path="/etc/nginx", certs_path="/certs", env=None):
    with open(os.path.join(nginx_conf_path, "conf.d/default.conf"), "w") as f:
        f.write(get_default_conf(certs_path, env or os.environ))


if __name__ == "__main__":
    main()
