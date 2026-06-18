local M = {}

local CAPTCHA_COOKIE = "cwmcdn_captcha"
local TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
local policy_cache = {}

local function duration_seconds(value, default)
    if value == nil or value == "" then
        return default
    end
    local amount, unit = tostring(value):match("^(%d+)([smhd])$")
    if not amount then
        return nil, "invalid duration"
    end
    local multipliers = { s = 1, m = 60, h = 3600, d = 86400 }
    return tonumber(amount) * multipliers[unit]
end

local function read_file(path)
    if not path or path == "" then
        return nil, "path is not configured"
    end
    local file, err = io.open(path, "rb")
    if not file then
        return nil, err
    end
    local data = file:read("*a")
    file:close()
    return (data or ""):gsub("%s+$", "")
end

local function html_escape(value)
    value = tostring(value or "")
    value = value:gsub("&", "&amp;")
    value = value:gsub("<", "&lt;")
    value = value:gsub(">", "&gt;")
    value = value:gsub('"', "&quot;")
    value = value:gsub("'", "&#39;")
    return value
end

local function table_or_empty(value)
    if type(value) == "table" then
        return value
    end
    return {}
end

local function safe_return_uri(value)
    if type(value) == "table" then
        value = value[1]
    end
    if type(value) ~= "string" or value == "" then
        return "/"
    end
    if not value:match("^/") or value:match("^//") or value:find("[\r\n%z]") then
        return "/"
    end
    if value:match("^/__cwmcdn/captcha/") then
        return "/"
    end
    return value
end

local function constant_time_equal(a, b)
    if type(a) ~= "string" or type(b) ~= "string" or #a ~= #b then
        return false
    end
    local diff = 0
    for i = 1, #a do
        if a:byte(i) ~= b:byte(i) then
            diff = diff + 1
        end
    end
    return diff == 0
end

local function status_list_contains(statuses, status)
    status = tonumber(status)
    if not status then
        return false
    end
    for _, candidate in ipairs(table_or_empty(statuses)) do
        if tonumber(candidate) == status then
            return true
        end
    end
    return false
end

local function upstream_status_contains(upstream_status, statuses)
    statuses = table_or_empty(statuses)
    if #statuses == 0 then
        return true
    end
    for status in tostring(upstream_status or ""):gmatch("%d+") do
        if status_list_contains(statuses, tonumber(status)) then
            return true
        end
    end
    return false
end

local function redirect_target(target, preserve_query, args)
    if not preserve_query or args == nil or args == "" then
        return target
    end
    if target:find("?", 1, true) then
        return target .. "&" .. args
    end
    return target .. "?" .. args
end

local function request_identifier(ngx_ref)
    local host = ngx_ref.var.host or ""
    local remote_addr = ngx_ref.var.remote_addr or ""
    return host .. "|" .. remote_addr
end

function M.decode_policy(encoded)
    if type(encoded) == "table" then
        return encoded
    end
    if policy_cache[encoded] then
        return policy_cache[encoded]
    end
    local cjson = require "cjson.safe"
    local decoded, err = cjson.decode(encoded or "{}")
    if not decoded then
        error("failed to decode tenant policy: " .. tostring(err))
    end
    policy_cache[encoded] = decoded
    return decoded
end

