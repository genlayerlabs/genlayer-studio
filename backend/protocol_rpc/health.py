# backend/protocol_rpc/health.py
import time
import os
import math
from typing import Optional, Union, Dict, Any
import logging
import asyncio

import aiohttp
from fastapi import APIRouter, FastAPI, Depends
from fastapi.responses import JSONResponse
from backend.database_handler.session_factory import get_database_manager
from backend.protocol_rpc.fastapi_rpc_router import FastAPIRPCRouter
from backend.protocol_rpc.dependencies import get_rpc_router_optional

# Create FastAPI router for health endpoints
health_router = APIRouter(tags=["health"])

# GenVM manager health probe cache (avoid probing on every /health hit)
_genvm_health_last_check: float = 0.0
_genvm_health_last_ok: bool = True
_genvm_health_last_error: str | None = None


@health_router.get("/health")
async def health_check(
    rpc_router: Optional[FastAPIRPCRouter] = Depends(get_rpc_router_optional),
) -> dict:
    """Unified health check endpoint summarizing all system metrics by calling other endpoints."""
    start = time.time()
    overall_status = "healthy"
    issues = []

    # Call existing health endpoints to reuse logic
    try:
        # 1. Database health
        db_health = await health_database()
        db_status = db_health.get("status", "unknown")
        if db_status in ["unhealthy", "error"]:
            overall_status = "unhealthy"
            issues.append("database_issue")
        elif db_status == "degraded":
            if overall_status == "healthy":
                overall_status = "degraded"

        # 3. Consensus health
        consensus_health = await health_consensus(rpc_router)
        consensus_status = consensus_health.get("status", "unknown")
        consensus_summary = {
            "processing_transactions": consensus_health.get(
                "total_processing_transactions", 0
            ),
            "orphaned_transactions": consensus_health.get(
                "total_orphaned_transactions", 0
            ),
            "active_workers": consensus_health.get("active_workers", 0),
            "status": consensus_status,
        }
        if consensus_status in ["unhealthy", "error"]:
            if overall_status == "healthy":
                overall_status = "degraded"
            issues.append("consensus_issue")
        elif consensus_health.get("total_orphaned_transactions", 0) > 0:
            if overall_status == "healthy":
                overall_status = "degraded"
            issues.append("orphaned_transactions")

        # 4. Memory health
        memory_health = await health_memory()
        memory_status = memory_health.get("status", "unknown")
        if memory_status in ["unhealthy", "degraded"]:
            if overall_status == "healthy":
                overall_status = "degraded"
            issues.append("memory_issue")

        # 5. Check Redis (lightweight check not in other endpoints)
        redis_status = "not_configured"
        if os.getenv("REDIS_URL"):
            try:
                import redis

                redis_client = redis.from_url(os.getenv("REDIS_URL"))
                redis_client.ping()
                redis_status = "healthy"
            except Exception:
                redis_status = "unhealthy"
                if overall_status == "healthy":
                    overall_status = "degraded"
                issues.append("redis_unreachable")

        # 6. Check GenVM manager health (with caching to avoid probing on every hit)
        # Returns 503 to trigger container restart when GenVM is unresponsive
        global _genvm_health_last_check, _genvm_health_last_ok, _genvm_health_last_error

        try:
            now = time.time()
            probe_interval_s = float(
                os.getenv("GENVM_MANAGER_HEALTH_PROBE_INTERVAL_SECONDS", "5")
            )
            if now - _genvm_health_last_check >= probe_interval_s:
                _genvm_health_last_check = now
                status_url = os.getenv(
                    "GENVM_MANAGER_STATUS_URL", "http://127.0.0.1:3999/status"
                )
                timeout_s = float(
                    os.getenv("GENVM_MANAGER_HEALTH_TIMEOUT_SECONDS", "2")
                )
                try:
                    async with aiohttp.request(
                        "GET",
                        status_url,
                        timeout=aiohttp.ClientTimeout(total=timeout_s),
                    ) as resp:
                        if resp.status != 200:
                            _genvm_health_last_ok = False
                            _genvm_health_last_error = (
                                f"genvm_manager_status_http_{resp.status}"
                            )
                        else:
                            _genvm_health_last_ok = True
                            _genvm_health_last_error = None
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    _genvm_health_last_ok = False
                    _genvm_health_last_error = f"genvm_manager_status_error: {exc}"

            if not _genvm_health_last_ok:
                # Return 503 to trigger Kubernetes/ArgoCD container restart
                return JSONResponse(
                    status_code=503,
                    content={
                        "status": "unhealthy",
                        "error": "genvm_manager_unresponsive",
                        "detail": _genvm_health_last_error,
                        "timestamp": time.time(),
                    },
                )
        except Exception as e:
            # Don't fail health checks due to probe implementation issues
            logging.warning(f"GenVM health probe failed unexpectedly: {e}")

        return {
            "status": overall_status,
            "timestamp": time.time(),
            "response_time_ms": round((time.time() - start) * 1000, 2),
            "issues": issues if issues else None,
            "services": {
                "database": {
                    "status": db_status,
                    "pool_size": db_health.get("connection_pool", {}).get("size"),
                    "checked_out": db_health.get("connection_pool", {}).get(
                        "checked_out"
                    ),
                },
                "redis": redis_status,
                "consensus": consensus_summary,
                "memory": {
                    "status": memory_status,
                    "usage_mb": memory_health.get("memory_usage_mb"),
                    "percent": memory_health.get("memory_percent"),
                },
                "genvm": {
                    "status": "healthy",
                },
            },
            "meta": {
                "pid": os.getpid(),
                "workers": os.getenv("WEB_CONCURRENCY", "1"),
            },
        }

    except Exception as e:
        logging.exception("Health check failed")
        return {
            "status": "error",
            "timestamp": time.time(),
            "response_time_ms": round((time.time() - start) * 1000, 2),
            "error": str(e),
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


@health_router.get("/health/cpu")
async def health_cpu() -> Dict[str, Any]:
    """Show detailed CPU usage statistics."""
    try:
        import psutil

        process = psutil.Process()

        # Get CPU times
        cpu_times = process.cpu_times()

        return {
            "status": "healthy",
            "cpu_percent": process.cpu_percent(interval=0.1),
            "cpu_times": {
                "user": cpu_times.user,
                "system": cpu_times.system,
            },
            "num_threads": process.num_threads(),
            "system_cpu": {
                "percent": psutil.cpu_percent(interval=0.1),
                "per_cpu_percent": psutil.cpu_percent(interval=0.1, percpu=True),
                "cpu_count": psutil.cpu_count(),
                "cpu_count_logical": psutil.cpu_count(logical=True),
                "load_average": (
                    psutil.getloadavg() if hasattr(psutil, "getloadavg") else None
                ),
            },
        }
    except Exception as e:
        logging.exception("CPU health check failed")
        return {"status": "error", "error": str(e)}


@health_router.get("/health/consensus")
async def health_consensus(
    rpc_router: Optional[FastAPIRPCRouter] = Depends(get_rpc_router_optional),
) -> Dict[str, Any]:
    """Show consensus system status with detailed contract-level transaction metrics."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import func, and_, or_
    from backend.database_handler.models import Transactions, TransactionStatus
    from backend.database_handler.session_factory import get_database_manager

    try:
        if not rpc_router:
            return {"status": "not_initialized", "error": "RPC router not available"}

        # Get active worker IDs from recent transactions
        db_manager = get_database_manager()
        with db_manager.engine.connect() as worker_conn:
            # Query distinct worker_ids from transactions in the last hour
            # Workers that have processed transactions recently are considered active
            now = datetime.now(timezone.utc)
            recent_threshold = now - timedelta(hours=1)

            from sqlalchemy import select, distinct

            worker_query = select(distinct(Transactions.worker_id)).where(
                and_(
                    Transactions.worker_id.isnot(None),
                    Transactions.created_at > recent_threshold,
                )
            )

            worker_result = worker_conn.execute(worker_query)
            active_workers = {row[0] for row in worker_result if row[0]}

        # Query transaction statistics by contract
        db_manager = get_database_manager()
        with db_manager.engine.connect() as conn:
            now = datetime.now(timezone.utc)

            # Get contract-level statistics
            from sqlalchemy import select, text

            query = text(
                """
                SELECT
                    to_address as contract_address,
                    COUNT(*) FILTER (WHERE status IN ('ACTIVATED', 'PROPOSING', 'COMMITTING', 'REVEALING', 'ACCEPTED', 'UNDETERMINED')) as processing_count,
                    COUNT(*) FILTER (WHERE status = 'PENDING') as pending_count,
                    COUNT(*) FILTER (WHERE created_at > :one_hour_ago) as created_last_1h,
                    COUNT(*) FILTER (WHERE created_at > :three_hours_ago) as created_last_3h,
                    COUNT(*) FILTER (WHERE created_at > :six_hours_ago) as created_last_6h,
                    COUNT(*) FILTER (WHERE created_at > :twelve_hours_ago) as created_last_12h,
                    COUNT(*) FILTER (WHERE created_at > :one_day_ago) as created_last_1d,
                    MIN(blocked_at) as oldest_blocked_at,
                    MIN(created_at) FILTER (WHERE status IN ('PENDING', 'ACTIVATED', 'PROPOSING', 'COMMITTING', 'REVEALING', 'ACCEPTED', 'UNDETERMINED')) as oldest_processing_created_at,
                    COUNT(*) FILTER (WHERE worker_id IS NOT NULL AND status IN ('PENDING', 'ACTIVATED', 'PROPOSING', 'COMMITTING', 'REVEALING', 'ACCEPTED', 'UNDETERMINED')) as blocked_count,
                    json_agg(DISTINCT jsonb_build_object('worker_id', worker_id, 'hash', hash))
                        FILTER (WHERE worker_id IS NOT NULL AND status IN ('PENDING', 'ACTIVATED', 'PROPOSING', 'COMMITTING', 'REVEALING', 'ACCEPTED', 'UNDETERMINED')) as worker_transactions
                FROM transactions
                WHERE to_address IS NOT NULL
                GROUP BY to_address
                HAVING COUNT(*) FILTER (WHERE status IN ('PENDING', 'ACTIVATED', 'PROPOSING', 'COMMITTING', 'REVEALING', 'ACCEPTED', 'UNDETERMINED')) > 0
                ORDER BY processing_count DESC
            """
            )

            result = conn.execute(
                query,
                {
                    "one_hour_ago": now - timedelta(hours=1),
                    "three_hours_ago": now - timedelta(hours=3),
                    "six_hours_ago": now - timedelta(hours=6),
                    "twelve_hours_ago": now - timedelta(hours=12),
                    "one_day_ago": now - timedelta(days=1),
                },
            )

            contracts = []
            total_orphaned = 0

            for row in result:
                contract_data = {
                    "contract_address": row.contract_address,
                    "processing_count": row.processing_count,
                    "pending_count": row.pending_count,
                    "created_last_1h": row.created_last_1h,
                    "created_last_3h": row.created_last_3h,
                    "created_last_6h": row.created_last_6h,
                    "created_last_12h": row.created_last_12h,
                    "created_last_1d": row.created_last_1d,
                }

                # Calculate elapsed time for oldest blocked transaction
                if row.oldest_blocked_at:
                    elapsed = now - row.oldest_blocked_at
                    minutes = int(elapsed.total_seconds() / 60)
                    if minutes < 60:
                        contract_data["oldest_transaction_elapsed"] = f"{minutes}m"
                    else:
                        hours = minutes // 60
                        contract_data["oldest_transaction_elapsed"] = f"{hours}h"
                else:
                    contract_data["oldest_transaction_elapsed"] = None

                # Add oldest processing transaction created_at timestamp
                if row.oldest_processing_created_at:
                    contract_data["oldest_processing_created_at"] = (
                        row.oldest_processing_created_at.isoformat()
                    )
                    elapsed = now - row.oldest_processing_created_at
                    minutes = int(elapsed.total_seconds() / 60)
                    if minutes < 60:
                        contract_data["oldest_processing_elapsed"] = f"{minutes}m"
                    else:
                        hours = minutes // 60
                        contract_data["oldest_processing_elapsed"] = f"{hours}h"
                else:
                    contract_data["oldest_processing_created_at"] = None
                    contract_data["oldest_processing_elapsed"] = None

                # Detect orphaned transactions
                orphaned_tx_hashes = []
                if row.worker_transactions:
                    for tx_info in row.worker_transactions:
                        if tx_info and tx_info.get("worker_id") not in active_workers:
                            orphaned_tx_hashes.append(tx_info.get("hash"))

                contract_data["orphaned_transactions"] = len(orphaned_tx_hashes)
                if orphaned_tx_hashes:
                    contract_data["orphaned_transaction_hashes"] = orphaned_tx_hashes
                total_orphaned += contract_data["orphaned_transactions"]

                contracts.append(contract_data)

            # Overall status
            total_processing = sum(c["processing_count"] for c in contracts)
            status = (
                "healthy"
                if total_processing < 100 and total_orphaned == 0
                else "degraded"
            )

            return {
                "status": status,
                "total_processing_transactions": total_processing,
                "total_orphaned_transactions": total_orphaned,
                "active_workers": len(active_workers),
                "contracts": contracts,
            }

    except Exception as e:
        logging.exception("Consensus health check failed")
        return {"status": "error", "error": str(e)}


@health_router.get("/metrics")
async def metrics():
    """Return worker metrics for autoscaling in Prometheus format."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select, distinct, and_
    from fastapi.responses import Response
    from prometheus_client import CollectorRegistry, Gauge, generate_latest, CONTENT_TYPE_LATEST
    from backend.database_handler.models import Transactions
    from backend.database_handler.session_factory import get_database_manager

    try:
        db_manager = get_database_manager()
        with db_manager.engine.connect() as conn:
            now = datetime.now(timezone.utc)
            recent_threshold = now - timedelta(hours=1)

            worker_query = select(distinct(Transactions.worker_id)).where(
                and_(
                    Transactions.worker_id.isnot(None),
                    Transactions.created_at > recent_threshold,
                )
            )

            worker_result = conn.execute(worker_query)
            active_workers_count = len({row[0] for row in worker_result if row[0]})

        # needed_workers = active_workers + ceil(active_workers * 0.1), minimum 1
        needed_workers_count = max(1, active_workers_count + math.ceil(active_workers_count * 0.1))

        # Create a fresh registry for each request to avoid duplicate metrics
        registry = CollectorRegistry()
        active_workers = Gauge(
            "genlayer_active_workers",
            "Number of active workers processing transactions in the last hour",
            registry=registry,
        )
        needed_workers = Gauge(
            "genlayer_needed_workers",
            "Number of workers needed for autoscaling (active + 10% buffer, min 1)",
            registry=registry,
        )
        active_workers.set(active_workers_count)
        needed_workers.set(needed_workers_count)

        return Response(
            content=generate_latest(registry),
            media_type=CONTENT_TYPE_LATEST,
        )

    except Exception as e:
        logging.exception("Metrics endpoint failed")
        return Response(
            content=b"# Metrics endpoint error\n",
            status_code=500,
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )
