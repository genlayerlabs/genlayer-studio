"""Redis-backed tiered API key rate limiter."""

from __future__ import annotations

import hashlib
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Optional

import redis.asyncio as aioredis
from redis.exceptions import NoScriptError
from sqlalchemy.orm import Session

from backend.database_handler.models import ApiKey, ApiTier
from backend.protocol_rpc.exceptions import RateLimitExceeded

logger = logging.getLogger(__name__)

TIER_CACHE_TTL = 300  # 5 minutes
DEFAULT_ANON_PER_MINUTE = 30
DEFAULT_ANON_PER_HOUR = 500
DEFAULT_ANON_PER_DAY = 5000

# Cheap reads (see rate_limit_methods) are metered in their own bucket at this
# multiple of the tier's limits. Reads never reach the GenVM, so the tier
# numbers — which have to be sized for consensus rounds — are far stricter than
# a database read warrants.
DEFAULT_READ_MULTIPLIER = 10

STANDARD_BUCKET = "standard"
READ_BUCKET = "read"

# Lua script that atomically prunes, checks, and records in one round-trip.
# This eliminates the TOCTOU race where concurrent requests could all read the
# same stale count before any of them recorded, bypassing the limit.
#
# KEYS: [minute_key, hour_key, day_key]
# ARGV: [now, member, minute_window, minute_limit, hour_window, hour_limit,
#         day_window, day_limit]
#
# Returns [allowed, window_name, limit, count, reset_seconds] in both the
# allowed (allowed=0) and denied (allowed=1) case. The reported window is the
# one closest to exhaustion, which is what a client needs in order to pace
# itself — reporting all three would just make the caller compute this anyway.
#
# `reset_seconds` is the time until the oldest entry in that window ages out,
# i.e. when capacity actually frees up. For a sliding window that is the honest
# answer; the window length alone would overstate the wait.
_CHECK_AND_RECORD_LUA = """
local now = tonumber(ARGV[1])
local member = ARGV[2]

local windows = {
    {key = KEYS[1], seconds = tonumber(ARGV[3]), limit = tonumber(ARGV[4]), name = "minute"},
    {key = KEYS[2], seconds = tonumber(ARGV[5]), limit = tonumber(ARGV[6]), name = "hour"},
    {key = KEYS[3], seconds = tonumber(ARGV[7]), limit = tonumber(ARGV[8]), name = "day"},
}

local function reset_seconds(w)
    local oldest = redis.call('ZRANGE', w.key, 0, 0, 'WITHSCORES')
    if not oldest[2] then
        return w.seconds
    end
    local reset = math.ceil(tonumber(oldest[2]) + w.seconds - now)
    if reset < 1 then
        return 1
    end
    return reset
end

-- Phase 1: Prune expired entries and check counts
for _, w in ipairs(windows) do
    redis.call('ZREMRANGEBYSCORE', w.key, 0, now - w.seconds)
    w.count = redis.call('ZCARD', w.key)
    if w.count >= w.limit then
        return {1, w.name, w.limit, w.count, reset_seconds(w)}
    end
end

-- Phase 2: Record this request (only reached if all windows are under limit)
for _, w in ipairs(windows) do
    redis.call('ZADD', w.key, now, member)
    redis.call('EXPIRE', w.key, w.seconds + 60)
    w.count = w.count + 1
end

-- Phase 3: Report whichever window has the least headroom left
local tightest = windows[1]
for _, w in ipairs(windows) do
    if (w.limit - w.count) < (tightest.limit - tightest.count) then
        tightest = w
    end
end

return {0, tightest.name, tightest.limit, tightest.count, reset_seconds(tightest)}
"""


@dataclass(frozen=True)
class TierLimits:
    name: str
    rate_limit_minute: int
    rate_limit_hour: int
    rate_limit_day: int

    def scaled(self, factor: int) -> "TierLimits":
        return TierLimits(
            name=self.name,
            rate_limit_minute=self.rate_limit_minute * factor,
            rate_limit_hour=self.rate_limit_hour * factor,
            rate_limit_day=self.rate_limit_day * factor,
        )


