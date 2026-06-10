local M = {}

local function is_healthy(health, origin)
    return health:get(origin.key .. ":healthy") ~= 0
end

function M.select_weighted_origin(origins, health, cursor)
    local running = 0

    for i, origin in ipairs(origins) do
        if is_healthy(health, origin) then
            running = running + origin.weight
            if cursor <= running then
                return i, origin
            end
        end
    end

    return nil, nil
end

function M.total_healthy_weight(origins, health)
    local total_weight = 0

    for _, origin in ipairs(origins) do
        if is_healthy(health, origin) then
            total_weight = total_weight + origin.weight
        end
    end

    return total_weight
end

function M.select_retry_origin(origins, health, desired_scheme, tried, preferred_index)
    local index = tonumber(preferred_index)

    if index ~= nil and tried[index] then
        index = nil
    end

    if index == nil then
        for i, origin in ipairs(origins) do
            if origin.scheme == desired_scheme and not tried[i] and is_healthy(health, origin) then
                index = i
                break
            end
        end
    end

    if index == nil then
        return nil, nil
    end

    return index, origins[index]
end

function M.count_remaining_retries(origins, health, desired_scheme, tried)
    local remaining = 0

    for i, origin in ipairs(origins) do
        if origin.scheme == desired_scheme and not tried[i] and is_healthy(health, origin) then
            remaining = remaining + 1
        end
    end

    return remaining
end

return M
