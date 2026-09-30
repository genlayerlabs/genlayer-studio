"""Executes the rate limiter's Lua script for real.

Every other test mocks `evalsha`, so the script body itself is otherwise
unexercised — a syntax error or a bad redis call in it would first surface in
production, on every single /api request. Here the script runs under a stubbed
`redis.call` that implements enough sorted-set semantics to be meaningful.

Skipped when no `lua` interpreter is present.
"""

import shutil
import subprocess
import tempfile

import pytest

from backend.protocol_rpc.rate_limiter import _CHECK_AND_RECORD_LUA

pytestmark = pytest.mark.skipif(
    shutil.which("lua") is None, reason="requires a lua interpreter"
)

REDIS_STUB = """
local store = {}
local function zset(k) store[k] = store[k] or {}; return store[k] end

redis = {}
function redis.call(cmd, key, a, b, c)
    local z = zset(key)
    if cmd == 'ZREMRANGEBYSCORE' then
        local kept = {}
        for _, e in ipairs(z) do
            if not (e.score >= tonumber(a) and e.score <= tonumber(b)) then
                kept[#kept + 1] = e
            end
        end
        store[key] = kept
        return 0
    elseif cmd == 'ZCARD' then
        return #z
    elseif cmd == 'ZADD' then
        z[#z + 1] = {score = tonumber(a), member = b}
        table.sort(z, function(x, y) return x.score < y.score end)
        return 1
    elseif cmd == 'EXPIRE' then
        return 1
    elseif cmd == 'ZRANGE' then
        if #z == 0 then return {} end
        return {z[1].member, tostring(z[1].score)}
    end
    error('unstubbed redis command: ' .. cmd)
end

local function run(now, keys, argv)
    KEYS = keys
    ARGV = argv
    return SCRIPT(now)
end

local function argv(now, member, m, h, d)
    return {tostring(now), member, "60", tostring(m),
            "3600", tostring(h), "86400", tostring(d)}
end

local K = {"k:minute", "k:hour", "k:day"}
"""


def _run_lua(scenario: str) -> str:
    script = (
        REDIS_STUB
        + "function SCRIPT(now)\n"
        + _CHECK_AND_RECORD_LUA
        + "\nend\n"
        + scenario
    )
    with tempfile.NamedTemporaryFile("w", suffix=".lua") as fh:
        fh.write(script)
        fh.flush()
        result = subprocess.run(
            ["lua", fh.name], capture_output=True, text=True, timeout=30
        )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_allows_and_reports_tightest_window():
    out = _run_lua(
        """
        local r = run(1000, K, argv(1000, "a", 3, 100, 1000))
        assert(r[1] == 0)
        assert(r[2] == "minute")
        assert(r[3] == 3 and r[4] == 1)
        print("ok")
        """
    )
    assert "ok" in out


def test_denies_once_window_is_full():
    out = _run_lua(
        """
        run(1000, K, argv(1000, "a", 3, 100, 1000))
        run(1001, K, argv(1001, "b", 3, 100, 1000))
        run(1002, K, argv(1002, "c", 3, 100, 1000))
        local d = run(1003, K, argv(1003, "d", 3, 100, 1000))
        assert(d[1] == 1)
        assert(d[2] == "minute")
        -- oldest entry is at t=1000, so capacity returns at t=1060
        assert(d[5] == 57, "reset was " .. tostring(d[5]))
        print("ok")
        """
    )
    assert "ok" in out


def test_capacity_returns_as_window_slides():
    out = _run_lua(
        """
        run(1000, K, argv(1000, "a", 1, 100, 1000))
        local denied = run(1030, K, argv(1030, "b", 1, 100, 1000))
        assert(denied[1] == 1)
        local allowed = run(1061, K, argv(1061, "c", 1, 100, 1000))
        assert(allowed[1] == 0)
        print("ok")
        """
    )
    assert "ok" in out


def test_day_window_can_be_the_reported_one():
    out = _run_lua(
        """
        local r = run(2000, K, argv(2000, "a", 1000, 1000, 2))
        assert(r[2] == "day", "got " .. tostring(r[2]))
        print("ok")
        """
    )
    assert "ok" in out


def test_reset_is_never_below_one_second():
    """A zero would tell clients to retry immediately into another denial."""
    out = _run_lua(
        """
        run(3000, K, argv(3000, "a", 1, 100, 1000))
        local edge = run(3059.9, K, argv(3059.9, "b", 1, 100, 1000))
        assert(edge[1] == 1)
        assert(edge[5] >= 1, "reset was " .. tostring(edge[5]))
        print("ok")
        """
    )
    assert "ok" in out


def test_denial_does_not_consume_capacity():
    """A rejected request must not push the oldest entry further out."""
    out = _run_lua(
        """
        run(1000, K, argv(1000, "a", 2, 100, 1000))
        run(1001, K, argv(1001, "b", 2, 100, 1000))
        local first = run(1002, K, argv(1002, "c", 2, 100, 1000))
        local second = run(1003, K, argv(1003, "d", 2, 100, 1000))
        assert(first[1] == 1 and second[1] == 1)
        assert(first[4] == 2 and second[4] == 2, "denied requests were recorded")
        print("ok")
        """
    )
    assert "ok" in out
