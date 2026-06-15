import ipaddress
import os
import re

HTTP_FIELD_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
DURATION_RE = re.compile(r"^(\d+)(s|m|h|d)$")
NGINX_SIZE_RE = re.compile(r"^(\d+)([kKmMgG])?$")
METHOD_RE = re.compile(r"^[A-Z]+$")
GLOB_PATH_RE = re.compile(r"^/[A-Za-z0-9._~/%:@+*,=-]*$")
REDIRECT_TARGET_RE = re.compile(r"^(?:/[A-Za-z0-9._~!&'()*+,=:@/%?-]*|https?://[A-Za-z0-9._~!&'()*+,=:@/%?-]+)$")
MAX_SECURITY_URL_BLOCK_RULES = 100
MAX_CAPTCHA_RULES = 100
MAX_REDIRECT_RULES = 100
MAX_METHODS = 32
MAX_CIDRS = 100

HOP_BY_HOP_OR_SENSITIVE_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "authorization",
    "cookie", "set-cookie", "host", "x-cwmcdn-tenant-name",
    "x-cwmcdn-cache-enabled", "x-cwmcdn-cache-edge-ttl-seconds",
    "x-cwmcdn-cache-respect-origin-cache-control", "x-cwmcdn-cache-status-header",
}

RAW_CONFIG_KEYS = {
    "config", "nginx", "nginxConfig", "nginx_config", "lua", "luaConfig",
    "snippet", "serverSnippet", "locationSnippet", "include", "file",
}


def _reject_unknown(obj, allowed, context):
    unknown = sorted(set(obj) - set(allowed))
    if unknown:
        raise ValueError(f"Unsupported {context} fields: {', '.join(unknown)}")


def _reject_raw_config_keys(obj, context="policy"):
    if isinstance(obj, dict):
        for key, value in obj.items():
            lower_key = key.lower()
            if key in RAW_CONFIG_KEYS or lower_key.endswith(("snippet", "config")) or "nginx" in lower_key or "lua" in lower_key:
                raise ValueError(f"Raw/generated config field is not allowed in {context}: {key}")
            _reject_raw_config_keys(value, f"{context}.{key}")
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            _reject_raw_config_keys(value, f"{context}[{i}]")


def _validate_bool(value, context):
    if not isinstance(value, bool):
        raise ValueError(f"{context} must be a boolean")


def parse_duration_seconds(value, context, min_seconds=1, max_seconds=30 * 24 * 60 * 60):
    if not isinstance(value, str):
        raise ValueError(f"{context} must be a duration string")
    match = DURATION_RE.match(value)
    if not match:
        raise ValueError(f"{context} must use s, m, h, or d duration syntax")
    amount = int(match.group(1))
    multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]
    seconds = amount * multiplier
    if seconds < min_seconds or seconds > max_seconds:
        raise ValueError(f"{context} must be between {min_seconds}s and {max_seconds}s")
    return seconds


def validate_http_field_name(value, context):
    if not isinstance(value, str):
        raise ValueError(f"{context} must be a string")
    if value == "":
        return
    if not HTTP_FIELD_NAME_RE.match(value):
        raise ValueError(f"{context} must be a valid HTTP field name")
    if value.lower() in HOP_BY_HOP_OR_SENSITIVE_HEADERS:
        raise ValueError(f"{context} must not be hop-by-hop, sensitive, or an internal CDN header")


def validate_cache_config(cache):
    if cache is None:
        return
    if not isinstance(cache, dict):
        raise ValueError("cache must be an object")
    _reject_unknown(cache, {"enabled", "mode", "edgeTtl", "respectOriginCacheControl", "statusHeader"}, "cache")
    if "enabled" in cache:
        _validate_bool(cache["enabled"], "cache.enabled")
    if "mode" in cache and cache["mode"] != "cache_everything":
        raise ValueError("cache.mode must be cache_everything")
    if "edgeTtl" in cache:
        edge_ttl_seconds = parse_duration_seconds(cache["edgeTtl"], "cache.edgeTtl")
        if edge_ttl_seconds != 3600:
            raise ValueError("cache.edgeTtl values other than 1h require cache-layer dynamic TTL support")
    if "respectOriginCacheControl" in cache:
        _validate_bool(cache["respectOriginCacheControl"], "cache.respectOriginCacheControl")
        if cache["respectOriginCacheControl"] is False:
            raise ValueError("cache.respectOriginCacheControl=false requires cache-layer origin-header override support")
    if "statusHeader" in cache:
        validate_http_field_name(cache["statusHeader"], "cache.statusHeader")


def _validate_cidrs(values, context):
    if not isinstance(values, list):
        raise ValueError(f"{context} must be a list")
    if len(values) > MAX_CIDRS:
        raise ValueError(f"{context} may contain at most {MAX_CIDRS} CIDRs")
    for cidr in values:
        ipaddress.ip_network(cidr)


