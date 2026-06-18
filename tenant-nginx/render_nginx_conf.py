#!/usr/bin/python3
import json
import os
import re
import ipaddress
from copy import deepcopy
from urllib.parse import urlsplit


CDN_CACHE_ROUTER = os.getenv("CDN_CACHE_ROUTER", "http://router.cdn-cache")
LUA_SSL_TRUSTED_CERTIFICATE = "/etc/ssl/certs/ca-certificates.crt"
ACME_CHALLENGE_ROOT = os.getenv("ACME_CHALLENGE_ROOT", "/var/lib/cwm-cdn/acme-challenges")
TLS_VERSIONS = ("TLSv1.2", "TLSv1.3")

LUA_SSL_CONFIG = f'''lua_ssl_trusted_certificate {LUA_SSL_TRUSTED_CERTIFICATE};
lua_ssl_verify_depth 5;'''


DOMAIN_CONF_TEMPLATE = '''
server {
    listen 443 ssl;
    server_name  __SERVER_NAME__;
    ssl_certificate __CERT_PATH__;
    ssl_certificate_key __KEY_PATH__;
    ssl_protocols __TLS_PROTOCOLS__;
    __SERVER_NGINX_CONFIG__
    __EXTRA_LOCATION_NGINX_CONFIG__
    location / {
        __ACCESS_LOG_CONFIG__
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Request-ID $request_id;
        __POLICY_LOCATION_NGINX_CONFIG__
        __CACHE_ROUTING_NGINX_CONFIG__
        __LOCATION_NGINX_CONFIG__
    }
}
'''


CACHE_ENABLED_LOCATION_CONFIG_TEMPLATE = '''
        proxy_pass __CDN_CACHE_ROUTER__;
        proxy_set_header X-CWMCDN-Tenant-Name __TENANT_NAME__;
        proxy_set_header X-CWMCDN-Cache-Enabled true;
        proxy_set_header X-CWMCDN-Cache-Mode __CACHE_MODE__;
        proxy_set_header X-CWMCDN-Cache-Edge-TTL-Seconds __CACHE_EDGE_TTL_SECONDS__;
        proxy_set_header X-CWMCDN-Cache-Respect-Origin-Cache-Control __CACHE_RESPECT_ORIGIN_CACHE_CONTROL__;
        proxy_set_header X-CWMCDN-Cache-Status-Header __CACHE_STATUS_HEADER__;
        __CACHE_STATUS_ADD_HEADER__
        if ($request_method !~ ^(GET|HEAD)$ ) {
            __CACHE_BYPASS_ADD_HEADER__
            proxy_pass http://127.0.0.1:80;
        }
'''


CACHE_DISABLED_LOCATION_CONFIG_TEMPLATE = '''
        __CACHE_BYPASS_ADD_HEADER__
        proxy_pass http://127.0.0.1:80;
        proxy_set_header X-CWMCDN-Tenant-Name __TENANT_NAME__;
        proxy_set_header X-CWMCDN-Cache-Enabled false;
'''


CAPTCHA_RUNTIME_LOCATION_TEMPLATE = '''
    location ^~ /__cwmcdn/captcha/ {
        access_log off;
        content_by_lua_block {
            local cwm_policy = require "cwm_policy"
            local policy = cwm_policy.decode_policy(__POLICY_JSON__)
            local ok, status = cwm_policy.handle_captcha(policy, {
                signing_key_path = __CAPTCHA_SIGNING_KEY_PATH_JSON__,
            })
            if not ok then
                return ngx.exit(status or ngx.HTTP_INTERNAL_SERVER_ERROR)
            end
        }
    }
'''


DOMAIN_HTTP_CONF_TEMPLATE = '''
server {
    listen 80;
    server_name  __SERVER_NAME__;
    __SERVER_NGINX_CONFIG__
    __EXTRA_LOCATION_NGINX_CONFIG__
    location ^~ /.well-known/acme-challenge/ {
        access_log off;
        root __ACME_CHALLENGE_ROOT__;
        try_files $uri =404;
    }
    location / {
        __HTTP_LOCATION_CONFIG__
    }
}
'''


DOMAIN_HTTP_PROXY_LOCATION_CONFIG_TEMPLATE = '''
        __ACCESS_LOG_CONFIG__
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto http;
        __POLICY_LOCATION_NGINX_CONFIG__
        __CACHE_ROUTING_NGINX_CONFIG__
'''


