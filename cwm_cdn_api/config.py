import os
import json

import dotenv


dotenv.load_dotenv()


CWM_LOG_LEVEL = os.getenv("CWM_LOG_LEVEL", "DEBUG")
CWM_ENV_TYPE = os.getenv("CWM_ENV_TYPE")

NAMESPACE = os.getenv("NAMESPACE", "default")
IS_PRIMARY = os.getenv("IS_PRIMARY", "") == "true"
ALLOWED_PRIMARY_KEY = os.getenv("ALLOWED_PRIMARY_KEY", "")

POP_ID = os.getenv("POP_ID", os.getenv("CWM_CDN_POP_ID", "unknown"))

CACHE_ADMIN_ENDPOINTS = os.getenv("CACHE_ADMIN_ENDPOINTS", "")
CACHE_ADMIN_BEARER_TOKEN = os.getenv("CACHE_ADMIN_BEARER_TOKEN", "")
CACHE_ADMIN_TIMEOUT_SECONDS = float(os.getenv("CACHE_ADMIN_TIMEOUT_SECONDS", "5"))
CACHE_ADMIN_CONCURRENCY = int(os.getenv("CACHE_ADMIN_CONCURRENCY", "8"))

SECONDARIES_JSON = os.getenv("SECONDARIES_JSON", "[]")
SECONDARY_TIMEOUT_SECONDS = float(os.getenv("SECONDARY_TIMEOUT_SECONDS", "10"))

def parse_json_env(value, default):
    if not value:
        return default
    return json.loads(value)
