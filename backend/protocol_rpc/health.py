# backend/protocol_rpc/health.py
import time
import os
import math
import random
from typing import Optional, Union, Dict, Any
import logging
import asyncio
from dataclasses import dataclass, field

import aiohttp
from fastapi import APIRouter, FastAPI, Depends
from fastapi.responses import JSONResponse
from backend.database_handler.session_factory import get_database_manager
from backend.protocol_rpc.fastapi_rpc_router import FastAPIRPCRouter
from backend.protocol_rpc.dependencies import get_rpc_router_optional

logger = logging.getLogger(__name__)

# Create FastAPI router for health endpoints
health_router = APIRouter(tags=["health"])

# =============================================================================
# GenVM Execution Failure Tracking (complements /status probe)
# =============================================================================

_genvm_consecutive_failures: int = 0
_genvm_failure_unhealthy_threshold: int = int(
    os.environ.get("GENVM_FAILURE_UNHEALTHY_THRESHOLD", "3")
)


def record_genvm_execution_failure():
    """Increment consecutive GenVM execution failure counter, but only if the
    GenVM manager process is actually unhealthy.  When the manager /status probe
    is healthy (checked every 10s by background task), the failure is capacity-
    related (semaphore full / timeout) and should NOT count toward liveness —
    restarting the pod won't fix permit starvation.
    """
    global _genvm_consecutive_failures
    if _health_cache.genvm_healthy:
        if _genvm_consecutive_failures > 0:
            logger.info(
                f"GenVM execution failed but manager is healthy — resetting failure count from {_genvm_consecutive_failures} to 0"
            )
            _genvm_consecutive_failures = 0
        else:
            logger.debug("GenVM execution failed but manager is healthy — ignoring")
        return
    _genvm_consecutive_failures += 1
    logger.warning(
        f"GenVM execution failure (manager unhealthy): {_genvm_consecutive_failures}/{_genvm_failure_unhealthy_threshold}"
    )


def record_genvm_execution_success():
    """Reset consecutive GenVM execution failure counter on success."""
    global _genvm_consecutive_failures
    if _genvm_consecutive_failures > 0:
        logger.info(
            f"GenVM execution success - resetting failure count from {_genvm_consecutive_failures} to 0"
        )
        _genvm_consecutive_failures = 0


def get_genvm_execution_failure_count() -> int:
    return _genvm_consecutive_failures


def get_genvm_failure_unhealthy_threshold() -> int:
    return _genvm_failure_unhealthy_threshold


# =============================================================================
# Background Health Check Cache
# =============================================================================


@dataclass
class HealthCache:
    """Cached health check results updated by background task."""

    last_check: float = 0.0
    last_check_duration_ms: float = 0.0
    status: str = "initializing"
    issues: list = field(default_factory=list)
    services: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    # GenVM-specific (triggers 503 on failure)
    genvm_healthy: bool = False
    genvm_error: Optional[str] = None
    # GenVM manager capacity (best-effort; populated from /status when available)
    genvm_max_permits: Optional[int] = None
    genvm_current_permits: Optional[int] = None
    genvm_available_permits: Optional[int] = None
    genvm_active_executions: Optional[int] = None
    # Additional metrics for external API
    total_decisions: int = 0
    total_users: int = 0
    pending_transactions: int = 0
    uptime_percent: float = 100.0
    start_time: float = field(default_factory=time.time)
    # Pending transactions per contract for dashboard
    pending_contracts: list = field(default_factory=list)


_health_cache = HealthCache()
_background_task: Optional[asyncio.Task] = None
_rpc_router_ref: Optional[FastAPIRPCRouter] = None
_usage_metrics_service_ref: Optional[Any] = None
_metrics_send_counter: int = 0

# =============================================================================
# Permit-aware readiness state (in-process)
# =============================================================================

_permits_below_threshold_since: Optional[float] = None
_permits_recovered_since: Optional[float] = None


def _get_readiness_permit_min_available() -> int:
    """
    Minimum required available permits for readiness gating.

    Default is 0 to keep behavior unchanged unless explicitly enabled.
    """
    try:
        return int(os.getenv("READINESS_PERMIT_MIN_AVAILABLE", "0"))
    except Exception:
        return 0


def _get_readiness_permit_sustain_seconds() -> float:
    """
    Require permit exhaustion to be sustained before flipping not-ready.
    """
    try:
        return float(os.getenv("READINESS_PERMIT_SUSTAIN_SECONDS", "20"))
    except Exception:
        return 20.0