ORIGINS_CONF_TEMPLATE = '''
__NGINX_RESOLVER_CONFIG__
lua_shared_dict origin_health 10m;
include /etc/nginx/metrics_shared_dict.conf;

init_worker_by_lua_block {
    dofile("/etc/nginx/metrics_init.lua")

    local origin_health = require "origin_health"
    local origins = __ORIGINS_LUA__
    local health = ngx.shared.origin_health

    local function check_origin(premature, origin)
        if premature then
            return
        end

        local start = ngx.now()
        local ok, status, message = false, 0, ""
        local sock = ngx.socket.tcp()
        sock:settimeout(origin.health.timeout * 1000)

        local connected, connect_err = sock:connect(origin.host, origin.port)
        if connected then
            if origin.scheme == "https" then
                local _, ssl_err = sock:sslhandshake(nil, origin.host, false)
                if ssl_err then
                    message = "tls handshake failed: " .. ssl_err
                end
            end
            if message == "" then
                local sent, send_err = sock:send(origin_health.build_check_request(origin))
                if sent then
                    local line, read_err = sock:receive("*l")
                    if line then
                        status = origin_health.parse_status_line(line)
                        ok = status == origin.health.expected_status
                        if not ok then
                            message = "unexpected status " .. status
                        end
                    else
                        message = "read failed: " .. (read_err or "unknown")
                    end
                else
                    message = "send failed: " .. (send_err or "unknown")
                end
            end
        else
            message = "connect failed: " .. (connect_err or "unknown")
        end
        sock:close()

        local latency_ms = math.floor((ngx.now() - start) * 1000)
        local state = origin_health.apply_check_result(origin, origin_health.read_state(health, origin), ok)
        state.status = status
        state.latency_ms = latency_ms
        state.message = message
        state.checked_at = ngx.time()
        origin_health.write_state(health, origin, state)

        local timer_ok, timer_err = ngx.timer.at(origin.health.interval, check_origin, origin)
        if not timer_ok then
            ngx.log(ngx.ERR, "failed to schedule origin health check for ", origin.name, ": ", timer_err)
        end
    end

    for _, origin in ipairs(origins) do
        if origin.health.enabled then
            health:set(origin.key .. ":healthy", 1)
            local ok, err = ngx.timer.at(0, check_origin, origin)
            if not ok then
                ngx.log(ngx.ERR, "failed to start origin health check for ", origin.name, ": ", err)
            end
        else
            origin_health.write_state(health, origin, origin_health.default_state(ngx.time()))
        end
    end
}

upstream tenant_origin_upstream {
    server 0.0.0.1 max_fails=1 fail_timeout=10s;
    balancer_by_lua_block {
        local balancer = require "ngx.balancer"
        local origin_balancer = require "origin_balancer"
        local origins = __ORIGINS_LUA__
        local health = ngx.shared.origin_health
        local desired_scheme = ngx.var.origin_scheme
        local tried = ngx.ctx.tried_origins or {}
        ngx.ctx.tried_origins = tried

        local index, origin = origin_balancer.select_retry_origin(origins, health, desired_scheme, tried, ngx.var.origin_index)

        if index == nil then
            return ngx.exit(ngx.HTTP_SERVICE_UNAVAILABLE)
        end

        tried[index] = true
        ngx.var.origin_index = tostring(index)
        ngx.var.origin_name = origin.name
        ngx.var.origin_host = origin.host_header
        ngx.var.origin_sni = origin.host
        ngx.var.origin_url = origin.url
        ngx.var.origin_request_uri = origin.request_uri_prefix .. ngx.var.request_uri
        local remaining = origin_balancer.count_remaining_retries(origins, health, desired_scheme, tried)
        if remaining > 0 then
            balancer.set_more_tries(remaining)
        end
        local peer_host = health:get(origin.key .. ":peer_host") or origin.host
        local ok, err = balancer.set_current_peer(peer_host, origin.port)
        if not ok then
            ngx.log(ngx.ERR, "failed to set origin peer ", origin.name, ": ", err)
            return ngx.exit(ngx.HTTP_SERVICE_UNAVAILABLE)
        end
    }
}

server {
    listen 80 default_server;
    server_name  _;
    __SERVER_NGINX_CONFIG__

    set $origin_index "";
    set $origin_name "";
    set $origin_host "";
    set $origin_sni "";
    set $origin_url "";
    set $origin_scheme "";
    set $origin_request_uri "";

    location / {
        access_log off;
        access_by_lua_block {
            local origin_balancer = require "origin_balancer"
            local resolver = require "resty.dns.resolver"
            local origins = __ORIGINS_LUA__
            local health = ngx.shared.origin_health

            local function resolve_origin_host(origin)
                if origin.host:match("^%d+%.%d+%.%d+%.%d+$") then
                    return origin.host
                end

                local nameservers = {}
                local resolv = io.open("/etc/resolv.conf", "r")
                if resolv then
                    for line in resolv:lines() do
                        local nameserver = line:match("^nameserver%s+([^%s]+)")
                        if nameserver then
                            table.insert(nameservers, nameserver)
                        end
                    end
                    resolv:close()
                end

                local r, resolver_err = resolver:new({ nameservers = nameservers, retrans = 2, timeout = 2000 })
                if not r then
                    return nil, "resolver init failed: " .. (resolver_err or "unknown")
                end

                local answers, query_err = r:query(origin.host, { qtype = r.TYPE_A })
                if not answers then
                    return nil, query_err or "unknown"
                end
                if answers.errcode then
                    return nil, answers.errstr or tostring(answers.errcode)
                end

                for _, answer in ipairs(answers) do
                    if answer.address then
                        return answer.address
                    end
                end
                return nil, "no A record"
            end

            local total_weight = origin_balancer.total_healthy_weight(origins, health)
            if total_weight == 0 then
                ngx.log(ngx.ERR, "all origins are unhealthy for tenant __TENANT_NAME__")
                return ngx.exit(ngx.HTTP_SERVICE_UNAVAILABLE)
            end
            for i, origin in ipairs(origins) do
                local resolved_host, resolve_err = resolve_origin_host(origin)
                if not resolved_host then
                    ngx.log(ngx.ERR, "failed to resolve origin ", origin.name, " host ", origin.host, ": ", resolve_err)
                    return ngx.exit(ngx.HTTP_SERVICE_UNAVAILABLE)
                end
                health:set(origin.key .. ":peer_host", resolved_host)
            end
            local cursor = (health:incr("rr_cursor", 1, 0) % total_weight) + 1
            local index, origin = origin_balancer.select_weighted_origin(origins, health, cursor)
            if index ~= nil then
                ngx.var.origin_index = tostring(index)
                ngx.var.origin_name = origin.name
                ngx.var.origin_host = origin.host_header
                ngx.var.origin_sni = origin.host
                ngx.var.origin_url = origin.url
                ngx.var.origin_scheme = origin.scheme
                ngx.var.origin_request_uri = origin.request_uri_prefix .. ngx.var.request_uri
                if origin.scheme == "https" then
                    return ngx.exec("@origin_https")
                end
                return ngx.exec("@origin_http")
            end
            return ngx.exit(ngx.HTTP_SERVICE_UNAVAILABLE)
        }
    }

    location @origin_http {
        access_log off;
        proxy_pass http://tenant_origin_upstream$origin_request_uri;
        proxy_set_header Host $origin_host;
        proxy_set_header X-Forwarded-Proto $origin_scheme;
        proxy_set_header X-Request-ID $request_id;
        set_real_ip_from  172.0.0.0/8;
        set_real_ip_from  10.0.0.0/8;
        real_ip_header    X-Forwarded-For;
        real_ip_recursive on;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_next_upstream error timeout http_500 http_502 http_503 http_504;
        proxy_next_upstream_tries __PROXY_NEXT_UPSTREAM_TRIES__;
        __LOCATION_NGINX_CONFIG__
    }

    location @origin_https {
        access_log off;
        proxy_pass https://tenant_origin_upstream$origin_request_uri;
        proxy_set_header Host $origin_host;
        proxy_set_header X-Forwarded-Proto $origin_scheme;
        proxy_set_header X-Request-ID $request_id;
        proxy_ssl_server_name on;
        proxy_ssl_name $origin_sni;
        set_real_ip_from  172.0.0.0/8;
        set_real_ip_from  10.0.0.0/8;
        real_ip_header    X-Forwarded-For;
        real_ip_recursive on;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_next_upstream error timeout http_500 http_502 http_503 http_504;
        proxy_next_upstream_tries __PROXY_NEXT_UPSTREAM_TRIES__;
        __LOCATION_NGINX_CONFIG__
    }
}
'''


JSON_ESCAPED_LOG_FORMAT = '''
log_format json_escaped escape=json
  '{'
    '"schema_version":"cdn_access_log_v1",'
    '"timestamp":"$time_iso8601",'
    '"pop_id":"__POP_ID__",'
    '"cdn_layer":"front",'
    '"accounting_source":true,'
    '"tenant":"__TENANT_NAME__",'
    '"host":"$host",'
    '"method":"$request_method",'
    '"request_path":"$uri",'
    '"bytes_sent":$body_bytes_sent,'
    '"request_duration_seconds":$request_time,'
    '"client_ip":"$remote_addr",'
    '"user_agent":"$http_user_agent",'
    '"cache_status":"$upstream_http_x_cwm_cache_status",'
    '"selected_origin":"unknown",'
    '"request_id":"$request_id",'
    '"time_local":"$time_local",'
    '"remote_addr":"$remote_addr",'
    '"request":"$request",'
    '"status":$status,'
    '"body_bytes_sent":$body_bytes_sent,'
    '"http_referer":"$http_referer",'
    '"http_user_agent":"$http_user_agent",'
    '"request_time":$request_time,'
    '"origin_name":"$origin_name",'
    '"origin_host":"$origin_host",'
    '"upstream_addr":"$upstream_addr",'
    '"upstream_status":"$upstream_status",'
    '"upstream_response_time":"$upstream_response_time"'
  '}';
'''



HTTP_HASH_CONFIG = '''
server_names_hash_bucket_size 128;
server_names_hash_max_size 4096;
lua_shared_dict cwmcdn_rate_limit 20m;
'''

CONFIG_PARSE_REGEX = re.compile(r'^([A-Z])(\d+)_(.+)$')
HTTP_FIELD_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
NGINX_SIZE_RE = re.compile(r"^(\d+)([kKmMgG])?$")
GLOB_PATH_RE = re.compile(r"^/[A-Za-z0-9._~/%:@+*,=-]*$")
REDIRECT_TARGET_RE = re.compile(r"^(?:/[A-Za-z0-9._~!&'()*+,=:@/%?-]*|https?://[A-Za-z0-9._~!&'()*+,=:@/%?-]+)$")
CIDR_RE = re.compile(r"^[0-9A-Fa-f:.]+/[0-9]{1,3}$")