function M.glob_to_lua_pattern(glob)
    local out = { "^" }
    local magic = { ["^"] = true, ["$"] = true, ["("] = true, [")"] = true, ["%"] = true, ["."] = true, ["["] = true, ["]"] = true, ["+"] = true, ["-"] = true, ["?"] = true }
    for i = 1, #glob do
        local ch = glob:sub(i, i)
        if ch == "*" then
            out[#out + 1] = ".*"
        elseif magic[ch] then
            out[#out + 1] = "%" .. ch
        else
            out[#out + 1] = ch
        end
    end
    out[#out + 1] = "$"
    return table.concat(out)
end

function M.glob_match(glob, path)
    if type(glob) ~= "string" or type(path) ~= "string" then
        return false
    end
    return path:match(M.glob_to_lua_pattern(glob)) ~= nil
end

function M.path_requires_captcha(policy, uri)
    policy = table_or_empty(policy)
    local captcha = table_or_empty(policy.captcha)
    if not captcha.enabled then
        return false
    end
    for _, rule in ipairs(table_or_empty(captcha.rules)) do
        local match = table_or_empty(table_or_empty(rule).match)
        if match.type == "glob" and M.glob_match(match.path, uri) then
            return true
        end
    end
    return false
end

function M.cookie_signature(signing_key, host, expires, signing_func)
    local material = tostring(host or "") .. "|" .. tostring(expires)
    if signing_func then
        return signing_func(signing_key, material)
    end
    return (ngx.encode_base64(ngx.hmac_sha1(signing_key, material)):gsub("=+$", ""))
end

function M.has_valid_captcha_cookie(policy, opts)
    opts = opts or {}
    local ngx_ref = opts.ngx or ngx
    local signing_key = opts.signing_key
    if not signing_key then
        signing_key = read_file(opts.signing_key_path)
    end
    if not signing_key or signing_key == "" then
        return false
    end
    local cookie = opts.cookie
    if not cookie and ngx_ref and ngx_ref.var then
        cookie = ngx_ref.var["cookie_" .. CAPTCHA_COOKIE]
    end
    local expires, signature = tostring(cookie or ""):match("^(%d+):([A-Za-z0-9+/=_-]+)$")
    expires = tonumber(expires)
    if not expires or expires <= (opts.now or ngx_ref.time()) then
        return false
    end
    local expected = M.cookie_signature(signing_key, opts.host or ngx_ref.var.host or "", expires, opts.signing_func)
    return constant_time_equal(signature, expected)
end

function M.set_captcha_cookie(policy, opts)
    opts = opts or {}
    local ngx_ref = opts.ngx or ngx
    policy = table_or_empty(policy)
    local captcha = table_or_empty(policy.captcha)
    local ttl, ttl_err = duration_seconds(captcha.cookieTtl, 1800)
    if not ttl then
        return nil, ttl_err
    end
    local signing_key, key_err = read_file(opts.signing_key_path)
    if not signing_key or signing_key == "" then
        return nil, key_err or "signing key is empty"
    end
    local expires = (opts.now or ngx_ref.time()) + ttl
    local signature = M.cookie_signature(signing_key, ngx_ref.var.host or "", expires)
    ngx_ref.header["Set-Cookie"] = CAPTCHA_COOKIE .. "=" .. expires .. ":" .. signature .. "; Path=/; Max-Age=" .. ttl .. "; HttpOnly; Secure; SameSite=Lax"
    return true
end

function M.rate_limit_exceeded(rate_limit, dict, now, client_key, prefix)
    rate_limit = table_or_empty(rate_limit)
    if not rate_limit.enabled then
        return false, 0
    end
    local period, period_err = duration_seconds(rate_limit.period, 60)
    if not period then
        return nil, period_err
    end
    local requests = tonumber(rate_limit.requests) or 0
    local burst = tonumber(rate_limit.burst) or 0
    if requests < 1 or burst < 0 then
        return nil, "invalid rate-limit settings"
    end
    local window = math.floor(now / period)
    local key = table.concat({ "rate", prefix or "", client_key or "", tostring(window) }, "|")
    local count, err = dict:incr(key, 1, 0, period * 2)
    if not count then
        return nil, err
    end
    return count > (requests + burst), count
end

function M.redirect_for_response(policy, uri, status, upstream_status)
    policy = table_or_empty(policy)
    for _, redirect in ipairs(table_or_empty(policy.redirects)) do
        redirect = table_or_empty(redirect)
        if redirect.enabled ~= false then
            local when = table_or_empty(redirect.when)
            local path = table_or_empty(when.path)
            local origin_statuses = table_or_empty(when.originStatus)
            local upstream_statuses = table_or_empty(when.upstreamStatus)
            local has_origin_status = #origin_statuses > 0
            local has_upstream_status = #upstream_statuses > 0
            local path_matches = true
            if path.type == "glob" then
                path_matches = M.glob_match(path.value, uri)
            end
            if (has_origin_status or has_upstream_status) and path_matches then
                if (not has_origin_status or status_list_contains(origin_statuses, status)) and upstream_status_contains(upstream_status, upstream_statuses) then
                    return redirect
                end
            end
        end
    end
    return nil
end

local function redirect_to_captcha(ngx_ref)
    local return_to = ngx_ref.escape_uri(ngx_ref.var.request_uri or "/")
    ngx_ref.redirect("/__cwmcdn/captcha/?return=" .. return_to, ngx_ref.HTTP_MOVED_TEMPORARILY or 302)
    return false, ngx_ref.HTTP_MOVED_TEMPORARILY or 302
end

function M.enforce_access(policy, opts)
    opts = opts or {}
    policy = table_or_empty(policy)
    local ngx_ref = opts.ngx or ngx
    local uri = ngx_ref.var.uri or "/"
    local captcha_valid = M.has_valid_captcha_cookie(policy, {
        ngx = ngx_ref,
        signing_key_path = opts.signing_key_path,
    })
    if M.path_requires_captcha(policy, uri) and not captcha_valid then
        return redirect_to_captcha(ngx_ref)
    end
    local security = table_or_empty(policy.security)
    local rate_limit = table_or_empty(security.rateLimit)
    if rate_limit.enabled then
        local dict = opts.rate_limit_dict or (ngx_ref.shared and ngx_ref.shared.cwmcdn_rate_limit)
        if not dict then
            ngx_ref.log(ngx_ref.ERR, "cwmcdn_rate_limit shared dict is unavailable")
            return false, ngx_ref.HTTP_SERVICE_UNAVAILABLE or 503
        end
        local limited, err = M.rate_limit_exceeded(rate_limit, dict, ngx_ref.time(), request_identifier(ngx_ref), opts.tenant or ngx_ref.var.host or "")
        if limited == nil then
            ngx_ref.log(ngx_ref.ERR, "rate limit failed: ", err)
            return false, ngx_ref.HTTP_SERVICE_UNAVAILABLE or 503
        end
        if limited then
            if rate_limit.action == "captcha" then
                if captcha_valid then
                    return true
                end
                return redirect_to_captcha(ngx_ref)
            end
            ngx_ref.status = ngx_ref.HTTP_TOO_MANY_REQUESTS or 429
            ngx_ref.say("rate limit exceeded")
            return false, ngx_ref.HTTP_TOO_MANY_REQUESTS or 429
        end
    end
    return true
end

local function encode_form(values)
    local parts = {}
    for key, value in pairs(values) do
        parts[#parts + 1] = ngx.escape_uri(key) .. "=" .. ngx.escape_uri(value or "")
    end
    return table.concat(parts, "&")
end

function M.verify_turnstile(token, secret, remote_ip, http_factory)
    if token == nil or token == "" then
        return false, "missing token"
    end
    local httpc
    if http_factory then
        httpc = http_factory()
    else
        local ok, http = pcall(require, "resty.http")
        if not ok then
            return false, "resty.http is unavailable"
        end
        httpc = http.new()
    end
    if httpc.set_timeout then
        httpc:set_timeout(3000)
    end
    local res, err = httpc:request_uri(TURNSTILE_VERIFY_URL, {
        method = "POST",
        body = encode_form({ secret = secret, response = token, remoteip = remote_ip or "" }),
        headers = { ["Content-Type"] = "application/x-www-form-urlencoded" },
        ssl_verify = true,
    })
    if not res then
        return false, err or "verification request failed"
    end
    local cjson = require "cjson.safe"
    local decoded = table_or_empty(cjson.decode(res.body or "{}"))
    if res.status == 200 and decoded.success == true then
        return true
    end
    return false, "turnstile verification failed"
end

function M.render_captcha_challenge(policy)
    policy = table_or_empty(policy)
    local captcha = table_or_empty(policy.captcha)
    if not captcha.enabled or captcha.provider ~= "turnstile" or captcha.siteKey == nil or captcha.siteKey == "" then
        ngx.status = ngx.HTTP_SERVICE_UNAVAILABLE
        ngx.say("captcha is not configured")
        return false, ngx.HTTP_SERVICE_UNAVAILABLE
    end
    local args = ngx.req.get_uri_args()
    local return_to = safe_return_uri(args["return"])
    ngx.header.content_type = "text/html; charset=utf-8"
    ngx.say("<!doctype html><html><head><meta charset=\"utf-8\"><title>Captcha required</title><script src=\"https://challenges.cloudflare.com/turnstile/v0/api.js\" async defer></script></head><body><main><h1>Verification required</h1><form method=\"POST\" action=\"/__cwmcdn/captcha/verify?return=" .. html_escape(ngx.escape_uri(return_to)) .. "\"><div class=\"cf-turnstile\" data-sitekey=\"" .. html_escape(captcha.siteKey) .. "\"></div><button type=\"submit\">Continue</button></form></main></body></html>")
    return true
end

function M.verify_captcha(policy, opts)
    opts = opts or {}
    policy = table_or_empty(policy)
    local captcha = table_or_empty(policy.captcha)
    local secret, secret_err = read_file(opts.secret_path)
    if not secret or secret == "" then
        ngx.status = ngx.HTTP_SERVICE_UNAVAILABLE
        ngx.say("captcha secret is not configured")
        return false, ngx.HTTP_SERVICE_UNAVAILABLE, secret_err
    end
    ngx.req.read_body()
    local post_args = ngx.req.get_post_args() or {}
    local uri_args = ngx.req.get_uri_args() or {}
    local token = post_args["cf-turnstile-response"] or post_args.token
    local ok, verify_err = M.verify_turnstile(token, secret, ngx.var.remote_addr, opts.http_factory)
    if not ok then
        ngx.status = ngx.HTTP_FORBIDDEN
        ngx.say(verify_err or "captcha verification failed")
        return false, ngx.HTTP_FORBIDDEN
    end
    local cookie_ok, cookie_err = M.set_captcha_cookie({ captcha = captcha }, { signing_key_path = opts.signing_key_path })
    if not cookie_ok then
        ngx.status = ngx.HTTP_SERVICE_UNAVAILABLE
        ngx.say("captcha signing key is not configured")
        return false, ngx.HTTP_SERVICE_UNAVAILABLE, cookie_err
    end
    ngx.redirect(safe_return_uri(uri_args["return"]), ngx.HTTP_MOVED_TEMPORARILY)
    return true
end

function M.handle_captcha(policy, opts)
    if (ngx.var.uri or ""):match("/verify$") then
        if ngx.req.get_method() ~= "POST" then
            ngx.status = ngx.HTTP_NOT_ALLOWED or 405
            ngx.say("captcha verification requires POST")
            return false, ngx.HTTP_NOT_ALLOWED or 405
        end
        return M.verify_captcha(policy, opts)
    end
    return M.render_captcha_challenge(policy)
end

function M.apply_response_redirect(policy)
    local redirect = M.redirect_for_response(policy, ngx.var.uri or "/", ngx.status, ngx.var.upstream_status)
    if not redirect then
        return
    end
    ngx.status = tonumber(redirect.status) or ngx.HTTP_MOVED_TEMPORARILY
    ngx.header["Location"] = redirect_target(redirect.to, redirect.preserveQuery, ngx.var.args)
    ngx.header["Content-Length"] = nil
    ngx.header["Content-Type"] = nil
    ngx.ctx.cwmcdn_response_redirect = true
end

function M.clear_redirect_body()
    if ngx.ctx.cwmcdn_response_redirect then
        ngx.arg[1] = ""
    end
end

return M
