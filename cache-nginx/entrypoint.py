#!/usr/bin/python3
import os


DEFAULT_TENANT_PROXY_PASS_DOMAIN = 'tenant.$http_x_cwmcdn_tenant_name.svc.cluster.local'
DEFAULT_NGINX_RESOLVER_CONFIG = '''
resolver 169.254.20.10 valid=30s;
resolver_timeout 5s;
'''

DEFAULT_CONF_ROUTER_TEMPLATE = '''
upstream cache {
  hash $http_x_cwmcdn_tenant_name$request_uri consistent;
__NGINX_UPSTREAM_CACHE_SERVERS__
}

__NGINX_HTTP_CONFIGS__

server {
    listen       80;
    server_name  _;
    __NGINX_SERVER_CONFIGS__
    location / {
        proxy_pass http://cache$request_uri;
        __NGINX_LOCATION_CONFIGS__
    }
}
'''

DEFAULT_CONF_CACHE_TEMPLATE = '''
__NGINX_RESOLVER_CONFIG__

__NGINX_HTTP_CONFIGS__

__PURGE_RUNTIME_HTTP_CONFIG__

__PURGE_RUNTIME_ADMIN_SERVER_CONFIG__

server {
    listen       80;
    server_name  _;
    __NGINX_SERVER_CONFIGS__
    __PURGE_RUNTIME_SERVER_CONFIG__
    location / {
        set $cwmcdn_purge_token "";
        __PURGE_RUNTIME_LOCATION_CONFIG__
        proxy_pass http://__TENANT_PROXY_PASS_DOMAIN__$request_uri;
        __NGINX_LOCATION_CONFIGS__
    }
}
'''


PURGE_RUNTIME_HTTP_CONFIG_TEMPLATE = r'''
lua_shared_dict cwmcdn_purge 20m;
init_worker_by_lua_block {
  local path = __PURGE_INDEX_PATH_JSON__
  local dict = ngx.shared.cwmcdn_purge
  local file = io.open(path, "r")
  if file then
    for line in file:lines() do
      local tenant, selector_type, selector, token = line:match("^([^\t]+)\t([^\t]+)\t([^\t]*)\t([^\t]+)$")
      if tenant and selector_type and token then
        dict:set(tenant .. "|" .. selector_type .. "|" .. selector, token)
      end
    end
    file:close()
  end
}
'''


PURGE_RUNTIME_ADMIN_SERVER_CONFIG_TEMPLATE = r'''
server {
    listen       __CACHE_ADMIN_PORT__;
    server_name  _;
    location = /internal/purge {
        allow 127.0.0.1;
        allow 10.0.0.0/8;
        allow 172.16.0.0/12;
        allow 192.168.0.0/16;
        deny all;
        content_by_lua_block {
          local cjson = require "cjson.safe"
          ngx.req.read_body()
          local token = __CACHE_ADMIN_TOKEN_JSON__
          if token == "" then
            ngx.status = 503
            ngx.say('{"success":false,"message":"cache admin token is not configured"}')
            return
          end
          if ngx.var.http_authorization ~= "Bearer " .. token then
            ngx.status = 401
            ngx.say('{"success":false,"message":"unauthorized"}')
            return
          end
          local body = ngx.req.get_body_data() or "{}"
          local payload, err = cjson.decode(body)
          if not payload then
            ngx.status = 400
            ngx.say('{"success":false,"message":"invalid json"}')
            return
          end
          local tenant = payload.tenant
          if type(tenant) ~= "string" or tenant == "" or tenant:find("[^%w%-]") then
            ngx.status = 400
            ngx.say('{"success":false,"message":"invalid tenant"}')
            return
          end
          local now_token = tostring(ngx.now())
          local dict = ngx.shared.cwmcdn_purge
          local index_path = __PURGE_INDEX_PATH_JSON__
          local file, file_err = io.open(index_path, "a")
          if not file then
            ngx.status = 503
            ngx.say(cjson.encode({success=false, message="purge index unavailable: " .. tostring(file_err)}))
            return
          end
          local applied = 0
          local function apply(selector_type, selector)
            local key = tenant .. "|" .. selector_type .. "|" .. selector
            dict:set(key, now_token)
            local ok, write_err = file:write(tenant .. "\t" .. selector_type .. "\t" .. selector .. "\t" .. now_token .. "\n")
            if not ok then
              file:close()
              ngx.status = 503
              ngx.say(cjson.encode({success=false, message="purge index write failed: " .. tostring(write_err)}))
              return false
            end
            applied = applied + 1
            return true
          end
          if payload.scope == "everything" then
            if not apply("everything", "") then return end
          else
            local selectors = payload.selectors or {}
            if type(selectors) ~= "table" or #selectors > 100 then
              file:close()
              ngx.status = 400
              ngx.say('{"success":false,"message":"invalid selectors"}')
              return
            end
            for _, selector in ipairs(selectors) do
              if selector.type == "path" then
                if not apply("path", selector.path .. (selector.query and ("?" .. selector.query) or "")) then return end
              elseif selector.type == "prefix" then
                if not apply("prefix", selector.path) then return end
              else
                file:close()
                ngx.status = 400
                ngx.say('{"success":false,"message":"invalid selector type"}')
                return
              end
            end
          end
          local flush_ok, flush_err = file:flush()
          if not flush_ok then
            file:close()
            ngx.status = 503
            ngx.say(cjson.encode({success=false, message="purge index flush failed: " .. tostring(flush_err)}))
            return
          end
          file:close()
          ngx.header.content_type = "application/json"
          ngx.say(cjson.encode({success=true, selectorsApplied=applied}))
        }
    }
}
'''