def replace_keys(base, d):
    out = base
    for k, v in d.items():
        out = out.replace(k, v)
    return out


def validate_header_name(value):
    assert value == "" or HTTP_FIELD_NAME_RE.match(value), f"Invalid cache status header name: {value}"
    assert value.lower() not in ("set-cookie", "cookie", "authorization", "host"), f"Unsafe cache status header name: {value}"


def get_cache_config(env):
    status_header = env.get("CACHE_STATUS_HEADER", "X-CWM-Cache-Status")
    validate_header_name(status_header)
    edge_ttl = int(env.get("CACHE_EDGE_TTL_SECONDS", "3600"))
    assert 1 <= edge_ttl <= 30 * 24 * 60 * 60, "CACHE_EDGE_TTL_SECONDS must be between 1 and 2592000"
    mode = env.get("CACHE_MODE", "cache_everything")
    assert mode == "cache_everything", "CACHE_MODE must be cache_everything"
    return {
        "enabled": parse_bool(env.get("CACHE_ENABLED"), True),
        "mode": mode,
        "edge_ttl_seconds": edge_ttl,
        "respect_origin_cache_control": parse_bool(env.get("CACHE_RESPECT_ORIGIN_CACHE_CONTROL"), True),
        "status_header": status_header,
    }


def nginx_add_header(header, value):
    if not header:
        return ""
    validate_header_name(header)
    return f"add_header {header} {value} always;"


def get_cache_routing_config(cache_config, tenant_name):
    bypass_header = nginx_add_header(cache_config["status_header"], "BYPASS")
    if not cache_config["enabled"]:
        return replace_keys(CACHE_DISABLED_LOCATION_CONFIG_TEMPLATE, {
            "__TENANT_NAME__": tenant_name,
            "__CACHE_BYPASS_ADD_HEADER__": bypass_header,
        })
    return replace_keys(CACHE_ENABLED_LOCATION_CONFIG_TEMPLATE, {
        "__CDN_CACHE_ROUTER__": CDN_CACHE_ROUTER,
        "__TENANT_NAME__": tenant_name,
        "__CACHE_MODE__": cache_config["mode"],
        "__CACHE_EDGE_TTL_SECONDS__": str(cache_config["edge_ttl_seconds"]),
        "__CACHE_RESPECT_ORIGIN_CACHE_CONTROL__": "true" if cache_config["respect_origin_cache_control"] else "false",
        "__CACHE_STATUS_HEADER__": cache_config["status_header"] or '""',
        "__CACHE_STATUS_ADD_HEADER__": nginx_add_header(cache_config["status_header"], f"$upstream_http_{cache_config['status_header'].lower().replace('-', '_')}"),
        "__CACHE_BYPASS_ADD_HEADER__": bypass_header,
    })


def load_policy(env):
    if env.get("TENANT_POLICY_JSON"):
        return json.loads(env["TENANT_POLICY_JSON"])
    policy_path = env.get("CWM_CDN_POLICY_PATH", "/etc/cwm-cdn/policy.json")
    if policy_path and os.path.exists(policy_path):
        with open(policy_path) as f:
            return json.load(f)
    return {}


def glob_to_nginx_regex(value):
    assert isinstance(value, str) and value.startswith("/"), f"Invalid policy glob path: {value}"
    assert GLOB_PATH_RE.match(value), f"Invalid policy glob path: {value}"
    escaped = re.escape(value).replace(r"\*", ".*")
    return f"^{escaped}$"


def validate_redirect_target(target):
    assert isinstance(target, str), "Invalid redirect target"
    assert "\n" not in target and "\r" not in target, "Invalid redirect target"
    assert not target.startswith("//"), "Invalid redirect target"
    assert REDIRECT_TARGET_RE.match(target), "Invalid redirect target"
    assert not any(c in target for c in " $;{}"), "Invalid redirect target"


def append_query_preservation(target):
    if "?" in target:
        return f"{target}&$args"
    return f"{target}$is_args$args"


def policy_json_literal(policy):
    return lua_quote(json.dumps(policy, separators=(",", ":"), sort_keys=True))


def lua_policy_access_block(policy_literal):
    return f'''access_by_lua_block {{
            local cwm_policy = require "cwm_policy"
            local policy = cwm_policy.decode_policy({policy_literal})
            local ok, status = cwm_policy.enforce_access(policy, {{
                signing_key_path = os.getenv("CAPTCHA_SIGNING_KEY_PATH"),
            }})
            if not ok then
                return ngx.exit(status or ngx.HTTP_INTERNAL_SERVER_ERROR)
            end
        }}'''


def lua_policy_header_filter_block(policy_literal):
    return f'''header_filter_by_lua_block {{
            local cwm_policy = require "cwm_policy"
            local policy = cwm_policy.decode_policy({policy_literal})
            cwm_policy.apply_response_redirect(policy)
        }}'''


