FROM ghcr.io/cloudwebmanage/cwm-iac-openresty:08370351309932e3feba7de76114b9fb416b114d
RUN apk update &&\
    apk add --no-cache supervisor logrotate &&\
    mkdir vector &&\
    curl -sSfL https://packages.timber.io/vector/0.51.1/vector-0.51.1-x86_64-unknown-linux-musl.tar.gz | \
    tar xzf - -C vector --strip-components=2 &&\
    mv vector/bin/vector /usr/local/bin/vector &&\
    rm -rf vector &&\
    mkdir -p /var/vector/data
RUN if [ ! -f /etc/nginx/metrics_shared_dict.conf ]; then \
        awk '/^lua_shared_dict prometheus_metrics/ { print }' /etc/nginx/metrics.conf > /etc/nginx/metrics_shared_dict.conf; \
    fi && \
    if [ ! -f /etc/nginx/metrics_init.lua ]; then \
        awk 'BEGIN { in_block = 0 } /^init_worker_by_lua_block/ { in_block = 1; next } in_block && /^}/ { exit } in_block { sub(/^    /, ""); print }' /etc/nginx/metrics.conf > /etc/nginx/metrics_init.lua; \
    fi && \
    if [ ! -f /etc/nginx/metrics_log.lua ]; then \
        awk 'BEGIN { in_block = 0 } /^log_by_lua_block/ { in_block = 1; next } in_block && /^}/ { exit } in_block { sub(/^    /, ""); print }' /etc/nginx/metrics.conf > /etc/nginx/metrics_log.lua; \
    fi
COPY tenant-nginx/entrypoint.sh /srv/
COPY tenant-nginx/logrotate.conf /srv/
COPY tenant-nginx/logrotate.sh /srv/
COPY tenant-nginx/render_nginx_conf.py /srv/
COPY tenant-nginx/render_vector_config.py /srv/
COPY tenant-nginx/supervisord.conf /srv/
COPY tenant-nginx/vector.template.yaml /srv/
COPY lua/origin_balancer.lua lua/origin_health.lua /usr/local/openresty/lualib/
RUN chmod 0644 /usr/local/openresty/lualib/origin_balancer.lua /usr/local/openresty/lualib/origin_health.lua
ENTRYPOINT ["/srv/entrypoint.sh"]
