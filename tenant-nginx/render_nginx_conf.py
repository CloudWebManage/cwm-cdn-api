#!/usr/bin/python3
import os
import re
import json
from copy import deepcopy
from urllib.parse import urlsplit


CDN_CACHE_ROUTER = os.getenv("CDN_CACHE_ROUTER", "http://router.cdn-cache")
ACME_CHALLENGE_ROOT = os.getenv("ACME_CHALLENGE_ROOT", "/var/lib/cwm-cdn/acme-challenges")
TLS_VERSIONS = ("TLSv1.2", "TLSv1.3")


DOMAIN_CONF_TEMPLATE = '''
server {
    listen 443 ssl;
    server_name  __SERVER_NAME__;
    ssl_certificate __CERT_PATH__;
    ssl_certificate_key __KEY_PATH__;
    ssl_protocols __TLS_PROTOCOLS__;
    __SERVER_NGINX_CONFIG__
    location / {
        __ACCESS_LOG_CONFIG__
        proxy_pass __CDN_CACHE_ROUTER__;
        proxy_set_header X-CWMCDN-Tenant-Name __TENANT_NAME__;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        __LOCATION_NGINX_CONFIG__
        
        if ($request_method !~ ^(GET|HEAD)$ ) {
            proxy_pass http://127.0.0.1:80;
        }
    }
}
'''


DOMAIN_HTTP_CONF_TEMPLATE = '''
server {
    listen 80;
    server_name  __SERVER_NAME__;
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
        proxy_pass __CDN_CACHE_ROUTER__;
        proxy_set_header X-CWMCDN-Tenant-Name __TENANT_NAME__;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto http;
'''


ORIGINS_CONF_TEMPLATE = '''
lua_shared_dict origin_health 10m;

init_worker_by_lua_block {
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
        local ok, err = balancer.set_current_peer(origin.host, origin.port)
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
            local origins = __ORIGINS_LUA__
            local health = ngx.shared.origin_health
            local total_weight = origin_balancer.total_healthy_weight(origins, health)
            if total_weight == 0 then
                ngx.log(ngx.ERR, "all origins are unhealthy for tenant __TENANT_NAME__")
                return ngx.exit(ngx.HTTP_SERVICE_UNAVAILABLE)
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
'''

CONFIG_PARSE_REGEX = re.compile(r'^([A-Z])(\d+)_(.+)$')


def replace_keys(base, d):
    out = base
    for k, v in d.items():
        out = out.replace(k, v)
    return out


def parse_bool(value):
    return str(value).lower() in ("1", "true", "yes", "on")


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


def get_domain_server_config(i, domain, certs_path, tenant_name, access_log_config):
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
    # TODO: pop other configs here and add to server/location nginx configs
    assert len(domain) == 0, f"Unknown domain configuration keys: {', '.join(domain.keys())}"
    server_config = DOMAIN_CONF_TEMPLATE
    server_config = replace_keys(server_config, {
        "__SERVER_NAME__": name,
        "__CERT_PATH__": cert_path,
        "__KEY_PATH__": key_path,
        "__TLS_PROTOCOLS__": protocols,
        "__TENANT_NAME__": tenant_name,
        "__SERVER_NGINX_CONFIG__": server_nginx_config,
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
        }).strip()
    http_server_config = replace_keys(DOMAIN_HTTP_CONF_TEMPLATE, {
        "__SERVER_NAME__": name,
        "__ACME_CHALLENGE_ROOT__": ACME_CHALLENGE_ROOT,
        "__HTTP_LOCATION_CONFIG__": http_location_config,
    })
    return "\n".join([server_config, http_server_config])


def get_domains_server_configs(domains, certs_path, tenant_name, access_log_config):
    server_configs = [JSON_ESCAPED_LOG_FORMAT]
    os.makedirs(certs_path, exist_ok=True)
    for i, domain in enumerate(domains):
        server_configs.append(get_domain_server_config(i, domain, certs_path, tenant_name, access_log_config))
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


def get_origin_server_config(origins, tenant_name):
    normalized_origins = [normalize_origin(origin, i, len(origins)) for i, origin in enumerate(origins)]
    location_nginx_config = ""
    server_nginx_config = ""
    server_config = ORIGINS_CONF_TEMPLATE
    server_config = replace_keys(server_config, {
        "__TENANT_NAME__": tenant_name,
        "__ORIGINS_LUA__": origins_to_lua(normalized_origins),
        "__PROXY_NEXT_UPSTREAM_TRIES__": str(max(len(normalized_origins), 1)),
        "__LOCATION_NGINX_CONFIG__": location_nginx_config,
        "__SERVER_NGINX_CONFIG__": server_nginx_config,
    })
    return server_config


def get_metrics_server_config():
    return 'include /etc/nginx/metrics.conf;'


def get_default_conf(certs_path, env):
    tenant_name = env["TENANT_NAME"]
    domain_access_log_path = "/var/log/nginx/access.logjson" if env.get("ENABLE_TENANT_ACCESS_LOGS") in ("1", "true", "yes") else ""
    if domain_access_log_path:
        domain_access_log_config = f'access_log {domain_access_log_path} json_escaped;'
    else:
        domain_access_log_config = 'access_log off;'
    domains, origins = parse_configs(env)
    assert len(domains) > 0, "At least one domain configuration is required"
    assert len(origins) >= 1, "At least one origin configuration is required"
    return "\n".join([
        HTTP_HASH_CONFIG,
        *get_domains_server_configs(domains, certs_path, tenant_name, domain_access_log_config),
        get_origin_server_config(origins, tenant_name),
        get_metrics_server_config()
    ])


def main(nginx_conf_path="/etc/nginx", certs_path="/certs", env=None):
    with open(os.path.join(nginx_conf_path, "conf.d/default.conf"), "w") as f:
        f.write(get_default_conf(certs_path, env or os.environ))


if __name__ == "__main__":
    main()
