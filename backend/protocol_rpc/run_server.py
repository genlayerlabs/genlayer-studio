#!/usr/bin/env python3
"""
Entry point for running the JSONRPC server with proper logging configuration.
This script configures uvicorn logging before starting the server.
"""

import os
import sys

# Add the app directory to the path
sys.path.insert(0, "/app")

from backend.protocol_rpc.logging_config import get_uvicorn_log_config


def main():
    import uvicorn

    port = int(os.getenv("RPCPORT", "4000"))
    workers = int(os.getenv("WEB_CONCURRENCY", "1"))
    log_level = os.getenv("LOG_LEVEL", "info").lower()

    uvicorn.run(
        "asgi:application",
        host="0.0.0.0",
        port=port,
        workers=workers,
        log_level=log_level,
        log_config=get_uvicorn_log_config(),
    )


if __name__ == "__main__":
    main()