def _get_readiness_permit_recover_seconds() -> float:
    """
    Require permits to be back above threshold for a bit before flipping ready again.
    """
    try:
        return float(os.getenv("READINESS_PERMIT_RECOVER_SECONDS", "10"))
    except Exception:
        return 10.0


def _get_readiness_permit_jitter_seconds() -> float:
    """
    Small per-pod jitter reduces synchronized readiness flapping.
    """
    try:
        return float(os.getenv("READINESS_PERMIT_JITTER_SECONDS", "2"))
    except Exception:
        return 2.0


# Per-process stable jitter in [0, jitter_s]
_READINESS_JITTER_S: float = (
    random.Random(os.getpid()).random() * _get_readiness_permit_jitter_seconds()
)


def _evaluate_permit_readiness(
    now: float, available_permits: Optional[int]
) -> tuple[bool, Optional[str]]:
    """
    Decide whether we should be ready based on available permits.

    This is intentionally conservative: by default it's disabled (min=0), and it
    uses sustain/recover windows plus jitter to avoid thrashing.
    """
    global _permits_below_threshold_since, _permits_recovered_since

    min_avail = _get_readiness_permit_min_available()
    if min_avail <= 0:
        _permits_below_threshold_since = None
        _permits_recovered_since = None
        return True, None

    if available_permits is None:
        # If we can't observe permits, don't gate readiness (safer than outage).
        _permits_below_threshold_since = None
        _permits_recovered_since = None
        return True, "permits_unknown"

    below = available_permits < min_avail
    sustain_s = _get_readiness_permit_sustain_seconds() + _READINESS_JITTER_S
    recover_s = _get_readiness_permit_recover_seconds() + _READINESS_JITTER_S

    if below:
        _permits_recovered_since = None
        if _permits_below_threshold_since is None:
            _permits_below_threshold_since = now
            return True, "permits_low_transient"
        if (now - _permits_below_threshold_since) >= sustain_s:
            return False, "permits_low_sustained"
        return True, "permits_low_transient"

    # Recovered (>= min_avail)
    _permits_below_threshold_since = None
    if _permits_recovered_since is None:
        _permits_recovered_since = now
        return True, "permits_recovering"
    if (now - _permits_recovered_since) >= recover_s:
        return True, None
    return True, "permits_recovering"


# Send system health metrics every 6 health checks (6 × 10s = 60s = 1 minute)
METRICS_SEND_INTERVAL = 6


def get_health_check_interval() -> float:
    """Get health check interval from env (default 10s)."""
    return float(os.getenv("HEALTH_CHECK_INTERVAL_SECONDS", "10"))


