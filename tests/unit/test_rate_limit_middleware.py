"""Unit tests for RateLimitMiddleware."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from starlette.responses import JSONResponse

from backend.protocol_rpc.rate_limit_middleware import RateLimitMiddleware
from backend.protocol_rpc.exceptions import RateLimitExceeded


def _make_request(
    path="/api",
    method="POST",
    api_key=None,
    client_host="127.0.0.1",
    headers=None,
):
    """Create a mock Starlette Request."""
    headers = headers or {}
    request = MagicMock()
    request.url.path = path
    request.method = method
    request.headers = MagicMock()

    def get_header(key, default=None):
        if key == "X-API-Key":
            return api_key
        return headers.get(key, headers.get(key.lower(), default))

    request.headers.get = MagicMock(side_effect=get_header)
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
    async def test_uses_forwarded_client_ip_from_trusted_proxy(self):
        limiter = AsyncMock()
        limiter.enabled = True
        limiter.check_rate_limit = AsyncMock(return_value=None)
        request = _make_request(
            client_host="127.0.0.1",
            headers={"X-Forwarded-For": "198.51.100.7, 10.0.12.34"},
        )
        request.app.state.rate_limiter = limiter
        call_next = _make_call_next()

        middleware = RateLimitMiddleware(app=MagicMock())
        await middleware.dispatch(request, call_next)

        limiter.check_rate_limit.assert_called_once_with(None, "198.51.100.7")

    @pytest.mark.asyncio
    async def test_ignores_forwarded_client_ip_from_untrusted_peer(self):
        limiter = AsyncMock()
        limiter.enabled = True
        limiter.check_rate_limit = AsyncMock(return_value=None)
        request = _make_request(
            client_host="198.51.100.9",
            headers={"X-Forwarded-For": "203.0.113.7"},
        )
        request.app.state.rate_limiter = limiter
        call_next = _make_call_next()

        middleware = RateLimitMiddleware(app=MagicMock())
        await middleware.dispatch(request, call_next)

        limiter.check_rate_limit.assert_called_once_with(None, "198.51.100.9")

    @pytest.mark.asyncio
    async def test_uses_first_forwarded_ip_when_all_hops_are_trusted(self):
        limiter = AsyncMock()
        limiter.enabled = True
        limiter.check_rate_limit = AsyncMock(return_value=None)
        request = _make_request(
            client_host="127.0.0.1",
            headers={"X-Forwarded-For": "10.0.12.7, 172.16.4.5"},
        )
        request.app.state.rate_limiter = limiter
        call_next = _make_call_next()

        middleware = RateLimitMiddleware(app=MagicMock())
        await middleware.dispatch(request, call_next)

        limiter.check_rate_limit.assert_called_once_with(None, "10.0.12.7")

    @pytest.mark.asyncio
    async def test_uses_real_ip_from_trusted_proxy_when_forwarded_for_missing(self):
        limiter = AsyncMock()
        limiter.enabled = True
        limiter.check_rate_limit = AsyncMock(return_value=None)
        request = _make_request(
            client_host="127.0.0.1",
            headers={"X-Real-IP": "203.0.113.12"},
        )
        request.app.state.rate_limiter = limiter
        call_next = _make_call_next()

        middleware = RateLimitMiddleware(app=MagicMock())
        await middleware.dispatch(request, call_next)

        limiter.check_rate_limit.assert_called_once_with(None, "203.0.113.12")

    @pytest.mark.asyncio
    async def test_falls_back_to_peer_when_forwarded_headers_are_invalid(self):
        limiter = AsyncMock()
        limiter.enabled = True
        limiter.check_rate_limit = AsyncMock(return_value=None)
        request = _make_request(
            client_host="127.0.0.1",
            headers={
                "X-Forwarded-For": "not-an-ip, ",
                "X-Real-IP": "also-not-an-ip",
            },
        )
        request.app.state.rate_limiter = limiter
        call_next = _make_call_next()

        middleware = RateLimitMiddleware(app=MagicMock())
        await middleware.dispatch(request, call_next)

        limiter.check_rate_limit.assert_called_once_with(None, "127.0.0.1")

    @pytest.mark.asyncio
    async def test_invalid_trusted_proxy_config_is_ignored(self, monkeypatch, caplog):
        monkeypatch.setenv(
            "RATE_LIMIT_TRUSTED_PROXIES",
            "127.0.0.1/32,not-a-cidr,",
        )
        limiter = AsyncMock()
        limiter.enabled = True
        limiter.check_rate_limit = AsyncMock(return_value=None)
        request = _make_request(
            client_host="127.0.0.1",
            headers={"X-Forwarded-For": "198.51.100.7"},
        )
        request.app.state.rate_limiter = limiter
        call_next = _make_call_next()

        middleware = RateLimitMiddleware(app=MagicMock())
        await middleware.dispatch(request, call_next)

        assert "Ignoring invalid RATE_LIMIT_TRUSTED_PROXIES entry" in caplog.text
        limiter.check_rate_limit.assert_called_once_with(None, "198.51.100.7")

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

    @pytest.mark.asyncio
    async def test_fails_open_when_rate_limiter_throws_unexpected_error(self):
        limiter = AsyncMock()
        limiter.enabled = True
        limiter.check_rate_limit = AsyncMock(side_effect=RuntimeError("redis down"))
        request = _make_request()
        request.app.state.rate_limiter = limiter
        call_next = _make_call_next()

        middleware = RateLimitMiddleware(app=MagicMock())
        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 200
        call_next.assert_called_once()
