#!/usr/bin/env python
"""
ASGI entry point for FastAPI with native WebSocket support.
"""

import os
import sys

# Set production environment
os.environ.setdefault('FLASK_ENV', 'production')
os.environ.setdefault('UVICORN_WORKER', 'true')

# Import FastAPI app
from backend.protocol_rpc.fastapi_server import app

# Export the ASGI application
application = app

if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "asgi:application",
        host="0.0.0.0",
        port=int(os.getenv("RPCPORT", "4000")),
        workers=int(os.getenv("WEB_CONCURRENCY", "1")),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
        reload=False,
        access_log=True,
    )