async def _run_health_checks() -> None:
    """Run all expensive health checks and update cache."""
    global _health_cache

    start = time.time()
    overall_status = "healthy"
    issues = []
    services = {}

    try:
        # 1. Database health
        db_health = await _check_database_health()
        db_status = db_health.get("status", "unknown")
        services["database"] = {
            "status": db_status,
            "pool_size": db_health.get("connection_pool", {}).get("size"),
            "checked_out": db_health.get("connection_pool", {}).get("checked_out"),
        }
        if db_status in ["unhealthy", "error"]:
            overall_status = "unhealthy"
            issues.append("database_issue")
        elif db_status == "degraded":
            if overall_status == "healthy":
                overall_status = "degraded"

        # 2. Consensus health
        consensus_health = await _check_consensus_health()
        consensus_status = consensus_health.get("status", "unknown")
        services["consensus"] = {
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

        # 3. Memory health
        memory_health = await _check_memory_health()
        memory_status = memory_health.get("status", "unknown")
        services["memory"] = {
            "status": memory_status,
            "usage_mb": memory_health.get("memory_usage_mb"),
            "percent": memory_health.get("memory_percent"),
            "cpu_percent": memory_health.get("cpu_percent", 0),
        }
        if memory_status in ["unhealthy", "degraded"]:
            if overall_status == "healthy":
                overall_status = "degraded"
            issues.append("memory_issue")

        # 4. Redis health
        redis_status = await _check_redis_health()
        services["redis"] = redis_status
        if redis_status == "unhealthy":
            if overall_status == "healthy":
                overall_status = "degraded"
            issues.append("redis_unreachable")

        # 5. GenVM manager health (+ capacity details when available)
        genvm_ok, genvm_error, genvm_status = await _check_genvm_health()
        services["genvm"] = {"status": "healthy" if genvm_ok else "unhealthy"}
        _health_cache.genvm_healthy = genvm_ok
        _health_cache.genvm_error = genvm_error

        # Best-effort parse of permit info (do not fail overall health on missing keys).
        try:
            max_permits = genvm_status.get("max_permits")
            current_permits = genvm_status.get("current_permits")
            active_exec = genvm_status.get("active_executions")

            max_permits_i = int(max_permits) if max_permits is not None else None
            current_permits_i = (
                int(current_permits) if current_permits is not None else None
            )
            available_i = (
                max_permits_i - current_permits_i
                if max_permits_i is not None and current_permits_i is not None
                else None
            )

            _health_cache.genvm_max_permits = max_permits_i
            _health_cache.genvm_current_permits = current_permits_i
            _health_cache.genvm_available_permits = available_i
            _health_cache.genvm_active_executions = (
                int(active_exec) if active_exec is not None else None
            )

            services["genvm"].update(
                {
                    "max_permits": max_permits_i,
                    "current_permits": current_permits_i,
                    "available_permits": available_i,
                    "active_executions": _health_cache.genvm_active_executions,
                }
            )
        except Exception as e:
            logger.debug(f"Failed to parse GenVM permit info from /status: {e}")
            _health_cache.genvm_max_permits = None
            _health_cache.genvm_current_permits = None
            _health_cache.genvm_available_permits = None
            _health_cache.genvm_active_executions = None

        # 6. Aggregate counts for metrics
        decisions_count, users_count, pending_count = await _get_aggregate_counts()
        _health_cache.total_decisions = decisions_count
        _health_cache.total_users = users_count
        _health_cache.pending_transactions = pending_count
        _health_cache.uptime_percent = 100.0  # 100% while running

        # 7. Get pending contracts breakdown for dashboard
        _health_cache.pending_contracts = await _get_pending_contracts()

        # Update cache
        _health_cache.last_check = time.time()
        _health_cache.last_check_duration_ms = round((time.time() - start) * 1000, 2)
        _health_cache.status = overall_status
        _health_cache.issues = issues
        _health_cache.services = services
        _health_cache.error = None

    except Exception as e:
        logger.exception("Background health check failed")
        _health_cache.last_check = time.time()
        _health_cache.last_check_duration_ms = round((time.time() - start) * 1000, 2)
        _health_cache.status = "error"
        _health_cache.error = str(e)


async def _background_health_loop() -> None:
    """Background loop that periodically runs health checks."""
    global _metrics_send_counter

    interval = get_health_check_interval()
    logger.info(f"Starting background health checker (interval={interval}s)")

    while True:
        try:
            await _run_health_checks()

            # Send system health metrics every 1 minute (every 6th iteration)
            _metrics_send_counter += 1
            if _metrics_send_counter >= METRICS_SEND_INTERVAL:
                _metrics_send_counter = 0
                if _usage_metrics_service_ref and _usage_metrics_service_ref.enabled:
                    try:
                        await _usage_metrics_service_ref.send_system_health_metrics(
                            _health_cache
                        )
                    except Exception as e:
                        logger.warning(f"Failed to send system health metrics: {e}")

        except asyncio.CancelledError:
            logger.info("Background health checker cancelled")
            raise
        except Exception as e:
            logger.exception(f"Background health check error: {e}")

        await asyncio.sleep(interval)


def start_background_health_checker(
    rpc_router: Optional[FastAPIRPCRouter] = None,
    usage_metrics_service: Optional[Any] = None,
) -> None:
    """Start the background health checker task. Call from app startup."""
    global _background_task, _rpc_router_ref, _usage_metrics_service_ref

    _rpc_router_ref = rpc_router
    _usage_metrics_service_ref = usage_metrics_service

    if _background_task is not None:
        logger.warning("Background health checker already running")
        return

    loop = asyncio.get_event_loop()
    _background_task = loop.create_task(_background_health_loop())
    logger.info("Background health checker started")


def stop_background_health_checker() -> None:
    """Stop the background health checker task. Call from app shutdown."""
    global _background_task

    if _background_task is not None:
        _background_task.cancel()
        _background_task = None
        logger.info("Background health checker stopped")


# =============================================================================
# Individual Health Check Functions (used by background task)
# =============================================================================


async def _check_database_health() -> Dict[str, Any]:
    """Check database connectivity and pool stats."""
    try:
        from backend.consensus.monitoring import get_monitor
        from sqlalchemy import text

        monitor = get_monitor()
        status = monitor.get_status()

        db_manager = get_database_manager()
        pool_status = {}

        try:
            pool = db_manager.engine.pool
            pool_status = {"class": pool.__class__.__name__}

            if hasattr(pool, "size"):
                try:
                    pool_status["size"] = pool.size()
                except Exception:
                    pass

            if hasattr(pool, "checkedout"):
                try:
                    pool_status["checked_out"] = pool.checkedout()
                except Exception:
                    pass

            if hasattr(pool, "overflow"):
                try:
                    pool_status["overflow"] = pool.overflow()
                except Exception:
                    pass

        except Exception as e:
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
            logger.error(f"Database connectivity test failed: {e}")

        return {
            "status": "healthy" if db_healthy else "unhealthy",
            "active_sessions": status.get("active_sessions", 0),
            "connection_pool": pool_status,
            "query_time_ms": query_time_ms,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


async def _check_consensus_health() -> Dict[str, Any]:
    """Check consensus system status."""
    from datetime import datetime, timedelta, timezone
    from backend.database_handler.models import Transactions
    from backend.database_handler.session_factory import get_database_manager

    try:
        if not _rpc_router_ref:
            return {"status": "not_initialized", "error": "RPC router not available"}

        db_manager = get_database_manager()

        # Get active worker IDs
        with db_manager.engine.connect() as worker_conn:
            now = datetime.now(timezone.utc)
            recent_threshold = now - timedelta(hours=1)

            from sqlalchemy import select, distinct, and_

            worker_query = select(distinct(Transactions.worker_id)).where(
                and_(
                    Transactions.worker_id.isnot(None),
                    Transactions.created_at > recent_threshold,
                )
            )
            worker_result = worker_conn.execute(worker_query)
            active_workers = {row[0] for row in worker_result if row[0]}

        # Get transaction statistics
        with db_manager.engine.connect() as conn:
            from sqlalchemy import text

            query = text(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status IN ('ACTIVATED', 'PROPOSING', 'COMMITTING', 'REVEALING', 'ACCEPTED', 'UNDETERMINED')) as processing_count,
                    COUNT(*) FILTER (WHERE worker_id IS NOT NULL AND status IN ('PENDING', 'ACTIVATED', 'PROPOSING', 'COMMITTING', 'REVEALING', 'ACCEPTED', 'UNDETERMINED')) as blocked_count
                FROM transactions
                WHERE to_address IS NOT NULL
            """
            )
            result = conn.execute(query)
            row = result.fetchone()

            total_processing = row.processing_count if row else 0
            total_blocked = row.blocked_count if row else 0

            # Simplified orphan detection
            total_orphaned = 0
            if total_blocked > 0 and len(active_workers) == 0:
                total_orphaned = total_blocked

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
            }

    except Exception as e:
        logger.exception("Consensus health check failed")
        return {"status": "error", "error": str(e)}


async def _check_memory_health() -> Dict[str, Any]:
    """Check memory usage and CPU usage."""
    try:
        import psutil

        process = psutil.Process()
        memory_info = process.memory_info()

        return {
            "status": "healthy",
            "memory_usage_mb": memory_info.rss / 1024 / 1024,
            "memory_percent": process.memory_percent(),
            "cpu_percent": process.cpu_percent(interval=0.1),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


async def _get_aggregate_counts() -> tuple[int, int, int]:
    """Query total decisions, unique users, and pending transactions from database."""
    from sqlalchemy import text

    try:
        db_manager = get_database_manager()
        with db_manager.engine.connect() as conn:
            query = text(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status IN ('FINALIZED', 'ACCEPTED')) as decisions,
                    COUNT(DISTINCT from_address) as users,
                    COUNT(*) FILTER (WHERE status = 'PENDING') as pending
                FROM transactions
            """
            )
            result = conn.execute(query)
            row = result.fetchone()
            return (
                (row.decisions or 0, row.users or 0, row.pending or 0)
                if row
                else (0, 0, 0)
            )
    except Exception as e:
        logger.warning(f"Failed to get aggregate counts: {e}")
        return (0, 0, 0)


async def _get_pending_contracts() -> list[dict]:
    """Query pending transactions grouped by contract address."""
    from sqlalchemy import text

    try:
        db_manager = get_database_manager()
        with db_manager.engine.connect() as conn:
            query = text(
                """
                SELECT
                    to_address as contract_address,
                    COUNT(*) as pending_count
                FROM transactions
                WHERE status = 'PENDING'
                  AND to_address IS NOT NULL
                GROUP BY to_address
                ORDER BY pending_count DESC
                LIMIT 20
            """
            )
            result = conn.execute(query)
            return [
                {
                    "contractAddress": row.contract_address,
                    "pendingCount": row.pending_count,
                }
                for row in result
            ]
    except Exception as e:
        logger.warning(f"Failed to get pending contracts: {e}")
        return []


async def _check_redis_health() -> str:
    """Check Redis connectivity."""
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        return "not_configured"

    try:
        import redis

        redis_client = redis.from_url(redis_url)
        try:
            redis_client.ping()
            return "healthy"
        finally:
            redis_client.close()
    except Exception:
        return "unhealthy"


async def _check_genvm_health() -> tuple[bool, Optional[str], Dict[str, Any]]:
    """Check GenVM manager health and (best-effort) parse permit status."""
    status_url = os.getenv("GENVM_MANAGER_STATUS_URL", "http://127.0.0.1:3999/status")
    timeout_s = float(os.getenv("GENVM_MANAGER_HEALTH_TIMEOUT_SECONDS", "2"))

    try:
        async with aiohttp.request(
            "GET",
            status_url,
            timeout=aiohttp.ClientTimeout(total=timeout_s),
        ) as resp:
            if resp.status != 200:
                return False, f"genvm_manager_status_http_{resp.status}", {}

            # /status is expected to be JSON and may contain permits + executions.
            # Treat JSON parse failures as "healthy but unknown capacity" to avoid
            # false negatives taking out production traffic.
            try:
                payload = await resp.json()
                if not isinstance(payload, dict):
                    return True, None, {}
                return True, None, payload
            except Exception:
                return True, None, {}
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        return False, f"genvm_manager_status_error: {exc}", {}
    except Exception as e:
        # Don't fail on unexpected errors
        logger.warning(f"GenVM health probe failed unexpectedly: {e}")
        return True, None, {}


# =============================================================================
# HTTP Endpoints
# =============================================================================


@health_router.get("/ping")
async def ping():
    """Ultra-lightweight health check for load balancers. No DB, no external calls."""
    return {"status": "ok"}


@health_router.get("/health", response_model=None)
async def health_check() -> Union[dict, JSONResponse]:
    """
    Returns cached health check results from background task.
    Fast response (~1ms) while still providing meaningful health status.
    """
    # Check if GenVM is unhealthy - return 503 to trigger restart
    if not _health_cache.genvm_healthy:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "error": "genvm_manager_unresponsive",
                "timestamp": time.time(),
            },
        )

    # Check if GenVM execution is failing consecutively
    if _genvm_consecutive_failures >= _genvm_failure_unhealthy_threshold:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "error": "genvm_consecutive_failures",
                "count": _genvm_consecutive_failures,
                "threshold": _genvm_failure_unhealthy_threshold,
                "timestamp": time.time(),
            },
        )

    cache_age_ms = (
        (time.time() - _health_cache.last_check) * 1000
        if _health_cache.last_check > 0
        else None
    )

    return {
        "status": _health_cache.status,
        "timestamp": time.time(),
        "cache_age_ms": round(cache_age_ms, 2) if cache_age_ms else None,
        "last_check_duration_ms": _health_cache.last_check_duration_ms,
        "issues": _health_cache.issues if _health_cache.issues else None,
        "services": _health_cache.services,
        # Note: internal errors logged server-side, not exposed to clients
        "meta": {
            "pid": os.getpid(),
            "workers": os.getenv("WEB_CONCURRENCY", "1"),
            "check_interval_s": get_health_check_interval(),
        },
    }