PURGE_RUNTIME_LOCATION_CONFIG = r'''
        access_by_lua_block {
          local dict = ngx.shared.cwmcdn_purge
          local tenant = ngx.var.http_x_cwmcdn_tenant_name or ""
          local uri = ngx.var.uri or "/"
          local args = ngx.var.args
          local full = uri .. (args and ("?" .. args) or "")
          local token = ""
          local token_number = 0
          local function select_newer_token(candidate)
            local candidate_number = tonumber(candidate or "") or 0
            if candidate_number > token_number then
              token = candidate
              token_number = candidate_number
            end
          end
          select_newer_token(dict:get(tenant .. "|everything|"))
          select_newer_token(dict:get(tenant .. "|path|" .. full))
          -- Prefix selectors are path-only and are required by API validation to end in /.
          local keys = dict:get_keys(0)
          for _, key in ipairs(keys) do
            local prefix = key:match("^" .. tenant:gsub("%-", "%%-") .. "|prefix|(.+)$")
            if prefix and uri:sub(1, #prefix) == prefix then
              select_newer_token(dict:get(key))
            end
          end
          ngx.var.cwmcdn_purge_token = token
        }
        header_filter_by_lua_block {
          local header_name = ngx.var.http_x_cwmcdn_cache_status_header or ""
          if header_name ~= "" and header_name:match("^[!#$%%&'*+.^_`|~0-9A-Za-z-]+$") then
            local lower = header_name:lower()
            local blocked = {
              ["authorization"] = true,
              ["cookie"] = true,
              ["set-cookie"] = true,
              ["host"] = true,
              ["connection"] = true,
              ["upgrade"] = true,
              ["keep-alive"] = true,
              ["transfer-encoding"] = true,
              ["te"] = true,
              ["trailer"] = true,
              ["proxy-authenticate"] = true,
              ["proxy-authorization"] = true,
            }
            if not blocked[lower] then
              ngx.header[header_name] = ngx.var.upstream_cache_status or "BYPASS"
            end
          end
        }
'''


def replace_keys(base, d):
    out = base
    for k, v in d.items():
        out = out.replace(k, v)
    return out


def lua_json_string(value):
    import json
    return json.dumps(str(value or ""))


def get_common_replace_keys(env):
    return {
        "__NGINX_HTTP_CONFIGS__": env.get('NGINX_HTTP_CONFIGS') or "",
        "__NGINX_SERVER_CONFIGS__": env.get('NGINX_SERVER_CONFIGS') or "",
        "__NGINX_LOCATION_CONFIGS__": env.get('NGINX_LOCATION_CONFIGS') or "",
    }


def get_router_default_conf(env):
    return replace_keys(DEFAULT_CONF_ROUTER_TEMPLATE, {
        **get_common_replace_keys(env),
        "__NGINX_UPSTREAM_CACHE_SERVERS__": env['NGINX_UPSTREAM_CACHE_SERVERS']
    })


def get_cache_default_conf(env):
    purge_runtime_enabled = (env.get('ENABLE_PURGE_RUNTIME') or '').lower() in ('1', 'true', 'yes')
    purge_index_path = env.get('CWMCDN_PURGE_INDEX_PATH') or "/var/cache/nginx/cwmcdn-purge-index.log"
    cache_admin_port = str(env.get('CWMCDN_CACHE_ADMIN_PORT') or env.get('CACHE_ADMIN_PORT') or "8081")
    cache_admin_token = env.get('CWMCDN_CACHE_ADMIN_TOKEN') or ""
    purge_runtime_http_config = replace_keys(PURGE_RUNTIME_HTTP_CONFIG_TEMPLATE, {
        "__PURGE_INDEX_PATH_JSON__": lua_json_string(purge_index_path),
    })
    purge_runtime_admin_server_config = replace_keys(PURGE_RUNTIME_ADMIN_SERVER_CONFIG_TEMPLATE, {
        "__CACHE_ADMIN_PORT__": cache_admin_port,
        "__CACHE_ADMIN_TOKEN_JSON__": lua_json_string(cache_admin_token),
        "__PURGE_INDEX_PATH_JSON__": lua_json_string(purge_index_path),
    })
    return replace_keys(DEFAULT_CONF_CACHE_TEMPLATE, {
        **get_common_replace_keys(env),
        "__NGINX_RESOLVER_CONFIG__": env.get('NGINX_RESOLVER_CONFIG') or DEFAULT_NGINX_RESOLVER_CONFIG,
        "__TENANT_PROXY_PASS_DOMAIN__": env.get('TENANT_PROXY_PASS_DOMAIN') or DEFAULT_TENANT_PROXY_PASS_DOMAIN,
        "__PURGE_RUNTIME_HTTP_CONFIG__": purge_runtime_http_config if purge_runtime_enabled else "",
        "__PURGE_RUNTIME_ADMIN_SERVER_CONFIG__": purge_runtime_admin_server_config if purge_runtime_enabled else "",
        "__PURGE_RUNTIME_SERVER_CONFIG__": "",
        "__PURGE_RUNTIME_LOCATION_CONFIG__": PURGE_RUNTIME_LOCATION_CONFIG if purge_runtime_enabled else "",
    })


def get_default_conf(env):
    if env['TYPE'] == 'router':
        return get_router_default_conf(env)
    elif env['TYPE'] == 'cache':
        return get_cache_default_conf(env)
    else:
        raise Exception(f'Unknown TYPE: {env["TYPE"]}')


def main(nginx_conf_path="/etc/nginx", env=None):
    with open(os.path.join(nginx_conf_path, "conf.d/default.conf"), "w") as f:
        f.write(get_default_conf(env or os.environ))


if __name__ == "__main__":
    main()
