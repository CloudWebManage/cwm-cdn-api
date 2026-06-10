local origin_balancer = require "origin_balancer"

local function shared_health(states)
    return {
        get = function(_, key)
            return states[key]
        end,
    }
end

describe("origin_balancer", function()
    local origins = {
        { key = "origin_0", name = "a", scheme = "http", weight = 2 },
        { key = "origin_1", name = "b", scheme = "http", weight = 1 },
        { key = "origin_2", name = "c", scheme = "https", weight = 3 },
    }

    describe("total_healthy_weight", function()
        it("sums only healthy origins", function()
            local health = shared_health({
                ["origin_0:healthy"] = 1,
                ["origin_1:healthy"] = 0,
                ["origin_2:healthy"] = 1,
            })

            assert.are.equal(5, origin_balancer.total_healthy_weight(origins, health))
        end)
    end)

    describe("select_weighted_origin", function()
        it("selects by weighted cursor across healthy origins", function()
            local health = shared_health({
                ["origin_0:healthy"] = 1,
                ["origin_1:healthy"] = 1,
                ["origin_2:healthy"] = 0,
            })

            assert.are.same({ 1, origins[1] }, { origin_balancer.select_weighted_origin(origins, health, 1) })
            assert.are.same({ 1, origins[1] }, { origin_balancer.select_weighted_origin(origins, health, 2) })
            assert.are.same({ 2, origins[2] }, { origin_balancer.select_weighted_origin(origins, health, 3) })
        end)

        it("returns nil when no healthy origin matches cursor", function()
            local health = shared_health({
                ["origin_0:healthy"] = 0,
                ["origin_1:healthy"] = 0,
                ["origin_2:healthy"] = 0,
            })

            assert.is_nil(origin_balancer.select_weighted_origin(origins, health, 1))
        end)
    end)

    describe("select_retry_origin", function()
        it("uses preferred index when available and untried", function()
            local health = shared_health({
                ["origin_0:healthy"] = 1,
                ["origin_1:healthy"] = 1,
                ["origin_2:healthy"] = 1,
            })

            assert.are.same({ 2, origins[2] }, { origin_balancer.select_retry_origin(origins, health, "http", {}, "2") })
        end)

        it("falls back to first healthy untried matching scheme", function()
            local health = shared_health({
                ["origin_0:healthy"] = 1,
                ["origin_1:healthy"] = 1,
                ["origin_2:healthy"] = 1,
            })

            assert.are.same({ 2, origins[2] }, { origin_balancer.select_retry_origin(origins, health, "http", { [1] = true }, "1") })
        end)

        it("returns nil when no retry origin is available", function()
            local health = shared_health({
                ["origin_0:healthy"] = 0,
                ["origin_1:healthy"] = 1,
                ["origin_2:healthy"] = 1,
            })

            assert.is_nil(origin_balancer.select_retry_origin(origins, health, "http", { [2] = true }, nil))
        end)
    end)

    describe("count_remaining_retries", function()
        it("counts healthy untried origins for the desired scheme", function()
            local health = shared_health({
                ["origin_0:healthy"] = 1,
                ["origin_1:healthy"] = 1,
                ["origin_2:healthy"] = 1,
            })

            assert.are.equal(1, origin_balancer.count_remaining_retries(origins, health, "http", { [1] = true }))
            assert.are.equal(1, origin_balancer.count_remaining_retries(origins, health, "https", {}))
        end)
    end)
end)