def lua_policy_body_filter_block():
    return '''body_filter_by_lua_block {
            local cwm_policy = require "cwm_policy"
            cwm_policy.clear_redirect_body()
        }'''


def validate_cidr(value):
    assert isinstance(value, str) and CIDR_RE.match(value), f"Invalid CIDR: {value}"
    ipaddress.ip_network(value)


def trusted_client_ip_enabled(env=None):
    source_env = env or os.environ
    return source_env.get("CWM_CDN_TRUSTED_CLIENT_IP_ENABLED", source_env.get("TRUSTED_CLIENT_IP_ENABLED", "")).lower() in ("1", "true", "yes")


def validate_no_raw_policy_keys(value, context="policy"):
    if isinstance(value, dict):
        for key, child in value.items():
            lower_key = key.lower()
            assert not lower_key.endswith(("config", "snippet")) and "nginx" not in lower_key and "lua" not in lower_key and lower_key not in ("include", "file"), f"Raw config field is not allowed in {context}: {key}"
            validate_no_raw_policy_keys(child, f"{context}.{key}")
    elif isinstance(value, list):
        for i, child in enumerate(value):
            validate_no_raw_policy_keys(child, f"{context}[{i}]")


def optional_policy_list(value, context):
    if value is None:
        return []
    assert isinstance(value, list), f"Invalid {context}: expected list"
    return value


def get_policy_configs(policy, env=None):
    if not policy:
        return "", "", ""
    source_env = env or os.environ
    validate_no_raw_policy_keys(policy)
    policy_literal = policy_json_literal(policy)
    server_lines = []
    location_lines = []
    extra_locations = []
    access_runtime_required = False
    response_redirect_required = False
    security = policy.get("security") or {}
    request = security.get("request") or {}
    if request.get("maxBodySize"):
        assert NGINX_SIZE_RE.match(str(request["maxBodySize"])), "Invalid security.request.maxBodySize"
        server_lines.append(f"client_max_body_size {request['maxBodySize']};")
    ip_access = security.get("ipAccess") or {}
    if (ip_access.get("allowCidrs") or ip_access.get("blockCidrs")) and not trusted_client_ip_enabled(source_env):
        raise AssertionError("security.ipAccess requires trusted client IP support to be enabled")
    for cidr in optional_policy_list(ip_access.get("blockCidrs"), "security.ipAccess.blockCidrs"):
        validate_cidr(cidr)
        location_lines.append(f"deny {cidr};")
    if ip_access.get("allowCidrs"):
        for cidr in optional_policy_list(ip_access.get("allowCidrs"), "security.ipAccess.allowCidrs"):
            validate_cidr(cidr)
            location_lines.append(f"allow {cidr};")
        location_lines.append("deny all;")
    methods = security.get("methods") or {}
    for method in optional_policy_list(methods.get("block"), "security.methods.block"):
        assert re.match(r"^[A-Z]+$", method), f"Invalid blocked method: {method}"
        location_lines.append(f"if ($request_method = {method}) {{ return 403; }}")
    if methods.get("allow"):
        allowed = "|".join(re.escape(method) for method in methods["allow"])
        location_lines.append(f"if ($request_method !~ ^({allowed})$) {{ return 403; }}")
    urls = security.get("urls") or {}
    for rule in optional_policy_list(urls.get("block"), "security.urls.block"):
        match = rule.get("match") or {}
        assert match.get("type") == "glob", "Only glob URL block rules are supported"
        location_lines.append(f"if ($uri ~ {glob_to_nginx_regex(match.get('path'))}) {{ return 403; }}")
    rate_limit = security.get("rateLimit") or {}
    if rate_limit.get("enabled"):
        assert isinstance(rate_limit.get("requests"), int) and rate_limit["requests"] >= 1, "Invalid security.rateLimit.requests"
        assert int(rate_limit.get("burst", 0)) >= 0, "Invalid security.rateLimit.burst"
        assert rate_limit.get("key") in (None, "", "clientIp"), "Only clientIp rate limiting is supported"
        assert trusted_client_ip_enabled(source_env), "security.rateLimit.key=clientIp requires trusted client IP support to be enabled"
        assert rate_limit.get("action", "block") in ("block", "captcha"), "Invalid security.rateLimit.action"
        access_runtime_required = True
    captcha = policy.get("captcha") or {}
    if rate_limit.get("enabled") and rate_limit.get("action", "block") == "captcha":
        assert captcha.get("enabled"), "security.rateLimit.action=captcha requires captcha.enabled=true"
    if captcha.get("enabled"):
        assert captcha.get("provider") == "turnstile", "Only turnstile captcha provider is supported"
        assert captcha.get("siteKey"), "captcha.siteKey is required"
        extra_locations.append(replace_keys(CAPTCHA_RUNTIME_LOCATION_TEMPLATE, {
            "__POLICY_JSON__": policy_literal,
            "__CAPTCHA_SIGNING_KEY_PATH_JSON__": json.dumps(source_env.get("CAPTCHA_SIGNING_KEY_PATH", "")),
        }))
        access_runtime_required = True
        for rule in optional_policy_list(captcha.get("rules"), "captcha.rules"):
            match = rule.get("match") or {}
            assert match.get("type") == "glob", "Only glob captcha rules are supported"
            glob_to_nginx_regex(match.get('path'))
    for redirect in optional_policy_list(policy.get("redirects"), "redirects"):
        if redirect.get("enabled", True) is False:
            continue
        when = redirect.get("when") or {}
        path = when.get("path") or {}
        assert not (when.get("originStatus") and when.get("upstreamStatus")), "originStatus and upstreamStatus are mutually exclusive"
        target = redirect.get("to")
        validate_redirect_target(target)
        status = int(redirect.get("status", 302))
        assert status in (301, 302, 307, 308), "Invalid redirect status"
        for response_status in [*(when.get("originStatus") or []), *(when.get("upstreamStatus") or [])]:
            assert isinstance(response_status, int) and 100 <= response_status <= 599, "Invalid redirect response status"
        if when.get("originStatus") or when.get("upstreamStatus"):
            if path:
                assert path.get("type") == "glob", "Only path glob redirects are supported"
                glob_to_nginx_regex(path.get('value'))
            response_redirect_required = True
        else:
            assert path.get("type") == "glob", "Only path glob redirects are supported"
            assert "*" in path.get("value", "") or target.split("?", 1)[0] != path.get("value"), "Direct self-redirects are not allowed"
            if redirect.get("preserveQuery"):
                target = append_query_preservation(target)
            location_lines.append(f"if ($uri ~ {glob_to_nginx_regex(path.get('value'))}) {{ return {status} {target}; }}")
    if access_runtime_required:
        location_lines.append(lua_policy_access_block(policy_literal))
    if response_redirect_required:
        location_lines.append(lua_policy_header_filter_block(policy_literal))
        location_lines.append(lua_policy_body_filter_block())
    return "\n    ".join(server_lines), "\n        ".join(location_lines), "\n".join(extra_locations)


