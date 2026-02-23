"""
Uvicorn configuration for production deployment.
"""

import os
import multiprocessing

# Server socket
bind = f"0.0.0.0:{os.getenv('RPCPORT', '4000')}"
host = "0.0.0.0"
port = int(os.getenv("RPCPORT", "4000"))

# Worker processes
workers = int(os.getenv("WEB_CONCURRENCY", 1))

# Worker class - use uvloop for better async performance
worker_class = "uvicorn.workers.UvicornWorker"
loop = "uvloop"  # High-performance event loop

# Logging
log_level = os.getenv("LOG_LEVEL", "info").lower()
access_log = True
error_log = "-"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Timeouts
timeout = 60
keepalive = 5

# SSL (if needed)
# ssl_keyfile = '/path/to/keyfile'
# ssl_certfile = '/path/to/certfile'

# Reload
reload = False  # Never use in production

# Process naming
proc_name = "genlayer-asgi"

# Server mechanics
forwarded_allow_ips = "*"  # Be careful with this in production
proxy_headers = True
proxy_protocol = False

# Limits
limit_concurrency = 1000  # Maximum number of concurrent connections
limit_max_requests = 1000  # Restart workers after this many requests

# WebSocket support
ws = "auto"  # Auto-detect WebSocket support
ws_max_size = 16777216  # 16MB max WebSocket message size
ws_ping_interval = 20  # Ping interval for WebSocket keepalive
ws_ping_timeout = 60  # Timeout for WebSocket ping responses
