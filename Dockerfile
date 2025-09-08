ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION} AS build
ARG UV_VERSION=0.8.8
RUN curl -LsSf https://astral.sh/uv/${UV_VERSION}/install.sh | sh
COPY pyproject.toml uv.lock /srv/cwm-minio-api/
WORKDIR /srv/cwm-cdn-api
RUN ~/.local/bin/uv export --no-emit-project > requirements.txt

FROM python:${PYTHON_VERSION}
ARG MIGRATE_VERSION=v4.18.3
RUN curl -L https://github.com/golang-migrate/migrate/releases/download/${MIGRATE_VERSION}/migrate.linux-amd64.tar.gz | tar xvz &&\
    mv migrate /usr/local/bin/migrate &&\
    chmod +x /usr/local/bin/migrate
RUN mkdir /srv/cwm-cdn-api && adduser --system cwm-cdn-api --home /srv/cwm-cdn-api
COPY --from=build /srv/cwm-cdn-api/requirements.txt /srv/cwm-cdn-api/requirements.txt
RUN pip install --upgrade pip && pip install --no-cache-dir -r /srv/cwm-cdn-api/requirements.txt
WORKDIR /srv/cwm-cdn-api
COPY pyproject.toml gunicorn.conf.py docker_entrypoint.sh ./
COPY migrations ./migrations
COPY bin ./bin
COPY cwm_cdn_api ./cwm_cdn_api
RUN pip install --no-cache-dir --no-deps -e .
ARG VERSION=docker-build
RUN echo "VERSION = '${VERSION}'" > cwm_cdn_api/version.py
USER cwm-cdn-api
CMD ["./docker_entrypoint.sh"]
