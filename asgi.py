#!/usr/bin/env python
"""
ASGI entry point for Uvicorn deployment.
This properly handles async Flask app with Socket.IO support.
"""

import os
import sys
import asyncio

# Set production environment
os.environ.setdefault('FLASK_ENV', 'production')
os.environ.setdefault('UVICORN_WORKER', 'true')

# Import asgiref for Flask-to-ASGI conversion
from asgiref.wsgi import WsgiToAsgi

# Import the Flask app and socketio instance
from backend.protocol_rpc.server import app, socketio

# Create ASGI application from Flask WSGI app
# This allows async handling of requests
application = WsgiToAsgi(app)

# For direct testing
if __name__ == "__main__":
    import uvicorn
    
    # Run with uvicorn
    uvicorn.run(
        "asgi:application",
        host="0.0.0.0",
        port=int(os.getenv("RPCPORT", "4000")),
        workers=int(os.getenv("WEB_CONCURRENCY", "1")),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
        reload=False,
        access_log=True,
    )