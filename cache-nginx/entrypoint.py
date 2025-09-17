#!/usr/bin/python3
import os


TYPE = os.environ.get("TYPE", "router")  # router or cache
NUM_CACHE_SERVERS = int(os.environ.get("NUM_CACHE_SERVERS", "3"))
if TYPE == "cache":
    NGINX_HTTP_CONFIGS = os.environ.get("NGINX_HTTP_CONFIGS", "proxy_cache_path /var/cache levels=1:2:3 keys_zone=cwm:1g inactive=5d use_temp_path=off;")
else:
    NGINX_HTTP_CONFIGS = os.environ.get("NGINX_HTTP_CONFIGS", "")


DEFAULT_CONF_ROUTER_TEMPLATE = '''
upstream cache {
  hash $http_x_cwmcdn_tenant_name$request_uri consistent;
__SERVERS__
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
    }
}
'''


def main():
    if not TYPE or not NUM_CACHE_SERVERS:
        raise Exception("TYPE, NUM_CACHE_SERVERS environment variables must be set")
    default_conf = DEFAULT_CONF_ROUTER_TEMPLATE
    default_conf = default_conf.replace("__NGINX_HTTP_CONFIGS__", NGINX_HTTP_CONFIGS)
    if TYPE == "router":
        servers_conf = []
        for i in range(1, NUM_CACHE_SERVERS + 1):
            servers_conf.append(f'  server cache{i}:80;')
        default_conf = default_conf.replace("__SERVERS__", "\n".join(servers_conf))
    elif TYPE == "cache":
        pass
    else:
        raise Exception("TYPE must be 'router' or 'cache'")
    with open("/etc/nginx/conf.d/default.conf", "w") as f:
        f.write(default_conf)


if __name__ == "__main__":
    main()