def tls_protocols(min_version, max_version):
    assert min_version in TLS_VERSIONS, f"Unsupported TLS minVersion: {min_version}"
    assert max_version in TLS_VERSIONS, f"Unsupported TLS maxVersion: {max_version}"
    assert TLS_VERSIONS.index(min_version) <= TLS_VERSIONS.index(max_version), "TLS minVersion cannot be greater than maxVersion"
    return " ".join(TLS_VERSIONS[TLS_VERSIONS.index(min_version):TLS_VERSIONS.index(max_version) + 1])


def parse_configs(env):
    domains, origins = {}, {}
    for k, v in env.items():
        match = re.match(CONFIG_PARSE_REGEX, k)
        if match:
            d = None
            if match.group(1) == "D":
                d = domains
            elif match.group(1) == "O":
                d = origins
            if d is not None:
                d.setdefault(match.group(2), {})[match.group(3).upper()] = v
    return list(domains.values()), list(origins.values())


def get_domain_server_config(i, domain, certs_path, tenant_name, access_log_config, cache_config=None, policy_configs=None):
    domain = deepcopy(domain)
    assert "NAME" in domain, "NAME must be set in all domain configurations"
    name = domain.pop("NAME")
    tls_mode = domain.pop("TLS_MODE", "provided")
    tls_min_version = domain.pop("TLS_MIN_VERSION", "TLSv1.2")
    tls_max_version = domain.pop("TLS_MAX_VERSION", "TLSv1.3")
    redirect_http_to_https = parse_bool(domain.pop("REDIRECT_HTTP_TO_HTTPS", "false"))
    assert tls_mode in ("provided", "letsencrypt"), f"Unsupported TLS mode: {tls_mode}"
    protocols = tls_protocols(tls_min_version, tls_max_version)
    if tls_mode == "provided":
        assert "CERT" in domain and "KEY" in domain, "CERT and KEY must be set for provided TLS domain configurations"
        cert, key = domain.pop("CERT"), domain.pop("KEY")
        cert_path = os.path.join(certs_path, f"tls{i}.crt")
        key_path = os.path.join(certs_path, f"tls{i}.key")
        with open(cert_path, "w") as f:
            f.write(cert)
        os.chmod(cert_path, 0o600)
        with open(key_path, "w") as f:
            f.write(key)
        os.chmod(key_path, 0o600)
    else:
        cert_path = domain.pop("CERT_PATH", os.path.join(certs_path, "letsencrypt", str(i), "tls.crt"))
        key_path = domain.pop("KEY_PATH", os.path.join(certs_path, "letsencrypt", str(i), "tls.key"))
    server_nginx_config = ""
    location_nginx_config = ""
    policy_server_config, policy_location_config, policy_extra_locations = policy_configs or ("", "", "")
    if policy_server_config:
        server_nginx_config = policy_server_config
    assert len(domain) == 0, f"Unknown domain configuration keys: {', '.join(domain.keys())}"
    server_config = DOMAIN_CONF_TEMPLATE
    server_config = replace_keys(server_config, {
        "__SERVER_NAME__": name,
        "__CERT_PATH__": cert_path,
        "__KEY_PATH__": key_path,
        "__TLS_PROTOCOLS__": protocols,
        "__TENANT_NAME__": tenant_name,
        "__SERVER_NGINX_CONFIG__": server_nginx_config,
        "__EXTRA_LOCATION_NGINX_CONFIG__": policy_extra_locations,
        "__POLICY_LOCATION_NGINX_CONFIG__": policy_location_config,
        "__CACHE_ROUTING_NGINX_CONFIG__": get_cache_routing_config(cache_config or get_cache_config({}), tenant_name),
        "__LOCATION_NGINX_CONFIG__": location_nginx_config,
        "__CDN_CACHE_ROUTER__": CDN_CACHE_ROUTER,
        "__ACCESS_LOG_CONFIG__": access_log_config if access_log_config else '',
    })
    if redirect_http_to_https:
        http_location_config = "return 308 https://$host$request_uri;"
    else:
        http_location_config = replace_keys(DOMAIN_HTTP_PROXY_LOCATION_CONFIG_TEMPLATE, {
            "__TENANT_NAME__": tenant_name,
            "__CDN_CACHE_ROUTER__": CDN_CACHE_ROUTER,
            "__ACCESS_LOG_CONFIG__": access_log_config if access_log_config else '',
            "__POLICY_LOCATION_NGINX_CONFIG__": policy_location_config,
            "__CACHE_ROUTING_NGINX_CONFIG__": get_cache_routing_config(cache_config or get_cache_config({}), tenant_name),
        }).strip()
    http_server_config = replace_keys(DOMAIN_HTTP_CONF_TEMPLATE, {
        "__SERVER_NAME__": name,
        "__SERVER_NGINX_CONFIG__": server_nginx_config,
        "__EXTRA_LOCATION_NGINX_CONFIG__": policy_extra_locations,
        "__ACME_CHALLENGE_ROOT__": ACME_CHALLENGE_ROOT,
        "__HTTP_LOCATION_CONFIG__": http_location_config,
    })
    return "\n".join([server_config, http_server_config])


