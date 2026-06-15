local cwm_policy = require "cwm_policy"

local function shared_dict()
    local values = {}
    return {
        incr = function(_, key, amount, init)
            if values[key] == nil then
                values[key] = init
            end
            values[key] = values[key] + amount
            return values[key]
        end,
    }
end

describe("cwm_policy", function()
    it("matches safe path globs", function()
        assert.is_true(cwm_policy.glob_match("/protected/*", "/protected/a/b"))
        assert.is_true(cwm_policy.glob_match("/exact", "/exact"))
        assert.is_false(cwm_policy.glob_match("/protected/*", "/public/a"))
    end)

    it("detects captcha-protected paths", function()
        local policy = {
            captcha = {
                enabled = true,
                rules = {
                    { name = "protected", match = { type = "glob", path = "/protected/*" } },
                },
            },
        }

        assert.is_true(cwm_policy.path_requires_captcha(policy, "/protected/index.html"))
        assert.is_false(cwm_policy.path_requires_captcha(policy, "/public/index.html"))
    end)

    it("enforces fixed-window rate limits with burst", function()
        local dict = shared_dict()
        local rate_limit = { enabled = true, requests = 2, period = "1m", burst = 1 }

        assert.is_false(cwm_policy.rate_limit_exceeded(rate_limit, dict, 10, "client", "tenant"))
        assert.is_false(cwm_policy.rate_limit_exceeded(rate_limit, dict, 20, "client", "tenant"))
        assert.is_false(cwm_policy.rate_limit_exceeded(rate_limit, dict, 30, "client", "tenant"))
        assert.is_true(cwm_policy.rate_limit_exceeded(rate_limit, dict, 40, "client", "tenant"))
        assert.is_false(cwm_policy.rate_limit_exceeded(rate_limit, dict, 70, "client", "tenant"))
    end)

    it("selects response-status redirects by path and upstream status", function()
        local policy = {
            redirects = {
                {
                    name = "missing",
                    when = { path = { type = "glob", value = "/old/*" }, upstreamStatus = { 404 } },
                    to = "/new/",
                    status = 302,
                },
            },
        }

        assert.are.same(policy.redirects[1], cwm_policy.redirect_for_response(policy, "/old/page", 404, "502, 404"))
        assert.is_nil(cwm_policy.redirect_for_response(policy, "/old/page", 404, "200"))
        assert.is_nil(cwm_policy.redirect_for_response(policy, "/other/page", 404, "404"))
    end)

    it("selects response-status redirects without a path matcher", function()
        local policy = {
            redirects = {
                {
                    name = "not-found",
                    when = { upstreamStatus = { 404 } },
                    to = "/404.html",
                    status = 302,
                },
            },
        }

        assert.are.same(policy.redirects[1], cwm_policy.redirect_for_response(policy, "/anything", 404, "404"))
    end)
end)
