local M = {}

local function state_key(origin, field)
    return origin.key .. ":" .. field
end

function M.parse_status_line(line)
    if type(line) ~= "string" then
        return 0
    else
        return tonumber(string.match(line, "HTTP/%d+%.%d+%s+(%d+)")) or 0
    end
end

function M.build_check_request(origin)
    return "GET " .. origin.health.path .. " HTTP/1.1\r\nHost: " .. origin.host_header .. "\r\nConnection: close\r\n\r\n"
end

function M.default_state(now)
    return {
        healthy = 1,
        fails = 0,
        successes = 0,
        status = 0,
        latency_ms = 0,
        message = "health check disabled",
        checked_at = now,
    }
end

function M.read_state(health, origin)
    return {
        fails = health:get(state_key(origin, "fails")) or 0,
        successes = health:get(state_key(origin, "successes")) or 0,
        healthy = health:get(state_key(origin, "healthy")) ~= 0,
    }
end

function M.apply_check_result(origin, current, ok)
    local fails = current.fails or 0
    local successes = current.successes or 0
    local healthy = current.healthy

    if ok then
        successes = successes + 1
        fails = 0
        if successes >= origin.health.healthy_threshold then
            healthy = true
        end
    else
        fails = fails + 1
        successes = 0
        if fails >= origin.health.unhealthy_threshold then
            healthy = false
        end
    end

    return {
        healthy = healthy,
        fails = fails,
        successes = successes,
    }
end

function M.write_state(health, origin, state)
    for field, value in pairs(state) do
        if field == "healthy" and type(value) == "boolean" then
            value = value and 1 or 0
        end
        health:set(state_key(origin, field), value)
    end
end

return M