@health_router.get("/ready")
async def readiness_check(
    rpc_router: FastAPIRPCRouter | None = Depends(get_rpc_router_optional),
):
    """
    Readiness check for Kubernetes.

    Returns HTTP 200 when ready, HTTP 503 when not-ready so kube-proxy/Ingress
    stops routing new traffic to this pod.
    """
    readiness_func = create_readiness_check_with_state(rpc_router)
    return await readiness_func()


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
        genvm_exec_healthy = (
            _genvm_consecutive_failures < _genvm_failure_unhealthy_threshold
        )
        permits_ready, permits_reason = _evaluate_permit_readiness(
            time.time(), _health_cache.genvm_available_permits
        )

        # If the GenVM manager is unhealthy, we are not ready (and /health already 503s).
        # Permit gating is optional and defaults to disabled (min_available=0).
        is_ready = (
            rpc_router_ready
            and genvm_exec_healthy
            and _health_cache.genvm_healthy
            and permits_ready
        )

        result = {
            "status": "ready" if is_ready else "not_ready",
            "service": "genlayer-rpc",
            "rpc_router_initialized": rpc_router_ready,
            "genvm_manager_healthy": _health_cache.genvm_healthy,
            "genvm_permits": {
                "max_permits": _health_cache.genvm_max_permits,
                "current_permits": _health_cache.genvm_current_permits,
                "available_permits": _health_cache.genvm_available_permits,
                "active_executions": _health_cache.genvm_active_executions,
                "min_available_for_ready": _get_readiness_permit_min_available(),
                "reason": permits_reason,
            },
        }
        if not genvm_exec_healthy:
            result["genvm_execution_failures"] = _genvm_consecutive_failures
        if not _health_cache.genvm_healthy:
            result["genvm_error"] = _health_cache.genvm_error
        if not is_ready:
            return JSONResponse(status_code=503, content=result)
        return result

    return readiness_check_with_state


