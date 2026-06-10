FROM ghcr.io/cloudwebmanage/cwm-iac-openresty:08370351309932e3feba7de76114b9fb416b114d
RUN apk update &&\
    apk add --no-cache supervisor logrotate &&\
    mkdir vector &&\
    curl -sSfL https://packages.timber.io/vector/0.51.1/vector-0.51.1-x86_64-unknown-linux-musl.tar.gz | \
    tar xzf - -C vector --strip-components=2 &&\
    mv vector/bin/vector /usr/local/bin/vector &&\
    rm -rf vector &&\
    mkdir -p /var/vector/data
COPY tenant-nginx/entrypoint.sh /srv/
COPY tenant-nginx/logrotate.conf /srv/
COPY tenant-nginx/logrotate.sh /srv/
COPY tenant-nginx/render_nginx_conf.py /srv/
COPY tenant-nginx/render_vector_config.py /srv/
COPY tenant-nginx/supervisord.conf /srv/
COPY tenant-nginx/vector.template.yaml /srv/
COPY lua/origin_balancer.lua lua/origin_health.lua /usr/local/openresty/lualib/
ENTRYPOINT ["/srv/entrypoint.sh"]
