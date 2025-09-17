#!/usr/bin/python3
import os


TYPE = os.environ.get("TYPE", "router")  # router or cache
NGINX_HTTP_CONFIGS = os.environ.get("NGINX_HTTP_CONFIGS", "")
NGINX_UPSTREAM_CACHE_SERVERS = os.environ.get("NGINX_UPSTREAM_CACHE_SERVERS", "")


DEFAULT_CONF_ROUTER_TEMPLATE = '''
upstream cache {
  hash $http_x_cwmcdn_tenant_name$request_uri consistent;
__NGINX_UPSTREAM_CACHE_SERVERS__
}

__NGINX_HTTP_CONFIGS__

server {
    listen       80;
    server_name  _;
    location / {
        proxy_pass http://cache$request_uri;
    }
}
'''

DEFAULT_CONF_CACHE_TEMPLATE = '''
resolver 169.254.20.10 valid=30s;
resolver_timeout 5s;

__NGINX_HTTP_CONFIGS__

server {
    listen       80;
    server_name  _;
    location / {
        proxy_pass http://tenant.$http_x_cwmcdn_tenant_name.svc.cluster.local$request_uri;
        proxy_cache_key "$http_x_cwmcdn_tenant_name$request_uri";
        proxy_cache cwm;
    }
}
'''


def main():
    default_conf = DEFAULT_CONF_ROUTER_TEMPLATE if TYPE == "router" else DEFAULT_CONF_CACHE_TEMPLATE
    default_conf = default_conf.replace("__NGINX_HTTP_CONFIGS__", NGINX_HTTP_CONFIGS)
    if TYPE == "router":
        default_conf = default_conf.replace("__NGINX_UPSTREAM_CACHE_SERVERS__", NGINX_UPSTREAM_CACHE_SERVERS)
    with open("/etc/nginx/conf.d/default.conf", "w") as f:
        f.write(default_conf)


if __name__ == "__main__":
    main()
