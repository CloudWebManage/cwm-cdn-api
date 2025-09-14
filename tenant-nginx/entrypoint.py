#!/usr/bin/python3
import os


CERT = os.getenv("CERT")
KEY = os.getenv("KEY")
ORIGIN_URL = os.getenv("ORIGIN_URL")


DEFAULT_CONF_TEMPLATE = '''
server {
    listen 443 ssl http2;
    server_name  _;
    ssl_certificate /certs/tls.crt;
    ssl_certificate_key /certs/tls.key;
    location / {
        proxy_pass __ORIGIN_URL__;
        proxy_set_header Host $proxy_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_ssl_server_name on;
    }
}
'''


def main():
    if not CERT or not KEY or not ORIGIN_URL:
        raise Exception("CERT, KEY and ORIGIN_URL environment variables must be set")
    with open("/etc/nginx/conf.d/default.conf", "w") as f:
        f.write(DEFAULT_CONF_TEMPLATE.replace("__ORIGIN_URL__", ORIGIN_URL))
    os.makedirs("/certs", exist_ok=True)
    with open("/certs/tls.crt", "w") as f:
        f.write(CERT)
    with open("/certs/tls.key", "w") as f:
        f.write(KEY)
    os.chmod("/certs/tls.crt", 0o600)
    os.chmod("/certs/tls.key", 0o600)


if __name__ == "__main__":
    main()
