import asyncio
import hashlib
import logging
import time
import uuid
from urllib.parse import urlsplit, urlunsplit

import requests
from prometheus_client import Counter, Histogram

from . import config


MAX_SELECTORS = 100

PURGE_REQUESTS_TOTAL = Counter(
    "cwm_cdn_purge_requests_total",
    "CDN purge requests handled by the local POP",
    ["scope", "result"],
)
PURGE_DURATION_SECONDS = Histogram(
    "cwm_cdn_purge_duration_seconds",
    "CDN local cache-admin purge duration seconds",
    ["scope", "result"],
)
PURGE_PROPAGATION_TOTAL = Counter(
    "cwm_cdn_purge_propagation_total",
    "CDN purge propagation attempts to secondary POP APIs",
    ["result"],
)
CACHE_ADMIN_FAILURES_TOTAL = Counter(
    "cwm_cdn_cache_admin_failures_total",
    "CDN cache-admin purge shard failures",
    [],
)


def parse_cache_admin_endpoints(value=None):
    value = config.CACHE_ADMIN_ENDPOINTS if value is None else value
    if not value:
        return []
    value = value.strip()
    if value.startswith("["):
        endpoints = config.parse_json_env(value, [])
        return [
            {"name": item.get("name") or item["url"], "url": item["url"].rstrip("/")}
            for item in endpoints
        ]
    endpoints = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            name, url = part.split("=", 1)
        else:
            name, url = part, part
        endpoints.append({"name": name.strip(), "url": url.strip().rstrip("/")})
    return endpoints


def parse_secondaries(value=None):
    value = config.SECONDARIES_JSON if value is None else value
    if not value:
        return []
    secondaries = config.parse_json_env(value, [])
    return [{**item, "url": item["url"].rstrip("/")} for item in secondaries]


def _selector_hash(selector):
    material = "|".join(str(selector.get(k, "")) for k in ("type", "path", "query"))
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def tenant_domains(tenant_object):
    return {domain.get("name") for domain in tenant_object.get("spec", {}).get("domains", []) if domain.get("name")}


def normalize_path_selector(path, selector_type):
    if not isinstance(path, str):
        raise ValueError(f"{selector_type} selector must be a string")
    if "://" in path or path.startswith("//"):
        raise ValueError(f"{selector_type} selector must not include scheme or host")
    parts = urlsplit(path)
    if parts.scheme or parts.netloc or parts.fragment:
        raise ValueError(f"{selector_type} selector must be path-only and must not include fragments")
    if not parts.path.startswith("/"):
        raise ValueError(f"{selector_type} selector must start with /")
    if selector_type == "prefix":
        if parts.query:
            raise ValueError("prefix selectors must not include a query string")
        if parts.path == "/":
            raise ValueError("prefix / is not allowed; use /purge-everything")
        if not parts.path.endswith("/"):
            raise ValueError("prefix selectors must end with / to avoid sibling-prefix matches")
    return {"type": selector_type, "path": parts.path, "query": parts.query or None}


def normalize_url_selector(url, allowed_domains):
    if not isinstance(url, str):
        raise ValueError("url selector must be a string")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("url selectors must be absolute http or https URLs")
    host = parts.hostname
    if host not in allowed_domains:
        raise ValueError(f"url host is not configured for this tenant: {host}")
    path = urlunsplit(("", "", parts.path or "/", parts.query, ""))
    return normalize_path_selector(path, "path")


def normalize_purge_selectors(body, tenant_object):
    selectors = []
    body = body or {}
    for key in body.keys():
        if key not in {"paths", "prefixes", "urls"}:
            raise ValueError(f"Unsupported purge request field: {key}")
    for path in body.get("paths") or []:
        selectors.append(normalize_path_selector(path, "path"))
    for prefix in body.get("prefixes") or []:
        selectors.append(normalize_path_selector(prefix, "prefix"))
    domains = tenant_domains(tenant_object)
    for url in body.get("urls") or []:
        selectors.append(normalize_url_selector(url, domains))
    if not selectors:
        raise ValueError("at least one purge selector is required")
    if len(selectors) > MAX_SELECTORS:
        raise ValueError(f"at most {MAX_SELECTORS} purge selectors are allowed")
    return selectors


def validate_normalized_selectors(selectors):
    if not isinstance(selectors, list):
        raise ValueError("selectors must be a list")
    if not selectors:
        raise ValueError("at least one purge selector is required")
    if len(selectors) > MAX_SELECTORS:
        raise ValueError(f"at most {MAX_SELECTORS} purge selectors are allowed")
    normalized = []
    for selector in selectors:
        if not isinstance(selector, dict):
            raise ValueError("selectors must contain objects")
        if set(selector) - {"type", "path", "query"}:
            raise ValueError("selectors contain unsupported fields")
        selector_type = selector.get("type")
        if selector_type not in ("path", "prefix"):
            raise ValueError("selector.type must be path or prefix")
        path = selector.get("path")
        if not isinstance(path, str):
            raise ValueError("selector.path must be a string")
        if selector.get("query") is not None and not isinstance(selector.get("query"), str):
            raise ValueError("selector.query must be a string when set")
        selector_value = path + (("?" + selector["query"]) if selector_type == "path" and selector.get("query") else "")
        normalized.append(normalize_path_selector(selector_value, selector_type))
    return normalized


def normalized_purge_body(operation_id, selectors=None, everything=False):
    body = {"operationId": operation_id}
    if everything:
        body["scope"] = "everything"
    else:
        body["selectors"] = selectors or []
    return body


async def _post_json(url, payload, headers, timeout):
    def request():
        return requests.post(url, json=payload, headers=headers, timeout=timeout)
    return await asyncio.to_thread(request)


