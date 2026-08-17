"""Unit tests for RateLimitMiddleware."""

import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from starlette.responses import JSONResponse

from backend.protocol_rpc.rate_limit_middleware import RateLimitMiddleware
from backend.protocol_rpc.exceptions import RateLimitExceeded
from backend.protocol_rpc.rate_limiter import RateLimitUsage


def _make_request(
    path="/api",
    method="POST",
    api_key=None,
    client_host="127.0.0.1",
    headers=None,
    body=b'{"jsonrpc":"2.0","method":"eth_sendRawTransaction","params":[],"id":1}',
):
    """Create a mock Starlette Request."""
    headers = headers or {}
    request = MagicMock()
    request.url.path = path
    request.method = method
    request.body = AsyncMock(return_value=body)
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


def test_rpc_app_cors_wraps_rate_limiting_without_credentials():
    from fastapi.middleware.cors import CORSMiddleware

    from backend.protocol_rpc.fastapi_server import app

    cors_class, _, cors_options = app.user_middleware[0]
    rate_limit_class, _, _ = app.user_middleware[1]

    assert cors_class is CORSMiddleware
    assert cors_options["allow_origins"] == ["*"]
    assert cors_options["allow_credentials"] is False
    assert rate_limit_class is RateLimitMiddleware


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

        limiter.check_rate_limit.assert_called_once_with(
            "glk_testkey123", "127.0.0.1", is_cheap_read=False
        )

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

        limiter.check_rate_limit.assert_called_once_with(
            None, "198.51.100.7", is_cheap_read=False
        )

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

        limiter.check_rate_limit.assert_called_once_with(
            None, "198.51.100.9", is_cheap_read=False
        )

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

        limiter.check_rate_limit.assert_called_once_with(
            None, "10.0.12.7", is_cheap_read=False
        )

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

        limiter.check_rate_limit.assert_called_once_with(
            None, "203.0.113.12", is_cheap_read=False
        )

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

        limiter.check_rate_limit.assert_called_once_with(
            None, "127.0.0.1", is_cheap_read=False
        )

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
        limiter.check_rate_limit.assert_called_once_with(
            None, "198.51.100.7", is_cheap_read=False
        )

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


def _rpc_body(*methods):
    if len(methods) == 1:
        return json.dumps(
            {"jsonrpc": "2.0", "method": methods[0], "params": [], "id": 1}
        ).encode()
    return json.dumps(
        [
            {"jsonrpc": "2.0", "method": m, "params": [], "id": i}
            for i, m in enumerate(methods)
        ]
    ).encode()