def _validate_methods(values, context):
    if not isinstance(values, list):
        raise ValueError(f"{context} must be a list")
    if len(values) > MAX_METHODS:
        raise ValueError(f"{context} may contain at most {MAX_METHODS} methods")
    for method in values:
        if not isinstance(method, str) or not METHOD_RE.match(method):
            raise ValueError(f"{context} contains an invalid HTTP method")


def _validate_glob_path(value, context):
    if not isinstance(value, str) or not value.startswith("/") or "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError(f"{context} must be a safe absolute path glob")
    if not GLOB_PATH_RE.match(value):
        raise ValueError(f"{context} contains unsafe characters")
    if len(value) > 512:
        raise ValueError(f"{context} is too long")


def trusted_client_ip_enabled():
    return os.getenv("CWM_CDN_TRUSTED_CLIENT_IP_ENABLED", os.getenv("TRUSTED_CLIENT_IP_ENABLED", "")).lower() in ("1", "true", "yes")


def _require_unique_names(items, context):
    names = []
    for i, item in enumerate(items):
        name = item.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"{context}[{i}].name is required")
        names.append(name)
    if len(names) != len(set(names)):
        raise ValueError(f"{context} names must be unique")


def validate_security_policy(security):
    if security is None:
        return
    if not isinstance(security, dict):
        raise ValueError("security must be an object")
    _reject_unknown(security, {"ipAccess", "methods", "urls", "rateLimit", "request"}, "security")
    ip_access = security.get("ipAccess") or {}
    _reject_unknown(ip_access, {"allowCidrs", "blockCidrs"}, "security.ipAccess")
    _validate_cidrs(ip_access.get("allowCidrs", []), "security.ipAccess.allowCidrs")
    _validate_cidrs(ip_access.get("blockCidrs", []), "security.ipAccess.blockCidrs")
    if (ip_access.get("allowCidrs") or ip_access.get("blockCidrs")) and not trusted_client_ip_enabled():
        raise ValueError("security.ipAccess requires trusted client IP support to be enabled")
    methods = security.get("methods") or {}
    _reject_unknown(methods, {"allow", "block"}, "security.methods")
    _validate_methods(methods.get("allow", []), "security.methods.allow")
    _validate_methods(methods.get("block", []), "security.methods.block")
    urls = security.get("urls") or {}
    _reject_unknown(urls, {"block"}, "security.urls")
    block_rules = urls.get("block", [])
    if len(block_rules) > MAX_SECURITY_URL_BLOCK_RULES:
        raise ValueError(f"security.urls.block may contain at most {MAX_SECURITY_URL_BLOCK_RULES} rules")
    _require_unique_names(block_rules, "security.urls.block")
    for i, rule in enumerate(block_rules):
        _reject_unknown(rule, {"name", "match"}, f"security.urls.block[{i}]")
        match = rule.get("match") or {}
        _reject_unknown(match, {"type", "path"}, f"security.urls.block[{i}].match")
        if match.get("type") != "glob":
            raise ValueError("security.urls.block match.type must be glob")
        _validate_glob_path(match.get("path"), f"security.urls.block[{i}].match.path")
    rate_limit = security.get("rateLimit") or {}
    _reject_unknown(rate_limit, {"enabled", "requests", "period", "burst", "key", "action"}, "security.rateLimit")
    if rate_limit.get("enabled"):
        if not isinstance(rate_limit.get("requests"), int) or rate_limit["requests"] < 1:
            raise ValueError("security.rateLimit.requests must be a positive integer")
        if "burst" in rate_limit and (not isinstance(rate_limit["burst"], int) or rate_limit["burst"] < 0):
            raise ValueError("security.rateLimit.burst must be a non-negative integer")
        if rate_limit.get("period"):
            parse_duration_seconds(rate_limit["period"], "security.rateLimit.period", max_seconds=3600)
        if rate_limit.get("key") not in (None, "", "clientIp"):
            raise ValueError("security.rateLimit.key must be clientIp")
        if rate_limit.get("key") in (None, "", "clientIp") and not trusted_client_ip_enabled():
            raise ValueError("security.rateLimit.key=clientIp requires trusted client IP support to be enabled")
        if rate_limit.get("action", "block") not in ("block", "captcha"):
            raise ValueError("security.rateLimit.action must be block or captcha")
    request = security.get("request") or {}
    _reject_unknown(request, {"maxBodySize"}, "security.request")
    if "maxBodySize" in request and not NGINX_SIZE_RE.match(str(request["maxBodySize"])):
        raise ValueError("security.request.maxBodySize must be an nginx size value")


def validate_captcha_policy(captcha):
    if captcha is None:
        return
    if not isinstance(captcha, dict):
        raise ValueError("captcha must be an object")
    _reject_unknown(captcha, {"enabled", "provider", "siteKey", "secretRef", "cookieTtl", "rules"}, "captcha")
    if "enabled" in captcha:
        _validate_bool(captcha["enabled"], "captcha.enabled")
    if captcha.get("enabled"):
        if captcha.get("provider") != "turnstile":
            raise ValueError("captcha.provider must be turnstile")
        if not captcha.get("siteKey"):
            raise ValueError("captcha.siteKey is required")
        secret_ref = captcha.get("secretRef") or {}
        _reject_unknown(secret_ref, {"name", "key"}, "captcha.secretRef")
        if not secret_ref.get("name") or not secret_ref.get("key"):
            raise ValueError("captcha.secretRef.name and key are required")
        if "secret" in captcha or "secretValue" in captcha:
            raise ValueError("captcha provider secrets must be referenced, not embedded")
        if captcha.get("cookieTtl"):
            parse_duration_seconds(captcha["cookieTtl"], "captcha.cookieTtl", max_seconds=24 * 60 * 60)
    captcha_rules = captcha.get("rules", [])
    if len(captcha_rules) > MAX_CAPTCHA_RULES:
        raise ValueError(f"captcha.rules may contain at most {MAX_CAPTCHA_RULES} rules")
    _require_unique_names(captcha_rules, "captcha.rules")
    if captcha_rules and not captcha.get("enabled"):
        raise ValueError("captcha.rules require captcha.enabled=true")
    for i, rule in enumerate(captcha_rules):
        _reject_unknown(rule, {"name", "match"}, f"captcha.rules[{i}]")
        match = rule.get("match") or {}
        _reject_unknown(match, {"type", "path"}, f"captcha.rules[{i}].match")
        if match.get("type") != "glob":
            raise ValueError("captcha rule match.type must be glob")
        _validate_glob_path(match.get("path"), f"captcha.rules[{i}].match.path")


def validate_redirects(redirects):
    if redirects is None:
        return
    if not isinstance(redirects, list):
        raise ValueError("redirects must be a list")
    if len(redirects) > MAX_REDIRECT_RULES:
        raise ValueError(f"redirects may contain at most {MAX_REDIRECT_RULES} rules")
    _require_unique_names(redirects, "redirects")
    for i, redirect in enumerate(redirects):
        _reject_unknown(redirect, {"name", "enabled", "when", "to", "status", "preserveQuery"}, f"redirects[{i}]")
        when = redirect.get("when") or {}
        _reject_unknown(when, {"path", "originStatus", "upstreamStatus"}, f"redirects[{i}].when")
        _validate_http_statuses(when.get("originStatus", []), f"redirects[{i}].when.originStatus")
        _validate_http_statuses(when.get("upstreamStatus", []), f"redirects[{i}].when.upstreamStatus")
        if when.get("originStatus") and when.get("upstreamStatus"):
            raise ValueError("redirect when.originStatus and when.upstreamStatus are mutually exclusive")
        path = when.get("path") or {}
        if not path and not when.get("originStatus") and not when.get("upstreamStatus"):
            raise ValueError("redirect requires at least one matcher: when.path, when.originStatus, or when.upstreamStatus")
        _reject_unknown(path, {"type", "value"}, f"redirects[{i}].when.path")
        if path and path.get("type") != "glob":
            raise ValueError("redirect path.type must be glob")
        if path:
            _validate_glob_path(path.get("value"), f"redirects[{i}].when.path.value")
        target = redirect.get("to")
        if not isinstance(target, str) or "\n" in target or "\r" in target:
            raise ValueError("redirect target must be a safe string")
        if target.startswith("//") or not REDIRECT_TARGET_RE.match(target) or any(c in target for c in " $;{}"):
            raise ValueError("redirect target must be a safe relative /... or absolute http(s) URL")
        if path and "*" not in path.get("value", "") and target.split("?", 1)[0] == path.get("value"):
            raise ValueError("direct self-redirects are not allowed")
        if redirect.get("status", 302) not in (301, 302, 307, 308):
            raise ValueError("redirect status must be one of 301, 302, 307, 308")


def _validate_http_statuses(values, context):
    if not isinstance(values, list):
        raise ValueError(f"{context} must be a list")
    seen = set()
    for status in values:
        if not isinstance(status, int) or status < 100 or status > 599:
            raise ValueError(f"{context} contains an invalid HTTP status")
        if status in seen:
            raise ValueError(f"{context} contains duplicate HTTP status {status}")
        seen.add(status)


def validate_tenant_spec(spec):
    if not isinstance(spec, dict):
        raise ValueError("tenant spec must be an object")
    validate_cache_config(spec.get("cache"))
    policy = {k: spec.get(k) for k in ("security", "captcha", "redirects") if k in spec}
    if policy:
        _reject_raw_config_keys(policy)
    validate_security_policy(spec.get("security"))
    validate_captcha_policy(spec.get("captcha"))
    rate_limit = (spec.get("security") or {}).get("rateLimit") or {}
    if rate_limit.get("enabled") and rate_limit.get("action", "block") == "captcha" and not (spec.get("captcha") or {}).get("enabled"):
        raise ValueError("security.rateLimit.action=captcha requires captcha.enabled=true")
    validate_redirects(spec.get("redirects"))