async def purge_local(operation_id, tenant_name, selectors=None, everything=False):
    endpoints = parse_cache_admin_endpoints()
    started = time.monotonic()
    if not endpoints:
        return {
            "popId": config.POP_ID,
            "status": "not_configured",
            "success": False,
            "message": "CACHE_ADMIN_ENDPOINTS is not configured",
            "shards": [],
            "durationSeconds": 0,
        }
    if not config.CACHE_ADMIN_BEARER_TOKEN:
        return {
            "popId": config.POP_ID,
            "status": "not_configured",
            "success": False,
            "message": "CACHE_ADMIN_BEARER_TOKEN is required for cache-admin purge",
            "shards": [],
            "durationSeconds": 0,
        }
    payload = {
        "operationId": operation_id,
        "tenant": tenant_name,
        "scope": "everything" if everything else "selectors",
        "selectors": [] if everything else selectors,
    }
    headers = {"Authorization": f"Bearer {config.CACHE_ADMIN_BEARER_TOKEN}"}
    sem = asyncio.Semaphore(config.CACHE_ADMIN_CONCURRENCY)

    async def call(endpoint):
        async with sem:
            shard_started = time.monotonic()
            try:
                response = await _post_json(f"{endpoint['url']}/internal/purge", payload, headers, config.CACHE_ADMIN_TIMEOUT_SECONDS)
                response_headers = getattr(response, "headers", {}) or {}
                data = response.json() if response_headers.get("content-type", "").startswith("application/json") else None
                ok = 200 <= response.status_code < 300 and (not isinstance(data, dict) or data.get("success") is not False)
                message = None if ok else response.text[:500]
            except Exception as exc:
                ok = False
                data = None
                message = str(exc)
                response = None
            return {
                "shard": endpoint["name"],
                "success": ok,
                "statusCode": response.status_code if response is not None else None,
                "message": message,
                "selectorsApplied": data.get("selectorsApplied") if isinstance(data, dict) else None,
                "latencySeconds": round(time.monotonic() - shard_started, 3),
            }

    shards = await asyncio.gather(*(call(endpoint) for endpoint in endpoints))
    successes = sum(1 for shard in shards if shard["success"])
    if successes == len(shards):
        status = "success"
        success = True
    elif successes == 0:
        status = "failed"
        success = False
    else:
        status = "partial"
        success = False
    logging.info("cdn purge local result", extra={
        "operation_id": operation_id,
        "tenant": tenant_name,
        "selector_count": 0 if everything else len(selectors or []),
        "scope": payload["scope"],
        "pop_id": config.POP_ID,
        "status": status,
    })
    scope = payload["scope"]
    PURGE_REQUESTS_TOTAL.labels(scope=scope, result=status).inc()
    PURGE_DURATION_SECONDS.labels(scope=scope, result=status).observe(time.monotonic() - started)
    CACHE_ADMIN_FAILURES_TOTAL.inc(len(shards) - successes)
    return {
        "popId": config.POP_ID,
        "status": status,
        "success": success,
        "message": None,
        "purgedSelectors": sum(shard.get("selectorsApplied") or 0 for shard in shards if shard["success"]),
        "shards": shards,
        "durationSeconds": round(time.monotonic() - started, 3),
    }


async def propagate_to_secondaries(operation_id, endpoint_path, tenant_name, body):
    if not config.IS_PRIMARY:
        return []
    secondaries = parse_secondaries()
    if not secondaries:
        return []
    if not config.ALLOWED_PRIMARY_KEY:
        return [{"popId": item.get("name") or item["url"], "status": "not_configured", "success": False, "message": "ALLOWED_PRIMARY_KEY is not configured on primary"} for item in secondaries]

    async def call(secondary):
        name = secondary.get("name") or secondary["url"]
        url = f"{secondary['url']}{endpoint_path}"
        params = {"cdn_tenant_name": tenant_name, "primary_key": config.ALLOWED_PRIMARY_KEY}
        auth = None
        if secondary.get("username") and secondary.get("password"):
            auth = (secondary["username"], secondary["password"])
        started = time.monotonic()
        body_status = None
        try:
            def request():
                return requests.post(url, params=params, json=body, timeout=config.SECONDARY_TIMEOUT_SECONDS, auth=auth)
            response = await asyncio.to_thread(request)
            data = response.json() if response.headers.get("content-type", "").startswith("application/json") else None
            body_success = data.get("success") is True if isinstance(data, dict) else False
            body_status = data.get("status") if isinstance(data, dict) else None
            ok = response.status_code == 200 and body_success and body_status in (None, "success")
            message = None if ok else response.text[:500]
        except Exception as exc:
            ok = False
            data = None
            message = str(exc)
            response = None
        return {
            "popId": name,
            "status": body_status if body_status in ("success", "partial", "failed", "not_configured", "skipped") else ("success" if ok else "failed"),
            "success": ok,
            "statusCode": response.status_code if response is not None else None,
            "message": message,
            "response": data,
            "latencySeconds": round(time.monotonic() - started, 3),
        }

    results = await asyncio.gather(*(call(secondary) for secondary in secondaries))
    for item in results:
        PURGE_PROPAGATION_TOTAL.labels(result=item["status"]).inc()
    logging.info("cdn purge secondary propagation result", extra={
        "operation_id": operation_id,
        "tenant": tenant_name,
        "secondary_count": len(results),
        "failed_count": sum(1 for item in results if not item["success"]),
    })
    return results


def new_operation_id():
    return str(uuid.uuid4())


def public_selectors(selectors):
    return [{**selector, "hash": _selector_hash(selector)} for selector in selectors]
