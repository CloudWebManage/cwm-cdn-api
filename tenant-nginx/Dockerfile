FROM ghcr.io/cloudwebmanage/cwm-iac-openresty:08370351309932e3feba7de76114b9fb416b114d
RUN apk update &&\
    apk add --no-cache supervisor logrotate &&\
    mkdir vector &&\
    curl -sSfL https://packages.timber.io/vector/0.51.1/vector-0.51.1-x86_64-unknown-linux-musl.tar.gz | \
    tar xzf - -C vector --strip-components=2 &&\
    mv vector/bin/vector /usr/local/bin/vector &&\
    rm -rf vector &&\
    mkdir -p /var/vector/data
COPY entrypoint.sh /srv/
COPY logrotate.conf /srv/
COPY logrotate.sh /srv/
COPY render_nginx_conf.py /srv/
COPY render_vector_config.py /srv/
COPY supervisord.conf /srv/
COPY vector.template.yaml /srv/
ENTRYPOINT ["/srv/entrypoint.sh"]
