# CWM CDN API

## Usage

Once you run the app, see the API documentation at the app domain under path `/docs`

## Configuration

Configuration values are set as env vars in `.env` file.

You can see the full list of available configuration options in the following files:

* App configuration values: [cwm_cdn_api/config.py](cwm_cdn_api/config.py)
* Web server configuration values: [gunicorn_conf.py](gunicorn_conf.py)

## Local Development

Prerequisites:

* Python 3.12
* [uv](https://pypi.org/project/uv/)
* Docker
* kubectl connected to a Kubernetes cluster with cwm-cdn-operator and related CRDs installed

Install:

```
uv sync
```

Set configuration values in `.env` file (See Configuration section above for details)

Run the CLI:

```shell
uv run cwm-minio-api --help
```

Run the web app:

```
uv run uvicorn cwm_cdn_api.app:app --reload --factory
```

Access the API Docs at http://localhost:8000/docs

## CDN Load Tests

Install load-test dependencies:

```shell
uv sync --extra load-test
```

Recommended minimal configuration:

```shell
CWM_CDN_API_URL=
CWM_CDN_API_USERNAME=
CWM_CDN_API_PASSWORD=
CWM_CDN_KEEP_TENANTS=true
CWM_CDN_NUM_TENANTS=5
CWM_CDN_EDGE_HOSTS=
CWM_CDN_ORIGIN_URL=
```

Run a smoke test:

```shell
uv run locust -f cwm_cdn_api/load_tests/locustfile.py --processes 2
```

Start tests from the web UI:

http://localhost:8089


Cleanup load-test tenants by prefix:

```shell
uv run cwm-cdn-api load-tests cleanup
```
