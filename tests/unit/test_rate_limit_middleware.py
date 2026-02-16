"""Unit tests for RateLimitMiddleware."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from starlette.responses import JSONResponse

from backend.protocol_rpc.rate_limit_middleware import RateLimitMiddleware
from backend.protocol_rpc.exceptions import RateLimitExceeded


def _make_request(path="/api", method="POST", api_key=None, client_host="127.0.0.1"):
    """Create a mock Starlette Request."""
    request = MagicMock()
    request.url.path = path
    request.method = method
    request.headers = MagicMock()
    request.headers.get = MagicMock(
        side_effect=lambda k, default=None: api_key if k == "X-API-Key" else default
    )
    request.client = MagicMock()
    request.client.host = client_host
    # app.state for rate_limiter access
    request.app = MagicMock()
    return request


def _make_call_next():
    """Create a mock call_next that returns a 200 response."""
    ok_response = JSONResponse(content={"jsonrpc": "2.0", "result": "ok", "id": 1})
    return AsyncMock(return_value=ok_response)


class TestMiddlewarePassthrough:
    @pytest.mark.asyncio
    async def test_passes_through_when_no_rate_limiter(self):
        request = _make_request()
        request.app.state.rate_limiter = None
        call_next = _make_call_next()

        middleware = RateLimitMiddleware(app=MagicMock())
        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 200
        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_passes_through_when_disabled(self):
        limiter = MagicMock()
        limiter.enabled = False
        request = _make_request()
        request.app.state.rate_limiter = limiter
        call_next = _make_call_next()

        middleware = RateLimitMiddleware(app=MagicMock())
        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 200
        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_passes_through_for_non_api_paths(self):
        limiter = MagicMock()
        limiter.enabled = True
        request = _make_request(path="/health", method="GET")
        request.app.state.rate_limiter = limiter
        call_next = _make_call_next()

        middleware = RateLimitMiddleware(app=MagicMock())
        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 200
        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_passes_through_for_get_on_api(self):
        limiter = MagicMock()
        limiter.enabled = True
        request = _make_request(path="/api", method="GET")
        request.app.state.rate_limiter = limiter
        call_next = _make_call_next()

        middleware = RateLimitMiddleware(app=MagicMock())
        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 200
        call_next.assert_called_once()


class TestMiddlewareRateLimiting:
    @pytest.mark.asyncio
    async def test_allows_when_under_limit(self):
        limiter = AsyncMock()
        limiter.enabled = True
        limiter.check_rate_limit = AsyncMock(return_value=None)
        request = _make_request()
        request.app.state.rate_limiter = limiter
        call_next = _make_call_next()

        middleware = RateLimitMiddleware(app=MagicMock())
        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 200
        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_429_when_rate_limited(self):
        limiter = AsyncMock()
        limiter.enabled = True
        limiter.check_rate_limit = AsyncMock(
            side_effect=RateLimitExceeded(
                message="Rate limit exceeded: 30 requests per minute",
                data={
                    "window": "minute",
                    "limit": 30,
                    "current": 30,
                    "retry_after_seconds": 60,
                },
            )
        )
        request = _make_request()
        request.app.state.rate_limiter = limiter
        call_next = _make_call_next()

        middleware = RateLimitMiddleware(app=MagicMock())
        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 429
        assert response.headers.get("Retry-After") == "60"
        call_next.assert_not_called()
        # Decode response body
        body = response.body.decode()
        import json

        data = json.loads(body)
        assert data["jsonrpc"] == "2.0"
        assert data["error"]["code"] == -32029
        assert data["id"] is None

    @pytest.mark.asyncio
    async def test_passes_api_key_header(self):
        limiter = AsyncMock()
        limiter.enabled = True
        limiter.check_rate_limit = AsyncMock(return_value=None)
        request = _make_request(api_key="glk_testkey123")
        request.app.state.rate_limiter = limiter
        call_next = _make_call_next()

        middleware = RateLimitMiddleware(app=MagicMock())
        await middleware.dispatch(request, call_next)

        limiter.check_rate_limit.assert_called_once_with("glk_testkey123", "127.0.0.1")

    @pytest.mark.asyncio
    async def test_retry_after_header_with_no_data(self):
        limiter = AsyncMock()
        limiter.enabled = True
        limiter.check_rate_limit = AsyncMock(
            side_effect=RateLimitExceeded(
                message="Invalid API key",
                data=None,
            )
        )
        request = _make_request()
        request.app.state.rate_limiter = limiter
        call_next = _make_call_next()

        middleware = RateLimitMiddleware(app=MagicMock())
        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 429
        assert response.headers.get("Retry-After") == "60"  # default
