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

from backend.protocol_rpc.app_lifespan import RPCAppSettings, rpc_app_lifespan
from backend.protocol_rpc.dependencies import (
    get_rpc_router_optional,
    websocket_broadcast,
)
from backend.protocol_rpc.fastapi_rpc_router import FastAPIRPCRouter
from backend.protocol_rpc.health import health_router, create_readiness_check_with_state
from backend.protocol_rpc.rpc_endpoint_manager import JSONRPCResponse
from backend.protocol_rpc.websocket import GLOBAL_CHANNEL, websocket_handler


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage FastAPI application lifecycle."""

    load_dotenv()
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

# Mount static files for monitoring dashboard
monitoring_dashboard_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "monitoring-dashboard"
)
if os.path.exists(monitoring_dashboard_path):
    app.mount(
        "/monitoring",
        StaticFiles(directory=monitoring_dashboard_path, html=True),
        name="monitoring",
    )


@app.get("/ready")
async def readiness_check_with_app_state(
    rpc_router: FastAPIRPCRouter | None = Depends(get_rpc_router_optional),
):
    """Enhanced readiness check with access to application state."""
    readiness_func = create_readiness_check_with_state(rpc_router)
    return await readiness_func()


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
