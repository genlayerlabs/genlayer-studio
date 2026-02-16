"""Unit tests for RateLimitMiddleware."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

from backend.protocol_rpc.rate_limit_middleware import RateLimitMiddleware
from backend.protocol_rpc.exceptions import RateLimitExceeded


def _make_app(rate_limiter=None):
    """Create a minimal FastAPI app with RateLimitMiddleware."""
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)

    @app.post("/api")
    async def api_endpoint():
        return {"jsonrpc": "2.0", "result": "ok", "id": 1}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    # Set rate_limiter on app state
    app.state.rate_limiter = rate_limiter
    return app


class TestMiddlewarePassthrough:
    def test_passes_through_when_no_rate_limiter(self):
        app = _make_app(rate_limiter=None)
        client = TestClient(app)
        response = client.post(
            "/api",
            json={"jsonrpc": "2.0", "method": "ping", "id": 1},
        )
        assert response.status_code == 200

    def test_passes_through_when_disabled(self):
        limiter = MagicMock()
        limiter.enabled = False
        app = _make_app(rate_limiter=limiter)
        client = TestClient(app)
        response = client.post(
            "/api",
            json={"jsonrpc": "2.0", "method": "ping", "id": 1},
        )
        assert response.status_code == 200

    def test_passes_through_for_non_api_paths(self):
        limiter = MagicMock()
        limiter.enabled = True
        app = _make_app(rate_limiter=limiter)
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200

    def test_passes_through_for_get_on_api(self):
        limiter = MagicMock()
        limiter.enabled = True
        app = _make_app(rate_limiter=limiter)
        client = TestClient(app)
        # GET /api should not be rate limited (only POST)
        response = client.get("/api")
        # Will get 405 Method Not Allowed since route only supports POST,
        # but the point is the middleware shouldn't block it
        assert response.status_code == 405


class TestMiddlewareRateLimiting:
    def test_allows_when_under_limit(self):
        limiter = AsyncMock()
        limiter.enabled = True
        limiter.check_rate_limit = AsyncMock(return_value=None)
        app = _make_app(rate_limiter=limiter)
        client = TestClient(app)
        response = client.post(
            "/api",
            json={"jsonrpc": "2.0", "method": "ping", "id": 1},
        )
        assert response.status_code == 200

    def test_returns_429_when_rate_limited(self):
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
        app = _make_app(rate_limiter=limiter)
        client = TestClient(app)
        response = client.post(
            "/api",
            json={"jsonrpc": "2.0", "method": "ping", "id": 1},
        )
        assert response.status_code == 429
        body = response.json()
        assert body["jsonrpc"] == "2.0"
        assert body["error"]["code"] == -32029
        assert body["id"] is None
        assert response.headers.get("Retry-After") == "60"

    def test_passes_api_key_header(self):
        limiter = AsyncMock()
        limiter.enabled = True
        limiter.check_rate_limit = AsyncMock(return_value=None)
        app = _make_app(rate_limiter=limiter)
        client = TestClient(app)
        response = client.post(
            "/api",
            json={"jsonrpc": "2.0", "method": "ping", "id": 1},
            headers={"X-API-Key": "glk_testkey123"},
        )
        assert response.status_code == 200
        # Verify the API key was passed to check_rate_limit
        limiter.check_rate_limit.assert_called_once()
        call_args = limiter.check_rate_limit.call_args
        assert call_args[0][0] == "glk_testkey123"  # api_key argument

    def test_retry_after_header_with_no_data(self):
        limiter = AsyncMock()
        limiter.enabled = True
        limiter.check_rate_limit = AsyncMock(
            side_effect=RateLimitExceeded(
                message="Invalid API key",
                data=None,
            )
        )
        app = _make_app(rate_limiter=limiter)
        client = TestClient(app)
        response = client.post(
            "/api",
            json={"jsonrpc": "2.0", "method": "ping", "id": 1},
        )
        assert response.status_code == 429
        assert response.headers.get("Retry-After") == "60"  # default