def get_domains_server_configs(domains, certs_path, tenant_name, access_log_config, cache_config=None, policy_configs=None, pop_id="unknown"):
    server_configs = [replace_keys(JSON_ESCAPED_LOG_FORMAT, {"__TENANT_NAME__": tenant_name, "__POP_ID__": pop_id})]
    os.makedirs(certs_path, exist_ok=True)
    for i, domain in enumerate(domains):
        server_configs.append(get_domain_server_config(i, domain, certs_path, tenant_name, access_log_config, cache_config, policy_configs))
    return server_configs


def get_url_host_scheme(url):
    parsed = parse_origin_url(url)
    host, scheme = parsed["host"], parsed["scheme"]
    assert host, f'invalid URL: {url}'
    return host, scheme


def parse_bool(value, default=True):
    if value is None or value == "":
        return default
    return str(value).lower() in ("1", "true", "yes", "on")


def parse_int(value, default, minimum=None, maximum=None):
    if value is None or value == "":
        value = default
    try:
        value = int(value)
    except Exception as e:
        raise AssertionError(f"invalid integer value: {value}") from e
    if minimum is not None and value < minimum:
        raise AssertionError(f"integer value must be >= {minimum}: {value}")
    if maximum is not None and value > maximum:
        raise AssertionError(f"integer value must be <= {maximum}: {value}")
    return value


def parse_duration_seconds(value, default):
    if value is None or value == "":
        value = default
    value = str(value)
    multipliers = {"ms": 0.001, "s": 1, "m": 60, "h": 3600}
    for suffix, multiplier in multipliers.items():
        if value.endswith(suffix):
            number = value[:-len(suffix)]
            break
    else:
        number, multiplier = value, 1
    try:
        seconds = float(number) * multiplier
    except Exception as e:
        raise AssertionError(f"invalid duration value: {value}") from e
    if seconds <= 0:
        raise AssertionError(f"duration value must be positive: {value}")
    return seconds

def lua_quote(value):
    return json.dumps(str(value))


def parse_origin_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise Exception(f'invalid URL: {url}')
    request_uri_prefix = parsed.path or ""
    if request_uri_prefix == "/":
        request_uri_prefix = ""
    else:
        request_uri_prefix = request_uri_prefix.rstrip("/")
    return {
        "url": url,
        "scheme": parsed.scheme,
        "host": parsed.hostname,
        "host_header": parsed.netloc,
        "port": parsed.port or (443 if parsed.scheme == "https" else 80),
        "path": parsed.path or "",
        "request_uri_prefix": request_uri_prefix,
    }


