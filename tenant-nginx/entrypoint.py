#!/usr/bin/python3
import os
import re
from copy import deepcopy


CDN_CACHE_ROUTER = os.getenv("CDN_CACHE_ROUTER", "http://router.cdn-cache")


DOMAIN_CONF_TEMPLATE = '''
server {
    listen 443 ssl;
    server_name  __SERVER_NAME__;
    ssl_certificate /certs/tls__DOMAIN_NUMBER__.crt;
    ssl_certificate_key /certs/tls__DOMAIN_NUMBER__.key;
    __SERVER_NGINX_CONFIG__
    location / {
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


ORIGINS_CONF_TEMPLATE = '''
server {
    listen 80;
    server_name  _;
    __SERVER_NGINX_CONFIG__
    location / {
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

CONFIG_PARSE_REGEX = re.compile(r'^([A-Z])(\d+)_(.+)$')


def replace_keys(base, d):
    out = base
    for k, v in d.items():
        out = out.replace(k, v)
    return out


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


def get_domain_server_config(i, domain, certs_path, tenant_name):
    domain = deepcopy(domain)
    assert "NAME" in domain and "CERT" in domain and "KEY" in domain, "NAME, CERT and KEY must be set in all domain configurations"
    name, cert, key = domain.pop("NAME"), domain.pop("CERT"), domain.pop("KEY")
    server_nginx_config = ""
    location_nginx_config = ""
    # TODO: pop other configs here and add to server/location nginx configs
    assert len(domain) == 0, f"Unknown domain configuration keys: {', '.join(domain.keys())}"
    with open(os.path.join(certs_path, f"tls{i}.crt"), "w") as f:
        f.write(cert)
    os.chmod(os.path.join(certs_path, f"tls{i}.crt"), 0o600)
    with open(os.path.join(certs_path, f"tls{i}.key"), "w") as f:
        f.write(key)
    os.chmod(os.path.join(certs_path, f"tls{i}.key"), 0o600)
    server_config = DOMAIN_CONF_TEMPLATE
    server_config = replace_keys(server_config, {
        "__SERVER_NAME__": name,
        "__DOMAIN_NUMBER__": str(i),
        "__TENANT_NAME__": tenant_name,
        "__SERVER_NGINX_CONFIG__": server_nginx_config,
        "__LOCATION_NGINX_CONFIG__": location_nginx_config,
        "__CDN_CACHE_ROUTER__": CDN_CACHE_ROUTER,
    })
    return server_config


def get_domains_server_configs(domains, certs_path, tenant_name):
    server_configs = []
    os.makedirs(certs_path, exist_ok=True)
    for i, domain in enumerate(domains):
        server_configs.append(get_domain_server_config(i, domain, certs_path, tenant_name))
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
    domains, origins = parse_configs(env)
    assert len(domains) > 0, "At least one domain configuration is required"
    assert len(origins) == 1, "Exactly one origin configuration is required"
    return "\n".join([
        *get_domains_server_configs(domains, certs_path, tenant_name),
        get_origin_server_config(origins[0], tenant_name),
        get_metrics_server_config()
    ])


def main(nginx_conf_path="/etc/nginx", certs_path="/certs", env=None):
    with open(os.path.join(nginx_conf_path, "conf.d/default.conf"), "w") as f:
        f.write(get_default_conf(certs_path, env or os.environ))


if __name__ == "__main__":
    main()
