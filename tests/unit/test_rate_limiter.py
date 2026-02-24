"""Unit tests for RateLimiterService."""

import hashlib
import time

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.protocol_rpc.rate_limiter import RateLimiterService, TierLimits
from backend.protocol_rpc.exceptions import RateLimitExceeded


def _make_redis_mock():
    """Create a mock Redis client with pipeline support."""
    redis = AsyncMock()
    # Pipeline methods (zremrangebyscore, zcard, zadd, expire) are called
    # without await for chaining; only execute() is awaited.
    pipe = MagicMock()
    pipe.execute = AsyncMock()
    # pipeline() itself is synchronous in redis.asyncio
    redis.pipeline = MagicMock(return_value=pipe)
    redis.hgetall = AsyncMock(return_value={})
    redis.hset = AsyncMock()
    redis.expire = AsyncMock()
    redis.delete = AsyncMock()
    return redis, pipe


def _make_service(redis=None, pipe=None, enabled=True):
    """Create a RateLimiterService with mocked dependencies."""
    if redis is None:
        redis, pipe = _make_redis_mock()
    return (
        RateLimiterService(
            redis_client=redis,
            get_session=MagicMock(),
            enabled=enabled,
            anon_per_minute=5,
            anon_per_hour=50,
            anon_per_day=500,
        ),
        redis,
        pipe,
    )


class TestDisabledMode:
    @pytest.mark.asyncio
    async def test_check_does_nothing_when_disabled(self):
        service, redis, _ = _make_service(enabled=False)
        await service.check_rate_limit("some-key", "1.2.3.4")
        redis.pipeline.assert_not_called()

    def test_enabled_property(self):
        service, _, _ = _make_service(enabled=False)
        assert service.enabled is False

        service2, _, _ = _make_service(enabled=True)
        assert service2.enabled is True


class TestAnonymousRateLimiting:
    @pytest.mark.asyncio
    async def test_allowed_when_under_limit(self):
        redis, pipe = _make_redis_mock()
        # zcard results: 0 for each window (below limits)
        pipe.execute = AsyncMock(
            side_effect=[
                [0, 2, 0, 10, 0, 100],  # phase 1: prune+count
                [True, True, True, True, True, True],  # phase 2: zadd+expire
            ]
        )
        service, _, _ = _make_service(redis=redis, pipe=pipe)
        # Should not raise
        await service.check_rate_limit(None, "1.2.3.4")

    @pytest.mark.asyncio
    async def test_raises_when_minute_limit_exceeded(self):
        redis, pipe = _make_redis_mock()
        # minute count = 5 (equals limit), should raise
        pipe.execute = AsyncMock(
            return_value=[0, 5, 0, 10, 0, 100]  # phase 1 only; raises before phase 2
        )
        service, _, _ = _make_service(redis=redis, pipe=pipe)
        with pytest.raises(RateLimitExceeded) as exc_info:
            await service.check_rate_limit(None, "1.2.3.4")
        assert exc_info.value.code == -32029
        assert exc_info.value.data["window"] == "minute"
        assert exc_info.value.data["limit"] == 5

    @pytest.mark.asyncio
    async def test_raises_when_hour_limit_exceeded(self):
        redis, pipe = _make_redis_mock()
        # minute ok (3 < 5), hour exceeded (50 >= 50)
        pipe.execute = AsyncMock(return_value=[0, 3, 0, 50, 0, 100])
        service, _, _ = _make_service(redis=redis, pipe=pipe)
        with pytest.raises(RateLimitExceeded) as exc_info:
            await service.check_rate_limit(None, "1.2.3.4")
        assert exc_info.value.data["window"] == "hour"

    @pytest.mark.asyncio
    async def test_raises_when_day_limit_exceeded(self):
        redis, pipe = _make_redis_mock()
        # minute ok, hour ok, day exceeded (500 >= 500)
        pipe.execute = AsyncMock(return_value=[0, 3, 0, 40, 0, 500])
        service, _, _ = _make_service(redis=redis, pipe=pipe)
        with pytest.raises(RateLimitExceeded) as exc_info:
            await service.check_rate_limit(None, "1.2.3.4")
        assert exc_info.value.data["window"] == "day"

    @pytest.mark.asyncio
    async def test_identity_uses_ip(self):
        redis, pipe = _make_redis_mock()
        pipe.execute = AsyncMock(
            side_effect=[
                [0, 0, 0, 0, 0, 0],
                [True, True, True, True, True, True],
            ]
        )
        service, _, _ = _make_service(redis=redis, pipe=pipe)
        await service.check_rate_limit(None, "10.0.0.1")
        # Verify keys contain the IP
        calls = pipe.zremrangebyscore.call_args_list
        assert any("ip:10.0.0.1" in str(c) for c in calls)


