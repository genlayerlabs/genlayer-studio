"""FastAPI middleware for API key rate limiting on the /api endpoint."""

from __future__ import annotations

import logging
import os
from ipaddress import ip_address, ip_network
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from backend.protocol_rpc.exceptions import RateLimitExceeded
from backend.protocol_rpc.rate_limit_methods import is_cheap_read_payload
from backend.protocol_rpc.rate_limiter import RateLimiterService, RateLimitUsage

logger = logging.getLogger(__name__)

DEFAULT_TRUSTED_PROXY_CIDRS = (
    "127.0.0.0/8",
    "10.0.0.0/8",  # NOSONAR - RFC1918 private proxy range.
    "172.16.0.0/12",  # NOSONAR - RFC1918 private proxy range.
    "192.168.0.0/16",  # NOSONAR - RFC1918 private proxy range.
    "::1/128",
    "fc00::/7",
)


def _load_trusted_proxy_networks():
    raw = os.environ.get(
        "RATE_LIMIT_TRUSTED_PROXIES",
        ",".join(DEFAULT_TRUSTED_PROXY_CIDRS),
    )
    networks = []
    for value in raw.split(","):
        value = value.strip()
        if not value:
            continue
        try:
            networks.append(ip_network(value, strict=False))
        except ValueError:
            logger.warning("Ignoring invalid RATE_LIMIT_TRUSTED_PROXIES entry")
    return tuple(networks)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Intercepts /api requests to enforce tiered rate limits."""

    def __init__(self, app, dispatch=None):
        super().__init__(app, dispatch=dispatch)
        self._trusted_proxy_networks = _load_trusted_proxy_networks()

    async def dispatch(self, request: Request, call_next) -> Response:
        # Only rate-limit the JSON-RPC endpoint
        if request.url.path != "/api" or request.method != "POST":
            return await call_next(request)

        rate_limiter: Optional[RateLimiterService] = getattr(
            request.app.state, "rate_limiter", None
        )
        if rate_limiter is None or not rate_limiter.enabled:
            return await call_next(request)

        api_key = request.headers.get("X-API-Key")
        client_ip = self._client_ip(request)
        is_cheap_read = await self._is_cheap_read(request)

        usage: Optional[RateLimitUsage] = None
        try:
            usage = await rate_limiter.check_rate_limit(
                api_key, client_ip, is_cheap_read=is_cheap_read
            )
        except RateLimitExceeded as exc:
            return JSONResponse(
                status_code=429,
                content={
                    "jsonrpc": "2.0",
                    "error": exc.to_dict(),
                    "id": None,
                },
                headers=self._denial_headers(exc),
            )
        except Exception:
            logger.warning(
                "Rate limiter unavailable, allowing request through",
                exc_info=True,
            )

        response = await call_next(request)
        if usage is not None:
            response.headers.update(usage.as_headers())
        return response

    async def _is_cheap_read(self, request: Request) -> bool:
        """Classify the request body, charging the stricter bucket on any doubt.

        Reading the body here is safe because Starlette's BaseHTTPMiddleware
        wraps the request in a _CachedRequest, which replays the buffered body
        downstream. Calling request.stream() instead would starve the route
        handler.
        """
        try:
            raw_body = await request.body()
        except Exception:
            logger.warning(
                "Could not read request body to classify rate limit bucket",
                exc_info=True,
            )
            return False
        return is_cheap_read_payload(raw_body)

    @staticmethod
    def _denial_headers(exc: RateLimitExceeded) -> dict:
        data = exc.data if isinstance(exc.data, dict) else {}
        retry_after = data.get("retry_after_seconds", 60)
        headers = {"Retry-After": str(retry_after)}
        # An invalid API key is raised before any window is evaluated, so there
        # is no usage to report — only the windowed denials carry limits.
        if "limit" in data:
            headers.update(
                {
                    "X-RateLimit-Bucket": str(data.get("bucket", "standard")),
                    "X-RateLimit-Window": str(data.get("window", "")),
                    "X-RateLimit-Limit": str(data["limit"]),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(retry_after),
                }
            )
        return headers

    def _client_ip(self, request: Request) -> str:
        peer_host = request.client.host if request.client else "unknown"
        if not self._is_trusted_proxy(peer_host):
            return peer_host

        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            forwarded_ip = self._forwarded_client_ip(forwarded_for)
            if forwarded_ip is not None:
                return forwarded_ip

        real_ip = self._valid_ip_header(request.headers.get("X-Real-IP"))
        if real_ip:
            return real_ip

        return peer_host

    def _forwarded_client_ip(self, forwarded_for: str) -> Optional[str]:
        parsed = self._parse_forwarded_for(forwarded_for)
        for value, parsed_ip in reversed(parsed):
            if not self._is_trusted_ip(parsed_ip):
                return value
        if parsed:
            return parsed[0][0]
        return None

    def _parse_forwarded_for(self, forwarded_for: str) -> list[tuple[str, object]]:
        parsed = []
        for value in forwarded_for.split(","):
            value = value.strip()
            if not value:
                continue
            try:
                parsed.append((value, ip_address(value)))
            except ValueError:
                continue
        return parsed

    def _valid_ip_header(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        value = value.strip()
        try:
            ip_address(value)
        except ValueError:
            return None
        return value

    def _is_trusted_proxy(self, host: str) -> bool:
        try:
            return self._is_trusted_ip(ip_address(host))
        except ValueError:
            return False

    def _is_trusted_ip(self, parsed_ip) -> bool:
        return any(parsed_ip in network for network in self._trusted_proxy_networks)
