#!/usr/bin/env python
"""
ASGI entry point for FastAPI with native WebSocket support.
"""

import os
import sys

# Set production environment
os.environ.setdefault("UVICORN_WORKER", "true")

# Import FastAPI app
from backend.protocol_rpc.fastapi_server import app

# Export the ASGI application

application = app

if __name__ == "__main__":
    import uvicorn

    # Enable reload in development mode
    is_debug = os.getenv("BACKEND_BUILD_TARGET") == "debug"

    uvicorn.run(
        "asgi:application",
        host="0.0.0.0",
        port=int(os.getenv("RPCPORT", "4000")),
        workers=1 if is_debug else int(os.getenv("WEB_CONCURRENCY", "1")),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
        reload=is_debug,
        reload_dirs=["backend"] if is_debug else None,
        access_log=True,
    )