class TestApiKeyResolution:
    @pytest.mark.asyncio
    async def test_invalid_key_raises(self):
        redis, pipe = _make_redis_mock()
        # Cache returns inactive
        redis.hgetall = AsyncMock(return_value={"status": "inactive"})
        service, _, _ = _make_service(redis=redis, pipe=pipe)
        with pytest.raises(RateLimitExceeded) as exc_info:
            await service.check_rate_limit("bad-key", "1.2.3.4")
        assert "Invalid API key" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_cached_key_uses_cached_limits(self):
        redis, pipe = _make_redis_mock()
        redis.hgetall = AsyncMock(
            return_value={
                "status": "active",
                "name": "pro",
                "rpm": "120",
                "rph": "3000",
                "rpd": "50000",
            }
        )
        pipe.execute = AsyncMock(
            side_effect=[
                [0, 0, 0, 0, 0, 0],  # phase 1: all under limit
                [True, True, True, True, True, True],  # phase 2
            ]
        )
        service, _, _ = _make_service(redis=redis, pipe=pipe)
        await service.check_rate_limit("glk_test1234", "1.2.3.4")
        # Verify it used the cached limits (didn't query DB)
        service._get_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_miss_queries_db(self):
        redis, pipe = _make_redis_mock()
        # Cache miss
        redis.hgetall = AsyncMock(return_value={})

        # Mock DB session
        mock_session = MagicMock()
        mock_tier = MagicMock()
        mock_tier.name = "free"
        mock_tier.rate_limit_minute = 30
        mock_tier.rate_limit_hour = 500
        mock_tier.rate_limit_day = 5000

        mock_api_key = MagicMock()
        mock_api_key.is_active = True
        mock_api_key.tier_id = 1

        mock_query = MagicMock()

        def filter_by_side_effect(**kwargs):
            mock_filter = MagicMock()
            if "key_hash" in kwargs:
                mock_filter.first.return_value = mock_api_key
            elif "id" in kwargs:
                mock_filter.first.return_value = mock_tier
            return mock_filter

        mock_query.filter_by = MagicMock(side_effect=filter_by_side_effect)
        mock_session.query.return_value = mock_query

        pipe.execute = AsyncMock(
            side_effect=[
                [0, 0, 0, 0, 0, 0],
                [True, True, True, True, True, True],
            ]
        )

        service = RateLimiterService(
            redis_client=redis,
            get_session=lambda: mock_session,
            enabled=True,
            anon_per_minute=5,
            anon_per_hour=50,
            anon_per_day=500,
        )
        await service.check_rate_limit("glk_test1234", "1.2.3.4")
        # Should have cached the result
        redis.hset.assert_called()

    @pytest.mark.asyncio
    async def test_cache_miss_inactive_key(self):
        redis, pipe = _make_redis_mock()
        redis.hgetall = AsyncMock(return_value={})

        mock_session = MagicMock()
        mock_query = MagicMock()
        mock_filter = MagicMock()
        mock_filter.first.return_value = None  # key not found
        mock_query.filter_by.return_value = mock_filter
        mock_session.query.return_value = mock_query

        service = RateLimiterService(
            redis_client=redis,
            get_session=lambda: mock_session,
            enabled=True,
            anon_per_minute=5,
            anon_per_hour=50,
            anon_per_day=500,
        )
        with pytest.raises(RateLimitExceeded) as exc_info:
            await service.check_rate_limit("glk_unknown", "1.2.3.4")
        assert "Invalid API key" in exc_info.value.message


class TestInvalidateCache:
    @pytest.mark.asyncio
    async def test_invalidate_deletes_cache_key(self):
        redis, _ = _make_redis_mock()
        service, _, _ = _make_service(redis=redis)
        await service.invalidate_key_cache("abc123hash")
        redis.delete.assert_called_once_with("ratelimit:tier:abc123hash")


class TestFromEnvironment:
    def test_creates_with_env_vars(self):
        redis, _ = _make_redis_mock()
        env = {
            "RATE_LIMIT_ENABLED": "true",
            "RATE_LIMIT_ANON_PER_MINUTE": "20",
            "RATE_LIMIT_ANON_PER_HOUR": "200",
            "RATE_LIMIT_ANON_PER_DAY": "2000",
        }
        with patch.dict("os.environ", env):
            service = RateLimiterService.from_environment(
                redis_client=redis,
                get_session=MagicMock(),
            )
        assert service.enabled is True
        assert service._anon_limits.rate_limit_minute == 20
        assert service._anon_limits.rate_limit_hour == 200
        assert service._anon_limits.rate_limit_day == 2000

    def test_defaults_to_disabled(self):
        redis, _ = _make_redis_mock()
        with patch.dict("os.environ", {}, clear=True):
            service = RateLimiterService.from_environment(
                redis_client=redis,
                get_session=MagicMock(),
            )
        assert service.enabled is False
