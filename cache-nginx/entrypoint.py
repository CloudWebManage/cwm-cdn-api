#!/usr/bin/python3
import os


TYPE = os.environ.get("TYPE", "router")  # router or cache
NUM_CACHE_SERVERS = int(os.environ.get("NUM_CACHE_SERVERS", "3"))


DEFAULT_CONF_ROUTER_TEMPLATE = '''
upstream cache {
  hash $http_x_cwmcdn_tenant_name$request_uri consistent;
__SERVERS__
}

server {
    listen       80;
    server_name  _;
    location / {
        proxy_pass http://cache;
    }
}
'''

DEFAULT_CONF_CACHE_TEMPLATE = '''
resolver 169.254.20.10 valid=30s;
resolver_timeout 5s;

server {
    listen       80;
    server_name  _;
    location / {
        proxy_pass http://tenant.$http_x_cwmcdn_tenant_name;
    }
}
'''


def main():
    if not TYPE or not NUM_CACHE_SERVERS:
        raise Exception("TYPE, NUM_CACHE_SERVERS environment variables must be set")
    if TYPE == "router":
        default_conf = DEFAULT_CONF_ROUTER_TEMPLATE
        servers_conf = []
        for i in range(1, NUM_CACHE_SERVERS + 1):
            servers_conf.append(f'  server cache{i}:80;')
        default_conf = default_conf.replace("__SERVERS__", "\n".join(servers_conf))
    elif TYPE == "cache":
        default_conf = DEFAULT_CONF_CACHE_TEMPLATE
    else:
        raise Exception("TYPE must be 'router' or 'cache'")
    with open("/etc/nginx/conf.d/default.conf", "w") as f:
        f.write(default_conf)


if __name__ == "__main__":
    main()