# =============================================================================
# Detailed Health Endpoints (for debugging - run checks synchronously)
# =============================================================================


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
                try:
                    pool_status["status"] = pool.status()
                except Exception:
                    pass

            if hasattr(pool, "size"):
                try:
                    pool_status["size"] = pool.size()
                except Exception:
                    pass

            if hasattr(pool, "checkedout"):
                try:
                    pool_status["checked_out"] = pool.checkedout()
                except Exception:
                    pass

            if hasattr(pool, "overflow"):
                try:
                    pool_status["overflow"] = pool.overflow()
                except Exception:
                    pass

            if "checked_out" in pool_status and "overflow" in pool_status:
                pool_status["total"] = (
                    pool_status["checked_out"] + pool_status["overflow"]
                )

            if pool.__class__.__name__ == "QueuePool":
                if hasattr(pool, "_pool"):
                    try:
                        pool_status["available"] = (
                            pool._pool.qsize() if hasattr(pool._pool, "qsize") else None
                        )
                    except Exception:
                        pass

        except Exception as e:
            logger.debug(f"Could not get pool statistics: {e}")
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
            logger.error(f"Database connectivity test failed: {e}")

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
        logger.exception("Database health check failed")
        return {"status": "error", "error": str(e)}


@health_router.get("/health/memory")
async def health_memory() -> Dict[str, Any]:
    """Show detailed memory usage statistics."""
    try:
        import psutil
        import gc

        process = psutil.Process()
        memory_info = process.memory_info()

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
        logger.exception("Memory health check failed")
        return {"status": "error", "error": str(e)}


