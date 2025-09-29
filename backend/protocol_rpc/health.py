# backend/protocol_rpc/health.py
import time
import os
from typing import Optional, Union
import logging

from fastapi import APIRouter, FastAPI
from backend.database_handler.session_factory import get_database_manager
from backend.protocol_rpc.fastapi_rpc_router import FastAPIRPCRouter

# Create FastAPI router for health endpoints
health_router = APIRouter(tags=["health"])


@health_router.get("/health")
async def health_check() -> dict:
    """Comprehensive health check endpoint for load balancers and monitoring."""
    start = time.time()

    # Basic health check - we're running
    status = "healthy"

    # Check database connectivity
    db_status = "unknown"
    try:
        db_manager = get_database_manager()
        with db_manager.engine.connect() as conn:
            from sqlalchemy import text

            conn.execute(text("SELECT 1"))
            conn.commit()
        db_status = "healthy"
    except Exception:
        logging.exception("Database health check failed.")
        db_status = "unhealthy"
        status = "degraded"

    # Check Redis (if configured)
    redis_status = "not_configured"
    if os.getenv("REDIS_URL"):
        try:
            import redis

            redis_client = redis.from_url(os.getenv("REDIS_URL"))
            redis_client.ping()
            redis_status = "healthy"
        except Exception:
            redis_status = "unhealthy"
            if status == "healthy":
                status = "degraded"

    # System metrics (optional)
    metrics = {}
    try:
        import psutil

        metrics = {
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "memory_percent": psutil.virtual_memory().percent,
        }
    except ImportError:
        pass

    return {
        "status": status,
        "database": db_status,
        "redis": redis_status,
        "response_time_ms": (time.time() - start) * 1000,
        "worker_pid": os.getpid(),
        "workers": os.getenv("WEB_CONCURRENCY", "1"),
        **metrics,
    }


@health_router.get("/ready")
async def readiness_check():
    """Readiness check to verify the service is ready to accept traffic."""
    return {
        "status": "ready",
        "service": "genlayer-rpc",
    }


def create_readiness_check_with_state(
    source: Union[FastAPI, Optional[FastAPIRPCRouter]]
):
    """Create a readiness check function that evaluates RPC router availability."""

    if isinstance(source, FastAPI):

        def rpc_router_provider() -> Optional[FastAPIRPCRouter]:
            return getattr(source.state, "rpc_router", None)

    else:

        def rpc_router_provider() -> Optional[FastAPIRPCRouter]:
            return source

    async def readiness_check_with_state():
        """Readiness check to verify the service is ready to accept traffic."""
        rpc_router_ready = rpc_router_provider() is not None

        return {
            "status": "ready" if rpc_router_ready else "not_ready",
            "service": "genlayer-rpc",
            "rpc_router_initialized": rpc_router_ready,
        }

    return readiness_check_with_state
