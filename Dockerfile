ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION} AS build
ARG UV_VERSION=0.8.8
RUN curl -LsSf https://astral.sh/uv/${UV_VERSION}/install.sh | sh
COPY pyproject.toml uv.lock /srv/cwm-cdn-api/
WORKDIR /srv/cwm-cdn-api
RUN ~/.local/bin/uv export --no-emit-project > requirements.txt

FROM python:${PYTHON_VERSION}
ARG KUBECTL_VERSION=v1.33.1
RUN curl -LsS https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl -o /usr/local/bin/kubectl && \
    chmod +x /usr/local/bin/kubectl
RUN mkdir /srv/cwm-cdn-api && adduser --system cwm-cdn-api --home /srv/cwm-cdn-api
COPY --from=build /srv/cwm-cdn-api/requirements.txt /srv/cwm-cdn-api/requirements.txt
RUN pip install --upgrade pip && pip install --no-cache-dir -r /srv/cwm-cdn-api/requirements.txt
WORKDIR /srv/cwm-cdn-api
COPY pyproject.toml gunicorn.conf.py docker_entrypoint.sh ./
COPY cwm_cdn_api ./cwm_cdn_api
RUN pip install --no-cache-dir --no-deps -e .
ARG VERSION=docker-build
RUN echo "VERSION = '${VERSION}'" > cwm_cdn_api/version.py
USER cwm-cdn-api
CMD ["./docker_entrypoint.sh"]
