"""FastAPI middleware for API key rate limiting on the /api endpoint."""

from __future__ import annotations

import logging
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from backend.protocol_rpc.exceptions import RateLimitExceeded
from backend.protocol_rpc.rate_limiter import RateLimiterService

logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Intercepts /api requests to enforce tiered rate limits."""

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
        client_ip = request.client.host if request.client else "unknown"

        try:
            await rate_limiter.check_rate_limit(api_key, client_ip)
        except RateLimitExceeded as exc:
            retry_after = "60"
            if exc.data and isinstance(exc.data, dict):
                retry_after = str(exc.data.get("retry_after_seconds", 60))
            return JSONResponse(
                status_code=429,
                content={
                    "jsonrpc": "2.0",
                    "error": exc.to_dict(),
                    "id": None,
                },
                headers={"Retry-After": retry_after},
            )
        except Exception:
            logger.warning(
                "Rate limiter unavailable, allowing request through",
                exc_info=True,
            )

        return await call_next(request)
