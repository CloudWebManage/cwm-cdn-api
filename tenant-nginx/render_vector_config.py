import os
import json

BASE_PATH = os.path.dirname(__file__)
VECTOR_TEMPLATE_PATH = os.path.join(BASE_PATH, "vector.template.yaml")

ENV_CONF_STRING = {
    "ES_API_VERSION": "api_version",
    "ES_COMPRESSION": "compression",
    "ES_DOC_TYPE": "doc_type",
    "ES_MODE": "mode",
    "ES_OPENSEARCH_SERVICE_TYPE": "opensearch_service_type",
}
ENV_CONF_JSON = {
    "ES_AUTH": "auth",
    "ES_BULK": "bulk",
    "ES_DATA_STREAM": "data_stream",
    "ES_ENCODING": "encoding",
    "ES_ENDPOINTS": "endpoints",
}


def get_es_logs_sink():
    sink = {
        "type": "elasticsearch",
        "inputs": ["parse_nginx"],
    }
    for env_key, conf_key in ENV_CONF_STRING.items():
        if os.environ.get(env_key):
            sink[conf_key] = os.environ[env_key]
    for env_key, conf_key in ENV_CONF_JSON.items():
        if os.environ.get(env_key):
            sink[conf_key] = json.loads(os.environ[env_key])
    sinkjson = json.dumps(sink)
    for char in ["{{", "}}", "$"]:
        assert char not in sinkjson
    return sink


def main():
    with open(VECTOR_TEMPLATE_PATH) as f:
        vector_conf_yaml = f.read()
    sinks = {}
    if os.environ.get("ENABLE_ES_SINK", "false").lower() in ("1", "true", "yes"):
        sinks["es"] = get_es_logs_sink()
    if os.environ.get("ENABLE_PLATFORM_LOGS", "false").lower() in ("1", "true", "yes"):
        # Platform log delivery is intentionally independent from tenant ES export.
        # IaC may later replace this with a central collector sink; stdout keeps the
        # container-runtime log path usable without adding a new dependency here.
        sinks["platform_console"] = {
            "type": "console",
            "inputs": ["parse_nginx"],
            "encoding": {
                "codec": "json",
            },
        }
    if os.environ.get("ENABLE_DEBUG_SINK", "false").lower() in ("1", "true", "yes") or not sinks:
        sinks["debug"] = {
            "type": "console",
            "inputs": ["parse_nginx"],
            "encoding": {
                "codec": "json",
            },
        }
    for k, v in {
        "VECTOR_DATA_DIR": "/var/vector/data",
        "NGINX_ACCESS_LOGS_PATH": "/var/log/nginx/access.logjson",
        "SINKS_JSON": json.dumps(sinks)
    }.items():
        vector_conf_yaml = vector_conf_yaml.replace(f"__{k}__", v)
    print(vector_conf_yaml)


if __name__ == "__main__":
    main()
