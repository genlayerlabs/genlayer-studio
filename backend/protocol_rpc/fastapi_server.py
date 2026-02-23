# backend/protocol_rpc/fastapi_server.py

import os
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import ClientDisconnect

# Load environment variables early so SENTRY_DSN is available for initialization
load_dotenv()

from backend.protocol_rpc.app_lifespan import RPCAppSettings, rpc_app_lifespan
from backend.protocol_rpc.dependencies import (
    get_rpc_router_optional,
    websocket_broadcast,
)
from backend.protocol_rpc.fastapi_rpc_router import FastAPIRPCRouter
from backend.protocol_rpc.health import health_router
from backend.protocol_rpc.rpc_endpoint_manager import JSONRPCResponse
from backend.protocol_rpc.websocket import GLOBAL_CHANNEL, websocket_handler


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

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include health check endpoints
app.include_router(health_router)


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