def normalize_origin(origin, index, total_origins):
    origin = deepcopy(origin)
    assert "URL" in origin, "URL must be set in the origin configuration"
    url = origin.pop("URL")
    parsed_url = parse_origin_url(url)
    if total_origins > 1 and parsed_url["path"] not in ("", "/"):
        raise AssertionError("Path-prefixed origin URLs are not supported with multiple origins")
    name = origin.pop("NAME", f"origin-{index}") or f"origin-{index}"
    weight = parse_int(origin.pop("WEIGHT", 1), 1, minimum=1)
    health = {
        "enabled": parse_bool(origin.pop("HEALTHCHECK_ENABLED", True), True),
        "path": origin.pop("HEALTHCHECK_PATH", "/"),
        "expected_status": parse_int(origin.pop("HEALTHCHECK_EXPECTEDSTATUS", 200), 200, minimum=100, maximum=599),
        "interval": parse_duration_seconds(origin.pop("HEALTHCHECK_INTERVAL", "10s"), "10s"),
        "timeout": parse_duration_seconds(origin.pop("HEALTHCHECK_TIMEOUT", "2s"), "2s"),
        "healthy_threshold": parse_int(origin.pop("HEALTHCHECK_HEALTHYTHRESHOLD", 2), 2, minimum=1),
        "unhealthy_threshold": parse_int(origin.pop("HEALTHCHECK_UNHEALTHYTHRESHOLD", 3), 3, minimum=1),
    }
    assert health["path"].startswith("/"), "Health check path must start with /"
    # TODO: pop other configs here and add to server/location nginx configs
    assert len(origin) == 0, f"Unknown origin configuration keys: {', '.join(origin.keys())}"
    return {
        **parsed_url,
        "key": f"origin_{index}",
        "name": name,
        "weight": weight,
        "health": health,
    }


def origins_to_lua(origins):
    lua_origins = []
    for origin in origins:
        health = origin["health"]
        lua_origins.append(
            "{"
            f"key={lua_quote(origin['key'])},"
            f"name={lua_quote(origin['name'])},"
            f"url={lua_quote(origin['url'])},"
            f"scheme={lua_quote(origin['scheme'])},"
            f"host={lua_quote(origin['host'])},"
            f"host_header={lua_quote(origin['host_header'])},"
            f"port={origin['port']},"
            f"request_uri_prefix={lua_quote(origin['request_uri_prefix'])},"
            f"weight={origin['weight']},"
            "health={"
            f"enabled={'true' if health['enabled'] else 'false'},"
            f"path={lua_quote(health['path'])},"
            f"expected_status={health['expected_status']},"
            f"interval={health['interval']},"
            f"timeout={health['timeout']},"
            f"healthy_threshold={health['healthy_threshold']},"
            f"unhealthy_threshold={health['unhealthy_threshold']}"
            "}"
            "}"
        )
    return "{" + ",".join(lua_origins) + "}"


def get_origin_server_config(origins, tenant_name, nginx_resolver_config=""):
    normalized_origins = [normalize_origin(origin, i, len(origins)) for i, origin in enumerate(origins)]
    location_nginx_config = ""
    server_nginx_config = ""
    server_config = ORIGINS_CONF_TEMPLATE
    server_config = replace_keys(server_config, {
        "__TENANT_NAME__": tenant_name,
        "__ORIGINS_LUA__": origins_to_lua(normalized_origins),
        "__PROXY_NEXT_UPSTREAM_TRIES__": str(max(len(normalized_origins), 1)),
        "__NGINX_RESOLVER_CONFIG__": nginx_resolver_config,
        "__LOCATION_NGINX_CONFIG__": location_nginx_config,
        "__SERVER_NGINX_CONFIG__": server_nginx_config,
    })
    return server_config


def get_metrics_server_config():
    return '''log_by_lua_block {
    dofile("/etc/nginx/metrics_log.lua")
}

include /etc/nginx/metrics_server.conf;'''


def get_default_conf(certs_path, env):
    tenant_name = env["TENANT_NAME"]
    access_logs_enabled = (env.get("ENABLE_TENANT_ACCESS_LOGS") in ("1", "true", "yes") or env.get("ENABLE_PLATFORM_LOGS") in ("1", "true", "yes"))
    domain_access_log_path = "/var/log/nginx/access.logjson" if access_logs_enabled else ""
    if domain_access_log_path:
        domain_access_log_config = f'access_log {domain_access_log_path} json_escaped;'
    else:
        domain_access_log_config = 'access_log off;'
    domains, origins = parse_configs(env)
    assert len(domains) > 0, "At least one domain configuration is required"
    cache_config = get_cache_config(env)
    policy_configs = get_policy_configs(load_policy(env), env)
    pop_id = env.get("POP_ID", env.get("CWM_CDN_POP_ID", "unknown"))
    assert len(origins) >= 1, "At least one origin configuration is required"
    return "\n".join([
        LUA_SSL_CONFIG,
        HTTP_HASH_CONFIG,
        *get_domains_server_configs(domains, certs_path, tenant_name, domain_access_log_config, cache_config, policy_configs, pop_id),
        get_origin_server_config(origins, tenant_name, env.get(
            "NGINX_RESOLVER_CONFIG",
            "resolver 8.8.8.8 ipv6=off;"
        )),
        get_metrics_server_config()
    ])


def main(nginx_conf_path="/etc/nginx", certs_path="/certs", env=None):
    default_conf = get_default_conf(certs_path, env or os.environ)
    print(default_conf)
    with open(os.path.join(nginx_conf_path, "conf.d/default.conf"), "w") as f:
        f.write(default_conf)


if __name__ == "__main__":
    main()
