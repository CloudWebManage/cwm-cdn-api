#!/usr/bin/python3
import os


CERT = os.getenv("CERT")
KEY = os.getenv("KEY")
ORIGIN_URL = os.getenv("ORIGIN_URL")
TENANT_NAME = os.getenv("TENANT_NAME")


DEFAULT_CONF_TEMPLATE = '''
server {
    listen 443 ssl;
    server_name  _;
    ssl_certificate /certs/tls.crt;
    ssl_certificate_key /certs/tls.key;
    location / {
        proxy_pass http://router.cdn-cache;
        proxy_set_header X-CWMCDN-Tenant-Name __TENANT_NAME__;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}

server {
    listen 80;
    server_name  _;
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
    }
}
'''


def main():
    if not CERT or not KEY or not ORIGIN_URL or not TENANT_NAME:
        raise Exception("CERT, KEY, TENANT_NAME, ORIGIN_URL environment variables must be set")
    default_conf = DEFAULT_CONF_TEMPLATE
    assert ORIGIN_URL.startswith('http')
    for k, v in {
        "__TENANT_NAME__": TENANT_NAME,
        "__ORIGIN_URL__": ORIGIN_URL,
        "__ORIGIN_URL_HOST__": ORIGIN_URL.split("://", 1)[1].split("/", 1)[0],
        "__ORIGIN_URL_SCHEME__": "https" if ORIGIN_URL.startswith("https") else "http",
    }.items():
        default_conf = default_conf.replace(k, v)
    with open("/etc/nginx/conf.d/default.conf", "w") as f:
        f.write(default_conf)
    os.makedirs("/certs", exist_ok=True)
    with open("/certs/tls.crt", "w") as f:
        f.write(CERT)
    with open("/certs/tls.key", "w") as f:
        f.write(KEY)
    os.chmod("/certs/tls.crt", 0o600)
    os.chmod("/certs/tls.key", 0o600)


if __name__ == "__main__":
    main()
