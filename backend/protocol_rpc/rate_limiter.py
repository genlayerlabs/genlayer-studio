"""Redis-backed tiered API key rate limiter."""

from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass
from typing import Callable, Optional

import redis.asyncio as aioredis
from sqlalchemy.orm import Session

from backend.database_handler.models import ApiKey, ApiTier
from backend.protocol_rpc.exceptions import RateLimitExceeded

logger = logging.getLogger(__name__)

TIER_CACHE_TTL = 300  # 5 minutes


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

    async def _check_windows(self, identity: str, limits: TierLimits) -> None:
        """Check sliding window counters for minute/hour/day."""
        now = time.time()
        windows = [
            ("minute", 60, limits.rate_limit_minute),
            ("hour", 3600, limits.rate_limit_hour),
            ("day", 86400, limits.rate_limit_day),
        ]

        # Phase 1: Prune old entries and count current in a single pipeline
        pipe = self._redis.pipeline()
        keys = []
        for window_name, window_seconds, _ in windows:
            key = f"ratelimit:{identity}:{window_name}"
            keys.append(key)
            cutoff = now - window_seconds
            pipe.zremrangebyscore(key, 0, cutoff)
            pipe.zcard(key)

        results = await pipe.execute()

        # Check counts (results alternate: zremrangebyscore result, zcard result)
        for i, (window_name, window_seconds, max_requests) in enumerate(windows):
            count = results[i * 2 + 1]  # zcard result
            if count >= max_requests:
                raise RateLimitExceeded(
                    message=f"Rate limit exceeded: {max_requests} requests per {window_name}",
                    data={
                        "window": window_name,
                        "limit": max_requests,
                        "current": count,
                        "retry_after_seconds": window_seconds,
                    },
                )

        # Phase 2: Record this request in all windows
        pipe2 = self._redis.pipeline()
        member = f"{now}"
        for i, (window_name, window_seconds, _) in enumerate(windows):
            key = keys[i]
            pipe2.zadd(key, {member: now})
            pipe2.expire(key, window_seconds + 60)
        await pipe2.execute()

    async def invalidate_key_cache(self, key_hash: str) -> None:
        """Invalidate cached tier for an API key (call after deactivation)."""
        cache_key = f"ratelimit:tier:{key_hash}"
        await self._redis.delete(cache_key)
