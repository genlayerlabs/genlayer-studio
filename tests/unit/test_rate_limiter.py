"""Unit tests for RateLimiterService."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.protocol_rpc.rate_limiter import RateLimiterService
from backend.protocol_rpc.exceptions import RateLimitExceeded

# [allowed, window, limit, count, reset] — the shape the Lua script returns.
_ALLOWED = [0, b"minute", 5, 1, 60]


def _make_redis_mock():
    """Create a mock Redis client with Lua script support."""
    redis = AsyncMock()
    redis.script_load = AsyncMock(return_value="fake_sha")
    redis.evalsha = AsyncMock(return_value=_ALLOWED)  # default: allow
    redis.hgetall = AsyncMock(return_value={})
    redis.hset = AsyncMock()
    redis.expire = AsyncMock()
    redis.delete = AsyncMock()
    return redis


def _make_service(redis=None, enabled=True):
    """Create a RateLimiterService with mocked dependencies."""
    if redis is None:
        redis = _make_redis_mock()
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
    )


class TestDisabledMode:
    @pytest.mark.asyncio
    async def test_check_does_nothing_when_disabled(self):
        service, redis = _make_service(enabled=False)
        await service.check_rate_limit("some-key", "1.2.3.4")
        redis.evalsha.assert_not_called()

    def test_enabled_property(self):
        service, _ = _make_service(enabled=False)
        assert service.enabled is False

        service2, _ = _make_service(enabled=True)
        assert service2.enabled is True


class TestAnonymousRateLimiting:
    @pytest.mark.asyncio
    async def test_allowed_when_under_limit(self):
        redis = _make_redis_mock()
        redis.evalsha = AsyncMock(return_value=_ALLOWED)
        service, _ = _make_service(redis=redis)
        # Should not raise
        await service.check_rate_limit(None, "1.2.3.4")
        redis.evalsha.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_when_minute_limit_exceeded(self):
        redis = _make_redis_mock()
        redis.evalsha = AsyncMock(return_value=[1, b"minute", 5, 5, 60])
        service, _ = _make_service(redis=redis)
        with pytest.raises(RateLimitExceeded) as exc_info:
            await service.check_rate_limit(None, "1.2.3.4")
        assert exc_info.value.code == -32029
        assert exc_info.value.data["window"] == "minute"
        assert exc_info.value.data["limit"] == 5

    @pytest.mark.asyncio
    async def test_raises_when_hour_limit_exceeded(self):
        redis = _make_redis_mock()
        redis.evalsha = AsyncMock(return_value=[1, b"hour", 50, 50, 3600])
        service, _ = _make_service(redis=redis)
        with pytest.raises(RateLimitExceeded) as exc_info:
            await service.check_rate_limit(None, "1.2.3.4")
        assert exc_info.value.data["window"] == "hour"

    @pytest.mark.asyncio
    async def test_raises_when_day_limit_exceeded(self):
        redis = _make_redis_mock()
        redis.evalsha = AsyncMock(return_value=[1, b"day", 500, 500, 86400])
        service, _ = _make_service(redis=redis)
        with pytest.raises(RateLimitExceeded) as exc_info:
            await service.check_rate_limit(None, "1.2.3.4")
        assert exc_info.value.data["window"] == "day"

    @pytest.mark.asyncio
    async def test_identity_uses_ip(self):
        redis = _make_redis_mock()
        redis.evalsha = AsyncMock(return_value=_ALLOWED)
        service, _ = _make_service(redis=redis)
        await service.check_rate_limit(None, "10.0.0.1")
        # Verify keys passed to evalsha contain the IP
        call_args = redis.evalsha.call_args
        keys_passed = call_args[0][2:5]  # sha, num_keys, key1, key2, key3
        assert all("ip:10.0.0.1" in k for k in keys_passed)

    @pytest.mark.asyncio
    async def test_lua_script_loaded_once(self):
        redis = _make_redis_mock()
        redis.evalsha = AsyncMock(return_value=_ALLOWED)
        service, _ = _make_service(redis=redis)
        await service.check_rate_limit(None, "1.2.3.4")
        await service.check_rate_limit(None, "1.2.3.4")
        # script_load should be called only once
        redis.script_load.assert_called_once()

    @pytest.mark.asyncio
    async def test_lua_script_reloaded_on_noscript_error(self):
        from redis.exceptions import NoScriptError

        redis_mock = _make_redis_mock()
        redis_mock.evalsha = AsyncMock(
            side_effect=[NoScriptError("NOSCRIPT"), _ALLOWED]
        )
        redis_mock.script_load = AsyncMock(return_value="new_sha")
        service, _ = _make_service(redis=redis_mock)
        # Should not raise — recovers by reloading the script
        await service.check_rate_limit(None, "1.2.3.4")
        assert redis_mock.script_load.call_count == 2

    @pytest.mark.asyncio
    async def test_passes_correct_limits_as_args(self):
        redis = _make_redis_mock()
        redis.evalsha = AsyncMock(return_value=_ALLOWED)
        service, _ = _make_service(redis=redis)
        await service.check_rate_limit(None, "1.2.3.4")
        call_args = redis.evalsha.call_args[0]
        # call_args layout: (sha, 3, key1, key2, key3, now, member, "60", "5", "3600", "50", "86400", "500")
        # Index:              0    1   2     3     4     5    6       7      8     9      10    11      12
        args_start = 7  # after sha + num_keys + 3 keys + now + member
        assert call_args[args_start] == "60"  # minute window
        assert call_args[args_start + 1] == "5"  # minute limit
        assert call_args[args_start + 2] == "3600"  # hour window
        assert call_args[args_start + 3] == "50"  # hour limit
        assert call_args[args_start + 4] == "86400"  # day window
        assert call_args[args_start + 5] == "500"  # day limit


class TestApiKeyResolution:
    @pytest.mark.asyncio
    async def test_invalid_key_raises(self):
        redis = _make_redis_mock()
        # Cache returns inactive
        redis.hgetall = AsyncMock(return_value={"status": "inactive"})
        service, _ = _make_service(redis=redis)
        with pytest.raises(RateLimitExceeded) as exc_info:
            await service.check_rate_limit("bad-key", "1.2.3.4")
        assert "Invalid API key" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_cached_key_uses_cached_limits(self):
        redis = _make_redis_mock()
        redis.hgetall = AsyncMock(
            return_value={
                "status": "active",
                "name": "pro",
                "rpm": "120",
                "rph": "3000",
                "rpd": "50000",
            }
        )
        redis.evalsha = AsyncMock(return_value=_ALLOWED)
        service, _ = _make_service(redis=redis)
        await service.check_rate_limit("glk_test1234", "1.2.3.4")
        # Verify it used the cached limits (didn't query DB)
        service._get_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_miss_queries_db(self):
        redis = _make_redis_mock()
        # Cache miss
        redis.hgetall = AsyncMock(return_value={})
        redis.evalsha = AsyncMock(return_value=_ALLOWED)

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
        redis = _make_redis_mock()
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
        redis = _make_redis_mock()
        service, _ = _make_service(redis=redis)
        await service.invalidate_key_cache("abc123hash")
        redis.delete.assert_called_once_with("ratelimit:tier:abc123hash")


class TestFromEnvironment:
    def test_creates_with_env_vars(self):
        redis = _make_redis_mock()
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
        redis = _make_redis_mock()
        with patch.dict("os.environ", {}, clear=True):
            service = RateLimiterService.from_environment(
                redis_client=redis,
                get_session=MagicMock(),
            )
        assert service.enabled is False
        assert service._anon_limits.rate_limit_minute == 30
        assert service._anon_limits.rate_limit_hour == 500
        assert service._anon_limits.rate_limit_day == 5000


class TestReadBucket:
    """Cheap reads are metered separately from GenVM-bound calls."""

    @pytest.mark.asyncio
    async def test_read_bucket_uses_separate_keys(self):
        service, redis = _make_service()
        await service.check_rate_limit(None, "1.2.3.4", is_cheap_read=True)

        keys = redis.evalsha.call_args[0][2:5]
        assert keys == (
            "ratelimit:ip:1.2.3.4:read:minute",
            "ratelimit:ip:1.2.3.4:read:hour",
            "ratelimit:ip:1.2.3.4:read:day",
        )

    @pytest.mark.asyncio
    async def test_standard_bucket_keeps_original_keys(self):
        """Key shape must not change, or limits in flight silently reset."""
        service, redis = _make_service()
        await service.check_rate_limit(None, "1.2.3.4")

        keys = redis.evalsha.call_args[0][2:5]
        assert keys == (
            "ratelimit:ip:1.2.3.4:minute",
            "ratelimit:ip:1.2.3.4:hour",
            "ratelimit:ip:1.2.3.4:day",
        )

    @pytest.mark.asyncio
    async def test_read_limits_are_scaled_by_multiplier(self):
        redis = _make_redis_mock()
        service = RateLimiterService(
            redis_client=redis,
            get_session=MagicMock(),
            enabled=True,
            anon_per_minute=5,
            anon_per_hour=50,
            anon_per_day=500,
            read_multiplier=10,
        )
        await service.check_rate_limit(None, "1.2.3.4", is_cheap_read=True)

        args = redis.evalsha.call_args[0][5:]
        # [now, member, 60, rpm, 3600, rph, 86400, rpd]
        assert args[3] == "50"
        assert args[5] == "500"
        assert args[7] == "5000"

    @pytest.mark.asyncio
    async def test_standard_limits_are_not_scaled(self):
        service, redis = _make_service()
        await service.check_rate_limit(None, "1.2.3.4")

        args = redis.evalsha.call_args[0][5:]
        assert args[3] == "5"
        assert args[5] == "50"
        assert args[7] == "500"

    @pytest.mark.asyncio
    async def test_multiplier_below_one_is_clamped(self):
        """A misconfigured multiplier must never make reads stricter."""
        redis = _make_redis_mock()
        service = RateLimiterService(
            redis_client=redis,
            get_session=MagicMock(),
            enabled=True,
            anon_per_minute=5,
            anon_per_hour=50,
            anon_per_day=500,
            read_multiplier=0,
        )
        await service.check_rate_limit(None, "1.2.3.4", is_cheap_read=True)

        assert redis.evalsha.call_args[0][8] == "5"


class TestUsageReporting:
    @pytest.mark.asyncio
    async def test_returns_usage_for_tightest_window(self):
        redis = _make_redis_mock()
        redis.evalsha = AsyncMock(return_value=[0, b"day", 500, 498, 3600])
        service, _ = _make_service(redis=redis)

        usage = await service.check_rate_limit(None, "1.2.3.4")

        assert usage.bucket == "standard"
        assert usage.window == "day"
        assert usage.limit == 500
        assert usage.remaining == 2
        assert usage.reset_seconds == 3600

    @pytest.mark.asyncio
    async def test_usage_reports_read_bucket(self):
        service, _ = _make_service()
        usage = await service.check_rate_limit(None, "1.2.3.4", is_cheap_read=True)
        assert usage.bucket == "read"

    @pytest.mark.asyncio
    async def test_remaining_never_goes_negative(self):
        redis = _make_redis_mock()
        redis.evalsha = AsyncMock(return_value=[0, b"minute", 5, 9, 60])
        service, _ = _make_service(redis=redis)

        usage = await service.check_rate_limit(None, "1.2.3.4")

        assert usage.remaining == 0

    @pytest.mark.asyncio
    async def test_returns_none_when_disabled(self):
        service, _ = _make_service(enabled=False)
        assert await service.check_rate_limit(None, "1.2.3.4") is None

    @pytest.mark.asyncio
    async def test_denial_carries_bucket_for_headers(self):
        redis = _make_redis_mock()
        redis.evalsha = AsyncMock(return_value=[1, b"minute", 50, 50, 12])
        service, _ = _make_service(redis=redis)

        with pytest.raises(RateLimitExceeded) as exc_info:
            await service.check_rate_limit(None, "1.2.3.4", is_cheap_read=True)

        assert exc_info.value.data["bucket"] == "read"
        assert exc_info.value.data["retry_after_seconds"] == 12
