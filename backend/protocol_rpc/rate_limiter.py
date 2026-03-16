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

# Lua script that atomically prunes, checks, and records in one round-trip.
# This eliminates the TOCTOU race where concurrent requests could all read the
# same stale count before any of them recorded, bypassing the limit.
#
# KEYS: [minute_key, hour_key, day_key]
# ARGV: [now, member, minute_window, minute_limit, hour_window, hour_limit,
#         day_window, day_limit]
#
# Returns: [0] on success, or [1, window_name, limit, count, retry_after] on denial.
_CHECK_AND_RECORD_LUA = """
local now = tonumber(ARGV[1])
local member = ARGV[2]

local windows = {
    {key = KEYS[1], seconds = tonumber(ARGV[3]), limit = tonumber(ARGV[4]), name = "minute"},
    {key = KEYS[2], seconds = tonumber(ARGV[5]), limit = tonumber(ARGV[6]), name = "hour"},
    {key = KEYS[3], seconds = tonumber(ARGV[7]), limit = tonumber(ARGV[8]), name = "day"},
}

-- Phase 1: Prune expired entries and check counts
for _, w in ipairs(windows) do
    redis.call('ZREMRANGEBYSCORE', w.key, 0, now - w.seconds)
    local count = redis.call('ZCARD', w.key)
    if count >= w.limit then
        return {1, w.name, w.limit, count, w.seconds}
    end
end

-- Phase 2: Record this request (only reached if all windows are under limit)
for _, w in ipairs(windows) do
    redis.call('ZADD', w.key, now, member)
    redis.call('EXPIRE', w.key, w.seconds + 60)
end

return {0}
"""


@dataclass(frozen=True)
class TierLimits:
    name: str
    rate_limit_minute: int
    rate_limit_hour: int
    rate_limit_day: int


class RateLimiterService:
    """Sliding-window rate limiter backed by Redis sorted sets."""

    def __init__(
        self,
        redis_client: aioredis.Redis,
        get_session: Callable[[], Session],
        enabled: bool = True,
        anon_per_minute: int = 10,
        anon_per_hour: int = 100,
        anon_per_day: int = 1000,
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
            anon_per_minute=int(os.environ.get("RATE_LIMIT_ANON_PER_MINUTE", "10")),
            anon_per_hour=int(os.environ.get("RATE_LIMIT_ANON_PER_HOUR", "100")),
            anon_per_day=int(os.environ.get("RATE_LIMIT_ANON_PER_DAY", "1000")),
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def check_rate_limit(self, api_key: Optional[str], client_ip: str) -> None:
        """Check rate limits. Raises RateLimitExceeded if over limit."""
        if not self._enabled:
            return

        if api_key:
            identity, limits = await self._resolve_api_key(api_key)
            if identity is None:
                raise RateLimitExceeded(message="Invalid API key")
        else:
            identity = f"ip:{client_ip}"
            limits = self._anon_limits

        await self._check_windows(identity, limits)

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

    async def _check_windows(self, identity: str, limits: TierLimits) -> None:
        """Atomically prune, check, and record using a Lua script."""
        now = time.time()
        member = f"{now}:{uuid.uuid4().hex[:8]}"

        keys = [
            f"ratelimit:{identity}:minute",
            f"ratelimit:{identity}:hour",
            f"ratelimit:{identity}:day",
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

        if result[0] == 1:
            window_name = (
                result[1].decode() if isinstance(result[1], bytes) else result[1]
            )
            max_requests = int(result[2])
            count = int(result[3])
            retry_after = int(result[4])
            raise RateLimitExceeded(
                message=f"Rate limit exceeded: {max_requests} requests per {window_name}",
                data={
                    "window": window_name,
                    "limit": max_requests,
                    "current": count,
                    "retry_after_seconds": retry_after,
                },
            )

    async def invalidate_key_cache(self, key_hash: str) -> None:
        """Invalidate cached tier for an API key (call after deactivation)."""
        cache_key = f"ratelimit:tier:{key_hash}"
        await self._redis.delete(cache_key)