@health_router.get("/health/cpu")
async def health_cpu() -> Dict[str, Any]:
    """Show detailed CPU usage statistics."""
    try:
        import psutil

        process = psutil.Process()
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
        logger.exception("CPU health check failed")
        return {"status": "error", "error": str(e)}


@health_router.get("/health/consensus")
async def health_consensus(
    rpc_router: Optional[FastAPIRPCRouter] = Depends(get_rpc_router_optional),
) -> Dict[str, Any]:
    """Show consensus system status with detailed contract-level transaction metrics."""
    from datetime import datetime, timedelta, timezone
    from backend.database_handler.models import Transactions
    from backend.database_handler.session_factory import get_database_manager

    try:
        if not rpc_router:
            return {"status": "not_initialized", "error": "RPC router not available"}

        # Get active worker IDs from recent transactions
        db_manager = get_database_manager()
        with db_manager.engine.connect() as worker_conn:
            now = datetime.now(timezone.utc)
            recent_threshold = now - timedelta(hours=1)

            from sqlalchemy import select, distinct, and_

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

            from sqlalchemy import text

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
        logger.exception("Consensus health check failed")
        return {"status": "error", "error": str(e)}


@health_router.get("/metrics")
async def metrics():
    """Return worker metrics for autoscaling in Prometheus format."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select, distinct, and_
    from fastapi.responses import Response
    from prometheus_client import (
        CollectorRegistry,
        Gauge,
        generate_latest,
        CONTENT_TYPE_LATEST,
    )
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
        needed_workers_count = max(
            1, active_workers_count + math.ceil(active_workers_count * 0.1)
        )

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

    except Exception:
        logging.exception("Metrics endpoint failed")
        return Response(
            content=b"# HELP genlayer_metrics_error Indicates metrics collection failed\n# TYPE genlayer_metrics_error gauge\ngenlayer_metrics_error 1\n",
            status_code=500,
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )
