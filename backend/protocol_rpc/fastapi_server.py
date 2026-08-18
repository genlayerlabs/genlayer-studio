# backend/protocol_rpc/fastapi_server.py

import os
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect

# Load environment variables early so SENTRY_DSN is available for initialization
load_dotenv()

from backend.protocol_rpc.api_key_redaction import (
    install_log_redaction,
    scrub_sentry_event,
)
from backend.protocol_rpc.app_lifespan import RPCAppSettings, rpc_app_lifespan
from backend.protocol_rpc.dependencies import (
    get_rpc_router_optional,
    websocket_broadcast,
)
from backend.protocol_rpc.fastapi_rpc_router import FastAPIRPCRouter
from backend.protocol_rpc.explorer.router import explorer_router
from backend.protocol_rpc.health import health_router
from backend.protocol_rpc.rate_limit_middleware import RateLimitMiddleware
from backend.protocol_rpc.rpc_endpoint_manager import JSONRPCResponse
from backend.protocol_rpc.websocket import GLOBAL_CHANNEL, websocket_handler


install_log_redaction()

SENTRY_DSN = os.getenv("SENTRY_DSN", None)
if SENTRY_DSN:
    import sentry_sdk

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        # API keys can arrive as a path segment, and with send_default_pii and
        # full trace sampling below, request URLs reach Sentry on *every*
        # transaction. Scrub keys out before anything leaves the process.
        before_send=scrub_sentry_event,
        before_send_transaction=scrub_sentry_event,
        # Add data like request headers and IP for users,
        # see https://docs.sentry.io/platforms/python/data-management/data-collected/ for more info
        send_default_pii=True,
        # Set traces_sample_rate to 1.0 to capture 100%
        # of transactions for tracing.
        traces_sample_rate=1.0,
        # Set profile_session_sample_rate to 1.0 to profile 100%
        # of profile sessions.
        profile_session_sample_rate=1.0,
        # Set profile_lifecycle to "trace" to automatically
        # run the profiler on when there is an active transaction
        profile_lifecycle="trace",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage FastAPI application lifecycle."""

    settings = RPCAppSettings.from_environment()

    async with rpc_app_lifespan(app, settings) as app_state:
        app_state.apply_to_app(app)
        yield


# Create FastAPI app
app = FastAPI(title="GenLayer Studio RPC API", version="1.0.0", lifespan=lifespan)

# Rate limiting is inner so CORS decorates short-circuit responses such as 429s.
app.add_middleware(RateLimitMiddleware)

# This public RPC uses header-based API keys and no cookie authentication.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    # Browsers hide non-simple response headers from JS unless they are listed
    # here, so without this the rate limit headers are readable by curl but not
    # by genlayer-js in the browser — the client that most needs to self-pace.
    expose_headers=[
        "Retry-After",
        "X-RateLimit-Bucket",
        "X-RateLimit-Window",
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "X-RateLimit-Reset",
    ],
)

# Include health check endpoints
app.include_router(health_router)

# Include explorer API endpoints
app.include_router(explorer_router)


# JSON-RPC endpoint (supports single and batch requests)
#
# `/api/{api_key}` is the same endpoint with the key in the URL, matching how
# every major RPC provider does it (Alchemy `/v2/<key>`, Infura `/v3/<key>`).
# The EVM toolchain takes a single URL string and has nowhere to put a custom
# header — MetaMask's "Add network" being the clearest case — so the header
# form alone makes Studio unusable from those tools without a proxy in front.
#
# The key is consumed by RateLimitMiddleware before the request reaches here;
# the path parameter exists only so the route matches. Anything that changes
# which paths this route accepts must change `_is_rpc_path` in that middleware
# to match, or the new paths become unlimited and unauthenticated.
@app.post("/api")
@app.post("/api/{api_key}")
async def jsonrpc_endpoint(
    request: Request,
    api_key: str | None = None,
    rpc_router: FastAPIRPCRouter | None = Depends(get_rpc_router_optional),
):
    """Main JSON-RPC endpoint with JSON-RPC 2.0 batch support."""
    if rpc_router is None:
        response = JSONRPCResponse(
            jsonrpc="2.0",
            error={"code": -32603, "message": "RPC router not initialized"},
            id=None,
        )
        return JSONResponse(content=response.model_dump(exclude_none=True))

    try:
        return await rpc_router.handle_http_request(request)
    except ClientDisconnect:
        return Response(status_code=204)
    except Exception as exc:
        # Ensure JSON-RPC compliant error response instead of framework HTML pages
        error = {
            "code": -32603,
            "message": "Internal error",
            "data": {"detail": str(exc)},
        }
        return JSONResponse(content={"jsonrpc": "2.0", "error": error, "id": None})


# WebSocket endpoint with native WebSocket support
@app.websocket("/socket.io/")
async def websocket_socketio_endpoint(
    websocket: WebSocket,
    broadcast=Depends(websocket_broadcast),
):
    """Socket.IO-compatible WebSocket endpoint."""
    return await websocket_handler(websocket, broadcast)


@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    broadcast=Depends(websocket_broadcast),
):
    """Standard WebSocket endpoint."""
    return await websocket_handler(websocket, broadcast)


# Method to emit events (to be used by other parts of the application)
async def emit_event(room: str, event: str, data: Any) -> None:
    """Emit an event to all clients in a room."""
    emit_fn = getattr(app.state, "emit_event", None)
    if emit_fn is not None:
        await emit_fn(room or GLOBAL_CHANNEL, event, data)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("RPCPORT", "4000")),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
        access_log=True,
    )
