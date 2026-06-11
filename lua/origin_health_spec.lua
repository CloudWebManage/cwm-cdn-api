local origin_health = require "origin_health"

describe("origin_health", function()

    describe("parse_status_line", function()
        it("extracts HTTP status", function()
            assert.are.equal(200, origin_health.parse_status_line("HTTP/1.1 200 OK"))
            assert.are.equal(503, origin_health.parse_status_line("HTTP/1.1 503 Service Unavailable"))
        end)

        it("returns 0 for invalid line", function()
            assert.are.equal(0, origin_health.parse_status_line("bad response"))
            assert.are.equal(0, origin_health.parse_status_line(nil))
        end)
    end)

    describe("default_state", function()
        it("returns disabled health check state", function()
            assert.are.same({
                healthy = 1,
                fails = 0,
                successes = 0,
                status = 0,
                latency_ms = 0,
                message = "health check disabled",
                checked_at = 123,
            }, origin_health.default_state(123))
        end)
    end)

    describe("build_check_request", function()
        it("builds an HTTP/1.1 health check request", function()
            assert.are.equal(
                "GET /healthz HTTP/1.1\r\nHost: origin.example.com:8080\r\nConnection: close\r\n\r\n",
                origin_health.build_check_request({
                    host_header = "origin.example.com:8080",
                    health = {
                        path = "/healthz",
                    },
                })
            )
        end)
    end)

    describe("apply_check_result", function()
        local origin = {
            health = {
                healthy_threshold = 2,
                unhealthy_threshold = 3,
            },
        }

        it("increments successes and marks healthy at threshold", function()
            assert.are.same({
                healthy = false,
                fails = 0,
                successes = 1,
            }, origin_health.apply_check_result(origin, {
                healthy = false,
                fails = 2,
                successes = 0,
            }, true))

            assert.are.same({
                healthy = true,
                fails = 0,
                successes = 2,
            }, origin_health.apply_check_result(origin, {
                healthy = false,
                fails = 1,
                successes = 1,
            }, true))
        end)

        it("increments failures and marks unhealthy at threshold", function()
            assert.are.same({
                healthy = true,
                fails = 1,
                successes = 0,
            }, origin_health.apply_check_result(origin, {
                healthy = true,
                fails = 0,
                successes = 2,
            }, false))

            assert.are.same({
                healthy = false,
                fails = 3,
                successes = 0,
            }, origin_health.apply_check_result(origin, {
                healthy = true,
                fails = 2,
                successes = 1,
            }, false))
        end)
    end)

    describe("read_state and write_state", function()
        local function shared_dict(values)
            return {
                values = values or {},
                get = function(self, key)
                    return self.values[key]
                end,
                set = function(self, key, value)
                    self.values[key] = value
                end,
            }
        end

        it("reads state with defaults", function()
            local health = shared_dict({
                ["origin_1:healthy"] = 0,
            })

            assert.are.same({
                healthy = false,
                fails = 0,
                successes = 0,
            }, origin_health.read_state(health, { key = "origin_1" }))
        end)

        it("writes state and converts boolean health", function()
            local health = shared_dict()

            origin_health.write_state(health, { key = "origin_1" }, {
                healthy = false,
                fails = 2,
                successes = 0,
                status = 503,
            })

            assert.are.same({
                ["origin_1:healthy"] = 0,
                ["origin_1:fails"] = 2,
                ["origin_1:successes"] = 0,
                ["origin_1:status"] = 503,
            }, health.values)
        end)
    end)
end)