class TestBucketClassification:
    """The bucket a request lands in is decided from its JSON-RPC method."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method",
        ["gen_getContractCode", "eth_getBalance", "ping", "eth_chainId"],
    )
    async def test_cheap_reads_use_read_bucket(self, method):
        limiter = AsyncMock()
        limiter.enabled = True
        limiter.check_rate_limit = AsyncMock(return_value=None)
        request = _make_request(body=_rpc_body(method))
        request.app.state.rate_limiter = limiter

        middleware = RateLimitMiddleware(app=MagicMock())
        await middleware.dispatch(request, _make_call_next())

        assert limiter.check_rate_limit.call_args.kwargs["is_cheap_read"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method",
        [
            # Each of these looks like a read but reaches the GenVM, so putting
            # any of them in the read bucket would hand out free LLM capacity.
            "eth_call",
            "gen_call",
            "gen_getContractSchema",
            "gen_getContractSchemaForCode",
            "sim_lintContract",
            "eth_sendRawTransaction",
        ],
    )
    async def test_genvm_methods_use_standard_bucket(self, method):
        limiter = AsyncMock()
        limiter.enabled = True
        limiter.check_rate_limit = AsyncMock(return_value=None)
        request = _make_request(body=_rpc_body(method))
        request.app.state.rate_limiter = limiter

        middleware = RateLimitMiddleware(app=MagicMock())
        await middleware.dispatch(request, _make_call_next())

        assert limiter.check_rate_limit.call_args.kwargs["is_cheap_read"] is False

    @pytest.mark.asyncio
    async def test_batch_of_reads_is_cheap(self):
        limiter = AsyncMock()
        limiter.enabled = True
        limiter.check_rate_limit = AsyncMock(return_value=None)
        request = _make_request(body=_rpc_body("ping", "eth_chainId", "eth_getBalance"))
        request.app.state.rate_limiter = limiter

        middleware = RateLimitMiddleware(app=MagicMock())
        await middleware.dispatch(request, _make_call_next())

        assert limiter.check_rate_limit.call_args.kwargs["is_cheap_read"] is True

    @pytest.mark.asyncio
    async def test_batch_with_one_expensive_call_is_not_cheap(self):
        """One costly member must taint the whole batch, or it is a free ride."""
        limiter = AsyncMock()
        limiter.enabled = True
        limiter.check_rate_limit = AsyncMock(return_value=None)
        request = _make_request(body=_rpc_body("ping", "eth_call", "ping"))
        request.app.state.rate_limiter = limiter

        middleware = RateLimitMiddleware(app=MagicMock())
        await middleware.dispatch(request, _make_call_next())

        assert limiter.check_rate_limit.call_args.kwargs["is_cheap_read"] is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "body",
        [
            b"",
            b"not json at all",
            b"{",
            b'{"jsonrpc":"2.0","id":1}',  # no method
            b'{"jsonrpc":"2.0","method":123,"id":1}',  # method not a string
            b"[]",  # empty batch
            b'"just a string"',
            b'{"jsonrpc":"2.0","method":"unknown_future_method","id":1}',
        ],
    )
    async def test_ambiguous_bodies_charge_the_stricter_bucket(self, body):
        limiter = AsyncMock()
        limiter.enabled = True
        limiter.check_rate_limit = AsyncMock(return_value=None)
        request = _make_request(body=body)
        request.app.state.rate_limiter = limiter

        middleware = RateLimitMiddleware(app=MagicMock())
        await middleware.dispatch(request, _make_call_next())

        assert limiter.check_rate_limit.call_args.kwargs["is_cheap_read"] is False

    @pytest.mark.asyncio
    async def test_oversized_body_is_not_parsed(self):
        oversized = b'{"jsonrpc":"2.0","method":"ping","params":["' + (b"x" * 70_000)
        limiter = AsyncMock()
        limiter.enabled = True
        limiter.check_rate_limit = AsyncMock(return_value=None)
        request = _make_request(body=oversized)
        request.app.state.rate_limiter = limiter

        middleware = RateLimitMiddleware(app=MagicMock())
        await middleware.dispatch(request, _make_call_next())

        assert limiter.check_rate_limit.call_args.kwargs["is_cheap_read"] is False

    @pytest.mark.asyncio
    async def test_unreadable_body_does_not_break_the_request(self):
        limiter = AsyncMock()
        limiter.enabled = True
        limiter.check_rate_limit = AsyncMock(return_value=None)
        request = _make_request()
        request.body = AsyncMock(side_effect=RuntimeError("stream consumed"))
        request.app.state.rate_limiter = limiter

        middleware = RateLimitMiddleware(app=MagicMock())
        response = await middleware.dispatch(request, _make_call_next())

        assert response.status_code == 200
        assert limiter.check_rate_limit.call_args.kwargs["is_cheap_read"] is False


class TestRateLimitHeaders:
    @pytest.mark.asyncio
    async def test_usage_headers_on_success(self):
        limiter = AsyncMock()
        limiter.enabled = True
        limiter.check_rate_limit = AsyncMock(
            return_value=RateLimitUsage(
                bucket="read",
                window="minute",
                limit=6000,
                remaining=5987,
                reset_seconds=42,
            )
        )
        request = _make_request(body=_rpc_body("ping"))
        request.app.state.rate_limiter = limiter

        middleware = RateLimitMiddleware(app=MagicMock())
        response = await middleware.dispatch(request, _make_call_next())

        assert response.status_code == 200
        assert response.headers["X-RateLimit-Bucket"] == "read"
        assert response.headers["X-RateLimit-Window"] == "minute"
        assert response.headers["X-RateLimit-Limit"] == "6000"
        assert response.headers["X-RateLimit-Remaining"] == "5987"
        assert response.headers["X-RateLimit-Reset"] == "42"

    @pytest.mark.asyncio
    async def test_no_headers_when_limiter_disabled(self):
        limiter = AsyncMock()
        limiter.enabled = True
        limiter.check_rate_limit = AsyncMock(return_value=None)
        request = _make_request()
        request.app.state.rate_limiter = limiter

        middleware = RateLimitMiddleware(app=MagicMock())
        response = await middleware.dispatch(request, _make_call_next())

        assert "X-RateLimit-Limit" not in response.headers

    @pytest.mark.asyncio
    async def test_usage_headers_on_429(self):
        limiter = AsyncMock()
        limiter.enabled = True
        limiter.check_rate_limit = AsyncMock(
            side_effect=RateLimitExceeded(
                message="Rate limit exceeded: 600 requests per minute",
                data={
                    "bucket": "standard",
                    "window": "minute",
                    "limit": 600,
                    "current": 600,
                    "retry_after_seconds": 17,
                },
            )
        )
        request = _make_request()
        request.app.state.rate_limiter = limiter

        middleware = RateLimitMiddleware(app=MagicMock())
        response = await middleware.dispatch(request, _make_call_next())

        assert response.status_code == 429
        assert response.headers["Retry-After"] == "17"
        assert response.headers["X-RateLimit-Bucket"] == "standard"
        assert response.headers["X-RateLimit-Window"] == "minute"
        assert response.headers["X-RateLimit-Limit"] == "600"
        assert response.headers["X-RateLimit-Remaining"] == "0"
        assert response.headers["X-RateLimit-Reset"] == "17"

    @pytest.mark.asyncio
    async def test_invalid_key_429_has_no_usage_headers(self):
        """No window was evaluated, so there is no headroom to report."""
        limiter = AsyncMock()
        limiter.enabled = True
        limiter.check_rate_limit = AsyncMock(
            side_effect=RateLimitExceeded(message="Invalid API key", data=None)
        )
        request = _make_request()
        request.app.state.rate_limiter = limiter

        middleware = RateLimitMiddleware(app=MagicMock())
        response = await middleware.dispatch(request, _make_call_next())

        assert response.status_code == 429
        assert response.headers["Retry-After"] == "60"
        assert "X-RateLimit-Limit" not in response.headers
