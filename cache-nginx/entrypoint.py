#!/usr/bin/python3
import os


DEFAULT_TENANT_PROXY_PASS_DOMAIN = 'tenant.$http_x_cwmcdn_tenant_name.svc.cluster.local'
DEFAULT_NGINX_RESOLVER_CONFIG = '''
resolver 169.254.20.10 valid=30s;
resolver_timeout 5s;
'''

DEFAULT_CONF_ROUTER_TEMPLATE = '''
include /etc/nginx/metrics.conf;

upstream cache {
  hash $http_x_cwmcdn_tenant_name$request_uri consistent;
__NGINX_UPSTREAM_CACHE_SERVERS__
}

__NGINX_HTTP_CONFIGS__

server {
    listen       80;
    server_name  _;
    __NGINX_SERVER_CONFIGS__
    location / {
        proxy_pass http://cache$request_uri;
        __NGINX_LOCATION_CONFIGS__
    }
}
'''

DEFAULT_CONF_CACHE_TEMPLATE = '''
include /etc/nginx/metrics.conf;

__NGINX_RESOLVER_CONFIG__

__NGINX_HTTP_CONFIGS__

server {
    listen       80;
    server_name  _;
    __NGINX_SERVER_CONFIGS__
    location / {
        proxy_pass http://__TENANT_PROXY_PASS_DOMAIN__$request_uri;
        __NGINX_LOCATION_CONFIGS__
    }
}
'''


def replace_keys(base, d):
    out = base
    for k, v in d.items():
        out = out.replace(k, v)
    return out


def get_common_replace_keys(env):
    return {
        "__NGINX_HTTP_CONFIGS__": env.get('NGINX_HTTP_CONFIGS') or "",
        "__NGINX_SERVER_CONFIGS__": env.get('NGINX_SERVER_CONFIGS') or "",
        "__NGINX_LOCATION_CONFIGS__": env.get('NGINX_LOCATION_CONFIGS') or "",
    }


def get_router_default_conf(env):
    return replace_keys(DEFAULT_CONF_ROUTER_TEMPLATE, {
        **get_common_replace_keys(env),
        "__NGINX_UPSTREAM_CACHE_SERVERS__": env['NGINX_UPSTREAM_CACHE_SERVERS']
    })


def get_cache_default_conf(env):
    return replace_keys(DEFAULT_CONF_CACHE_TEMPLATE, {
        **get_common_replace_keys(env),
        "__NGINX_RESOLVER_CONFIG__": env.get('NGINX_RESOLVER_CONFIG') or DEFAULT_NGINX_RESOLVER_CONFIG,
        "__TENANT_PROXY_PASS_DOMAIN__": env.get('TENANT_PROXY_PASS_DOMAIN') or DEFAULT_TENANT_PROXY_PASS_DOMAIN,
    })


def get_default_conf(env):
    if env['TYPE'] == 'router':
        return get_router_default_conf(env)
    elif env['TYPE'] == 'cache':
        return get_cache_default_conf(env)
    else:
        raise Exception(f'Unknown TYPE: {env["TYPE"]}')


def main(nginx_conf_path="/etc/nginx", env=None):
    with open(os.path.join(nginx_conf_path, "conf.d/default.conf"), "w") as f:
        f.write(get_default_conf(env or os.environ))


if __name__ == "__main__":
    main()
