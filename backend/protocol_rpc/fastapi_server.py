# backend/protocol_rpc/fastapi_server.py

import os
import logging
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect

# Load environment variables early so SENTRY_DSN is available for initialization
load_dotenv()

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

logger = logging.getLogger(__name__)

SENTRY_DSN = os.getenv("SENTRY_DSN", None)
if SENTRY_DSN:
    import sentry_sdk

    sentry_sdk.init(
        dsn=SENTRY_DSN,
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

# Security Fix: Require an explicit CORS allowlist from the environment.
# Fail closed (prevent startup) if the configuration is missing to avoid permissive wildcard access.
allowed_origins_env = os.getenv("ALLOWED_ORIGINS", "")
if not allowed_origins_env:
    raise RuntimeError("ALLOWED_ORIGINS environment variable must be explicitly set to configure CORS.")

allowed_origins = [origin.strip() for origin in allowed_origins_env.split(",") if origin.strip()]

# This public RPC uses header-based API keys and no cookie authentication.
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include health check endpoints
app.include_router(health_router)

# Include explorer API endpoints
app.include_router(explorer_router)


# JSON-RPC endpoint (supports single and batch requests)
@app.post("/api")
async def jsonrpc_endpoint(
    request: Request,
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
        # Security Fix: Log the actual exception securely on the server side
        # and return a stable, generic error message to the client to prevent internal state leakage.
        logger.error(f"[JSON-RPC] Request processing failed: {exc}", exc_info=True)
        
        error = {
            "code": -32603,
            "message": "Internal error",
            "data": {"detail": "An unexpected server error occurred."},
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