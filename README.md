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
