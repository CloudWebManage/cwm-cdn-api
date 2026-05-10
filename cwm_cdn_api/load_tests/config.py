import os
from pathlib import Path

import dotenv


REPO_ROOT = Path(__file__).resolve().parents[2]
dotenv.load_dotenv(REPO_ROOT / ".env")
dotenv.load_dotenv()


def _bool(name, default="no"):
    return os.getenv(name, default).lower() in ("1", "true", "yes", "on")


def _csv(name):
    return [value.strip() for value in os.getenv(name, "").split(",") if value.strip()]


# enable debug logging
CWM_CDN_LOAD_TESTS_DEBUG = _bool("CWM_CDN_LOAD_TESTS_DEBUG")

# required - cdn api creds to primary cdn api
CWM_CDN_API_URL = os.getenv("CWM_CDN_API_URL", "").rstrip("/")
CWM_CDN_API_USERNAME = os.getenv("CWM_CDN_API_USERNAME", "")
CWM_CDN_API_PASSWORD = os.getenv("CWM_CDN_API_PASSWORD", "")
CWM_CDN_API_VERIFY_TLS = _bool("CWM_CDN_API_VERIFY_TLS", "yes")

# wait time between locust tasks will be random between these values
CWM_CDN_WAIT_TIME_MIN_SECONDS = float(os.getenv("CWM_CDN_WAIT_TIME_MIN_SECONDS", "0"))
CWM_CDN_WAIT_TIME_MAX_SECONDS = float(os.getenv("CWM_CDN_WAIT_TIME_MAX_SECONDS", "1"))

# if true, will not delete tenants at end, and will try to reuse them on start
CWM_CDN_KEEP_TENANTS = _bool("CWM_CDN_KEEP_TENANTS")

# configure the tenants, only one of these can be specified
CWM_CDN_TENANT_NAMES = _csv("CWM_CDN_TENANT_NAMES")  # comma-separated values, each value can be 'tenant_name:tenant_domain'
                                                     # or 'tenant_name' (in which case domain is from CWM_CDN_TENANT_DOMAIN_SUFFIX)
CWM_CDN_TENANTS = _csv("CWM_CDN_TENANTS")  # comma-seaparated values, each value is just tenant name and domain is from CWM_CDN_TENANT_DOMAIN_SUFFIX
CWM_CDN_NUM_TENANTS = int(os.getenv("CWM_CDN_NUM_TENANTS", "0"))  # number of tenants, each tenant is named with CWM_CDN_TENANT_PREFIX and domain from CWM_CDN_TENANT_DOMAIN_SUFFIX

# used depending on tenant configuration above
CWM_CDN_TENANT_PREFIX = os.getenv("CWM_CDN_TENANT_PREFIX", "cmcdnlt")
CWM_CDN_TENANT_DOMAIN_SUFFIX = os.getenv("CWM_CDN_TENANT_DOMAIN_SUFFIX", "example.com")

# configure the edge ips to test on, all values from below are combined and deduped
CWM_CDN_EDGE_IPS = _csv("CWM_CDN_EDGE_IPS")  # comma-separated list of edge ips
CWM_CDN_EDGE_HOSTS = _csv("CWM_CDN_EDGE_HOSTS")  # comma-separated list of host names (will be resolved to ips)

# configure the tenant spec
CWM_CDN_ORIGIN_URL = os.getenv("CWM_CDN_ORIGIN_URL", "https://httpbin.org").rstrip("/")
CWM_CDN_TENANT_SPEC_PATH = os.getenv("CWM_CDN_TENANT_SPEC_PATH", str(REPO_ROOT / "tests" / "test_tenant.json"))
CWM_CDN_ES_ENDPOINTS = _csv("CWM_CDN_ES_ENDPOINTS")
CWM_CDN_ES_AUTH = os.getenv("CWM_CDN_ES_AUTH", "")
CWM_CDN_ES_BULK = os.getenv("CWM_CDN_ES_BULK", "")

# how to check tenant readiness - this is checked on every worker before starting
CWM_CDN_TENANT_READY_TIMEOUT_SECONDS = int(os.getenv("CWM_CDN_TENANT_READY_TIMEOUT_SECONDS", "300"))
CWM_CDN_TENANT_READY_POLL_SECONDS = float(os.getenv("CWM_CDN_TENANT_READY_POLL_SECONDS", "3"))

# configures the https connection pools
CWM_CDN_EDGE_VERIFY_TLS = _bool("CWM_CDN_EDGE_VERIFY_TLS")
CWM_CDN_EDGE_CONNECT_TIMEOUT_SECONDS = float(os.getenv("CWM_CDN_EDGE_CONNECT_TIMEOUT_SECONDS", "5"))
CWM_CDN_EDGE_READ_TIMEOUT_SECONDS = float(os.getenv("CWM_CDN_EDGE_READ_TIMEOUT_SECONDS", "30"))

# bypass writer worker - does a POST requests which bypasses the cache layers completely
CWM_CDN_BYPASS_WRITER_ENABLED = _bool("CWM_CDN_BYPASS_WRITER_ENABLED", "yes")
CWM_CDN_BYPASS_WRITER_WEIGHT = int(os.getenv("CWM_CDN_BYPASS_WRITER_WEIGHT", "1"))
CWM_CDN_BYPASS_WRITER_FIXED_COUNT = int(os.getenv("CWM_CDN_BYPASS_WRITER_FIXED_COUNT", "0"))

# cache getter worker - does cached GET requests
CWM_CDN_CACHE_GETTER_ENABLED = _bool("CWM_CDN_CACHE_GETTER_ENABLED", "yes")
CWM_CDN_CACHE_GETTER_WEIGHT = int(os.getenv("CWM_CDN_CACHE_GETTER_WEIGHT", "10"))
CWM_CDN_CACHE_GETTER_FIXED_COUNT = int(os.getenv("CWM_CDN_CACHE_GETTER_FIXED_COUNT", "0"))
CWM_CDN_CACHE_PATHS = _csv("CWM_CDN_CACHE_PATHS")  # predefined list of paths to test
CWM_CDN_CACHE_KEY_COUNT = int(os.getenv("CWM_CDN_CACHE_KEY_COUNT", "100"))  # adds this number of numbered paths

# cache miss worker - does non-cached unique GET requests
CWM_CDN_CACHE_MISS_GETTER_ENABLED = _bool("CWM_CDN_CACHE_MISS_GETTER_ENABLED", "yes")
CWM_CDN_CACHE_MISS_GETTER_WEIGHT = int(os.getenv("CWM_CDN_CACHE_MISS_GETTER_WEIGHT", "1"))
CWM_CDN_CACHE_MISS_GETTER_FIXED_COUNT = int(os.getenv("CWM_CDN_CACHE_MISS_GETTER_FIXED_COUNT", "0"))
