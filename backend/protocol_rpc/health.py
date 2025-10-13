# backend/protocol_rpc/health.py
import time
import os
from typing import Optional, Union, Dict, Any
import logging
import asyncio

from fastapi import APIRouter, FastAPI, Depends
from backend.database_handler.session_factory import get_database_manager
from backend.protocol_rpc.fastapi_rpc_router import FastAPIRPCRouter
from backend.protocol_rpc.dependencies import get_rpc_router_optional

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
            "cpu_percent": psutil.cpu_percent(interval=None),
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
    source: Union[FastAPI, Optional[FastAPIRPCRouter]],
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


@health_router.get("/health/tasks")
async def health_tasks() -> Dict[str, Any]:
    """Show status of background tasks and monitoring information."""
    try:
        from backend.consensus.monitoring import get_monitor

        monitor = get_monitor()
        status = monitor.get_status()

        # Add task health assessment
        all_healthy = True
        if status.get("stale_tasks"):
            all_healthy = False

        task_health = "healthy" if all_healthy else "degraded"

        return {
            "status": task_health,
            "uptime_seconds": status.get("uptime_seconds", 0),
            "active_tasks": status.get("active_tasks", 0),
            "stale_tasks": len(status.get("stale_tasks", [])),
            "stale_task_details": status.get("stale_tasks", []),
            "tasks": status.get("tasks", {}),
            "memory_usage_mb": status.get("memory_usage_mb", 0),
            "cpu_percent": status.get("cpu_percent", 0),
        }
    except Exception as e:
        logging.exception("Task health check failed")
        return {"status": "error", "error": str(e)}


@health_router.get("/health/db")
async def health_database() -> Dict[str, Any]:
    """Show database connection pool statistics and session tracking."""
    try:
        from backend.consensus.monitoring import get_monitor
        from sqlalchemy import text

        monitor = get_monitor()
        status = monitor.get_status()

        db_manager = get_database_manager()
        pool_status = {}

        # Get connection pool stats if available
        try:
            pool = db_manager.engine.pool
            pool_status = {"class": pool.__class__.__name__}

            # Try to get pool statistics based on pool type
            if hasattr(pool, "status"):
                # Some pools have a status() method
                try:
                    pool_status["status"] = pool.status()
                except:
                    pass

            # Try common pool attributes
            if hasattr(pool, "size"):
                try:
                    pool_status["size"] = pool.size()
                except:
                    pass

            if hasattr(pool, "checkedout"):
                try:
                    pool_status["checked_out"] = pool.checkedout()
                except:
                    pass

            if hasattr(pool, "overflow"):
                try:
                    pool_status["overflow"] = pool.overflow()
                except:
                    pass

            # Calculate total if we have the components
            if "checked_out" in pool_status and "overflow" in pool_status:
                pool_status["total"] = (
                    pool_status["checked_out"] + pool_status["overflow"]
                )

            # For QueuePool specifically, try to get more info
            if pool.__class__.__name__ == "QueuePool":
                if hasattr(pool, "_pool"):
                    # Internal pool queue
                    try:
                        pool_status["available"] = (
                            pool._pool.qsize() if hasattr(pool._pool, "qsize") else None
                        )
                    except:
                        pass

        except Exception as e:
            logging.debug(f"Could not get pool statistics: {e}")
            pool_status = {"class": "unknown", "error": str(e)}

        # Test database connectivity
        db_healthy = False
        query_time_ms = 0
        try:
            start = time.time()
            with db_manager.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
                conn.commit()
            query_time_ms = (time.time() - start) * 1000
            db_healthy = True
        except Exception as e:
            logging.error(f"Database connectivity test failed: {e}")

        return {
            "status": "healthy" if db_healthy else "unhealthy",
            "active_sessions": status.get("active_sessions", 0),
            "connection_pool": pool_status,
            "query_time_ms": query_time_ms,
            "database_url": (
                db_manager.engine.url.render_as_string(hide_password=True)
                if hasattr(db_manager, "engine")
                else "unknown"
            ),
        }
    except Exception as e:
        logging.exception("Database health check failed")
        return {"status": "error", "error": str(e)}


@health_router.get("/health/processing")
async def health_processing() -> Dict[str, Any]:
    """Show current transaction processing status."""
    try:
        from backend.consensus.monitoring import get_monitor

        monitor = get_monitor()
        status = monitor.get_status()

        processing = status.get("processing", {})
        processing_count = status.get("processing_transactions", 0)

        return {
            "status": "healthy",
            "processing_count": processing_count,
            "processing_transactions": processing,
            "contracts_being_processed": list(processing.keys()) if processing else [],
        }
    except Exception as e:
        logging.exception("Processing health check failed")
        return {"status": "error", "error": str(e)}


@health_router.get("/health/memory")
async def health_memory() -> Dict[str, Any]:
    """Show detailed memory usage statistics."""
    try:
        import psutil
        import gc

        process = psutil.Process()
        memory_info = process.memory_info()

        # Get garbage collection stats
        gc_stats = gc.get_stats()

        return {
            "status": "healthy",
            "memory_usage_mb": memory_info.rss / 1024 / 1024,
            "memory_percent": process.memory_percent(),
            "virtual_memory_mb": (
                memory_info.vms / 1024 / 1024 if hasattr(memory_info, "vms") else 0
            ),
            "gc_objects": len(gc.get_objects()),
            "gc_stats": gc_stats[0] if gc_stats else {},
            "system_memory": {
                "total_mb": psutil.virtual_memory().total / 1024 / 1024,
                "available_mb": psutil.virtual_memory().available / 1024 / 1024,
                "percent_used": psutil.virtual_memory().percent,
            },
        }
    except Exception as e:
        logging.exception("Memory health check failed")
        return {"status": "error", "error": str(e)}


@health_router.get("/health/consensus")
async def health_consensus(
    rpc_router: Optional[FastAPIRPCRouter] = Depends(get_rpc_router_optional),
) -> Dict[str, Any]:
    """Show consensus system status."""
    try:
        if not rpc_router:
            return {"status": "not_initialized", "error": "RPC router not available"}

        # Get consensus from app state if available
        from fastapi import Request
        from backend.consensus.monitoring import get_monitor

        monitor = get_monitor()
        status = monitor.get_status()

        # Check if consensus tasks are healthy
        active_tasks = status.get("active_tasks", 0)
        stale_tasks = status.get("stale_tasks", [])

        consensus_healthy = active_tasks > 0 and len(stale_tasks) == 0

        return {
            "status": "healthy" if consensus_healthy else "degraded",
            "consensus_tasks": {
                "crawl_snapshot": any(
                    "crawl_snapshot" in t.get("name", "")
                    for t in status.get("tasks", {}).values()
                ),
                "pending_transactions": any(
                    "pending_tx" in t.get("name", "")
                    for t in status.get("tasks", {}).values()
                ),
                "appeal_window": any(
                    "appeal_window" in t.get("name", "")
                    for t in status.get("tasks", {}).values()
                ),
            },
            "processing_transactions": status.get("processing_transactions", 0),
            "active_background_tasks": active_tasks,
        }
    except Exception as e:
        logging.exception("Consensus health check failed")
        return {"status": "error", "error": str(e)}