@dataclass(frozen=True)
class RateLimitUsage:
    """Headroom in the window closest to exhaustion, for X-RateLimit-* headers."""

    bucket: str
    window: str
    limit: int
    remaining: int
    reset_seconds: int

    def as_headers(self) -> dict[str, str]:
        return {
            "X-RateLimit-Bucket": self.bucket,
            "X-RateLimit-Window": self.window,
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(self.remaining),
            "X-RateLimit-Reset": str(self.reset_seconds),
        }


class RateLimiterService:
    """Sliding-window rate limiter backed by Redis sorted sets."""

    def __init__(
        self,
        redis_client: aioredis.Redis,
        get_session: Callable[[], Session],
        enabled: bool = True,
        anon_per_minute: int = DEFAULT_ANON_PER_MINUTE,
        anon_per_hour: int = DEFAULT_ANON_PER_HOUR,
        anon_per_day: int = DEFAULT_ANON_PER_DAY,
        read_multiplier: int = DEFAULT_READ_MULTIPLIER,
    ):
        self._redis = redis_client
        self._get_session = get_session
        self._enabled = enabled
        self._anon_limits = TierLimits(
            name="anonymous",
            rate_limit_minute=anon_per_minute,
            rate_limit_hour=anon_per_hour,
            rate_limit_day=anon_per_day,
        )
        # A multiplier below 1 would make reads *stricter* than writes, which is
        # never intended; clamp rather than trust the environment.
        self._read_multiplier = max(1, read_multiplier)
        self._lua_sha: Optional[str] = None

    @classmethod
    def from_environment(
        cls,
        redis_client: aioredis.Redis,
        get_session: Callable[[], Session],
    ) -> RateLimiterService:
        return cls(
            redis_client=redis_client,
            get_session=get_session,
            enabled=os.environ.get("RATE_LIMIT_ENABLED", "false").lower() == "true",
            anon_per_minute=int(
                os.environ.get("RATE_LIMIT_ANON_PER_MINUTE", DEFAULT_ANON_PER_MINUTE)
            ),
            anon_per_hour=int(
                os.environ.get("RATE_LIMIT_ANON_PER_HOUR", DEFAULT_ANON_PER_HOUR)
            ),
            anon_per_day=int(
                os.environ.get("RATE_LIMIT_ANON_PER_DAY", DEFAULT_ANON_PER_DAY)
            ),
            read_multiplier=int(
                os.environ.get("RATE_LIMIT_READ_MULTIPLIER", DEFAULT_READ_MULTIPLIER)
            ),
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def check_rate_limit(
        self,
        api_key: Optional[str],
        client_ip: str,
        is_cheap_read: bool = False,
    ) -> Optional[RateLimitUsage]:
        """Check rate limits. Raises RateLimitExceeded if over limit.

        Returns the usage of whichever window is closest to exhaustion, or None
        when limiting is disabled.
        """
        if not self._enabled:
            return None

        if api_key:
            identity, limits = await self._resolve_api_key(api_key)
            if identity is None or limits is None:
                raise RateLimitExceeded(message="Invalid API key")
        else:
            identity = f"ip:{client_ip}"
            limits = self._anon_limits

        if is_cheap_read:
            return await self._check_windows(
                identity, limits.scaled(self._read_multiplier), READ_BUCKET
            )
        return await self._check_windows(identity, limits, STANDARD_BUCKET)

    async def _resolve_api_key(
        self, raw_key: str
    ) -> tuple[Optional[str], Optional[TierLimits]]:
        """Look up API key tier, using Redis cache."""
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        cache_key = f"ratelimit:tier:{key_hash}"

        # Try cache first
        cached = await self._redis.hgetall(cache_key)
        if cached:
            if cached.get("status") == "inactive":
                return None, None
            return f"key:{key_hash[:16]}", TierLimits(
                name=cached["name"],
                rate_limit_minute=int(cached["rpm"]),
                rate_limit_hour=int(cached["rph"]),
                rate_limit_day=int(cached["rpd"]),
            )

        # Cache miss: query DB
        session = self._get_session()
        try:
            api_key_row = session.query(ApiKey).filter_by(key_hash=key_hash).first()
            if api_key_row is None or not api_key_row.is_active:
                await self._redis.hset(cache_key, mapping={"status": "inactive"})
                await self._redis.expire(cache_key, TIER_CACHE_TTL)
                return None, None

            tier = session.query(ApiTier).filter_by(id=api_key_row.tier_id).first()
            if tier is None:
                return None, None

            await self._redis.hset(
                cache_key,
                mapping={
                    "status": "active",
                    "name": tier.name,
                    "rpm": str(tier.rate_limit_minute),
                    "rph": str(tier.rate_limit_hour),
                    "rpd": str(tier.rate_limit_day),
                },
            )
            await self._redis.expire(cache_key, TIER_CACHE_TTL)

            return f"key:{key_hash[:16]}", TierLimits(
                name=tier.name,
                rate_limit_minute=tier.rate_limit_minute,
                rate_limit_hour=tier.rate_limit_hour,
                rate_limit_day=tier.rate_limit_day,
            )
        finally:
            session.close()

    async def _ensure_lua_loaded(self) -> str:
        """Load the Lua script into Redis and cache the SHA."""
        if self._lua_sha is None:
            self._lua_sha = await self._redis.script_load(_CHECK_AND_RECORD_LUA)
        return self._lua_sha

    async def _check_windows(
        self,
        identity: str,
        limits: TierLimits,
        bucket: str,
    ) -> RateLimitUsage:
        """Atomically prune, check, and record using a Lua script."""
        now = time.time()
        member = f"{now}:{uuid.uuid4().hex[:8]}"

        # The standard bucket keeps its original key shape so that limits in
        # flight at deploy time carry over instead of silently resetting.
        prefix = (
            f"ratelimit:{identity}"
            if bucket == STANDARD_BUCKET
            else f"ratelimit:{identity}:{bucket}"
        )
        keys = [
            f"{prefix}:minute",
            f"{prefix}:hour",
            f"{prefix}:day",
        ]
        args = [
            str(now),
            member,
            "60",
            str(limits.rate_limit_minute),
            "3600",
            str(limits.rate_limit_hour),
            "86400",
            str(limits.rate_limit_day),
        ]

        sha = await self._ensure_lua_loaded()
        try:
            result = await self._redis.evalsha(sha, len(keys), *keys, *args)
        except NoScriptError:
            # Script was evicted from Redis cache, reload it
            self._lua_sha = None
            sha = await self._ensure_lua_loaded()
            result = await self._redis.evalsha(sha, len(keys), *keys, *args)

        window_name = result[1].decode() if isinstance(result[1], bytes) else result[1]
        max_requests = int(result[2])
        count = int(result[3])
        reset_after = int(result[4])

        if result[0] == 1:
            raise RateLimitExceeded(
                message=f"Rate limit exceeded: {max_requests} requests per {window_name}",
                data={
                    "bucket": bucket,
                    "window": window_name,
                    "limit": max_requests,
                    "current": count,
                    "retry_after_seconds": reset_after,
                },
            )

        return RateLimitUsage(
            bucket=bucket,
            window=window_name,
            limit=max_requests,
            remaining=max(0, max_requests - count),
            reset_seconds=reset_after,
        )

    async def invalidate_key_cache(self, key_hash: str) -> None:
        """Invalidate cached tier for an API key (call after deactivation)."""
        cache_key = f"ratelimit:tier:{key_hash}"
        await self._redis.delete(cache_key)
