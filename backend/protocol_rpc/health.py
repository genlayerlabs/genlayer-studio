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


def _update_genvm_health_cache(
    services: Dict[str, Any],
    genvm_ok: bool,
    genvm_error: Optional[str],
    genvm_status: Dict[str, Any],
) -> None:
    services["genvm"] = {"status": "healthy" if genvm_ok else "unhealthy"}
    _health_cache.genvm_healthy = genvm_ok
    _health_cache.genvm_error = genvm_error

    # Best-effort parse of permit info from /status.
    # The manager returns: {"permits": {"current": N, "max": M}, "executions": {...}}
    # where permits.current = available permits (semaphore value).
    try:
        permits_obj = genvm_status.get("permits") or {}
        executions_obj = genvm_status.get("executions")

        max_permits_i = int(permits_obj["max"]) if "max" in permits_obj else None
        # permits.current IS the available count (semaphore value)
        available_i = int(permits_obj["current"]) if "current" in permits_obj else None
        active_i = len(executions_obj) if isinstance(executions_obj, dict) else None

        _health_cache.genvm_max_permits = max_permits_i
        _health_cache.genvm_available_permits = available_i
        _health_cache.genvm_current_permits = active_i  # in-use count
        _health_cache.genvm_active_executions = active_i

        services["genvm"].update(
            {
                "max_permits": max_permits_i,
                "available_permits": available_i,
                "current_permits": active_i,
                "active_executions": active_i,
            }
        )
    except Exception as e:
        logger.debug(f"Failed to parse GenVM permit info from /status: {e}")
        _health_cache.genvm_max_permits = None
        _health_cache.genvm_current_permits = None
        _health_cache.genvm_available_permits = None
        _health_cache.genvm_active_executions = None


async def _run_health_checks() -> None:
    """Run all expensive health checks and update cache."""
    global _health_cache

    start = time.time()
    overall_status = "healthy"
    issues = []
    services = {}

    try:
        # 1. GenVM manager health. Keep readiness-critical state independent
        # from slower dashboard/alert queries so new pods can become ready once
        # their local GenVM manager is responsive.
        genvm_ok, genvm_error, genvm_status = await _check_genvm_health()
        _update_genvm_health_cache(services, genvm_ok, genvm_error, genvm_status)

        # 2. Database health
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

        # 3. Consensus health
        consensus_health = await _check_consensus_health()
        consensus_status = consensus_health.get("status", "unknown")
        services["consensus"] = {
            "processing_transactions": consensus_health.get(
                "total_processing_transactions", 0
            ),
            "orphaned_transactions": consensus_health.get(
                "total_orphaned_transactions", 0
            ),
            "stuck_finalization_count": consensus_health.get(
                "stuck_finalization_count", 0
            ),
            "recovery_storm_count": consensus_health.get("recovery_storm_count", 0),
            "max_recovery_count": consensus_health.get("max_recovery_count", 0),
            "max_recovery_exhausted_count": consensus_health.get(
                "max_recovery_exhausted_count", 0
            ),
            "no_consensus_progress": consensus_health.get(
                "no_consensus_progress", False
            ),
            "no_progress_backlog_count": consensus_health.get(
                "no_progress_backlog_count", 0
            ),
            "seconds_since_consensus_progress": consensus_health.get(
                "seconds_since_consensus_progress"
            ),
            "no_progress_check_error": consensus_health.get(
                "no_progress_check_error", False
            ),
            "active_workers": consensus_health.get("active_workers", 0),
            "status": consensus_status,
        }
        if consensus_status in ["unhealthy", "error"]:
            if overall_status == "healthy":
                overall_status = "degraded"
            issues.append("consensus_issue")
        else:
            if consensus_health.get("total_orphaned_transactions", 0) >= 3:
                if overall_status == "healthy":
                    overall_status = "degraded"
                issues.append("orphaned_transactions")
            if consensus_health.get("stuck_finalization_count", 0) >= 3:
                if overall_status == "healthy":
                    overall_status = "degraded"
                issues.append("stuck_finalizations")
            if consensus_health.get("recovery_storm_count", 0) > 0:
                if overall_status == "healthy":
                    overall_status = "degraded"
                issues.append("transaction_recovery_storm")
            if consensus_health.get("max_recovery_exhausted_count", 0) > 0:
                if overall_status == "healthy":
                    overall_status = "degraded"
                issues.append("max_recovery_cycles_exhausted")
            if consensus_health.get("no_consensus_progress", False):
                if overall_status == "healthy":
                    overall_status = "degraded"
                issues.append("no_consensus_progress")

        # 4. LLM provider health (per-provider failure-rate detection)
        # Catches cases like a provider returning HTTP 402 / "tier required"
        # for every call from a specific (provider, model) entry — the
        # actual cause behind a recent stuck-shard incident that the
        # generic "orphaned_transactions" tag couldn't articulate.
        llm_health = await _check_llm_provider_health()
        llm_status = llm_health.get("status", "unknown")
        services["llm_providers"] = {
            "status": llm_status,
            "alert_providers": llm_health.get("alert_providers", []),
            "window_minutes": llm_health.get("window_minutes"),
            "total_samples": llm_health.get("total_samples"),
        }
        if llm_status == "error":
            issues.append("llm_provider_check_error")
        elif llm_status == "degraded" and llm_health.get("alert_providers"):
            if overall_status == "healthy":
                overall_status = "degraded"
            issues.append("llm_provider_failure")

        # 5. Memory health
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

        # 6. Redis health
        redis_status = await _check_redis_health()
        services["redis"] = redis_status
        if redis_status == "unhealthy":
            if overall_status == "healthy":
                overall_status = "degraded"
            issues.append("redis_unreachable")

        # 7. Aggregate counts for metrics
        decisions_count, users_count, pending_count = await _get_aggregate_counts()
        _health_cache.total_decisions = decisions_count
        _health_cache.total_users = users_count
        _health_cache.pending_transactions = pending_count
        _health_cache.uptime_percent = 100.0  # 100% while running

        # 8. Get pending contracts breakdown for dashboard
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

        # Test database connectivity — run in thread to avoid blocking event loop
        def _db_ping():
            start_t = time.time()
            with db_manager.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return (time.time() - start_t) * 1000

        db_healthy = False
        query_time_ms = 0
        try:
            query_time_ms = await asyncio.to_thread(_db_ping)
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
    """Check consensus system status.

    "Orphaned transactions" here means *contracts whose head-of-queue tx
    isn't making progress* — not "lots of pending txs". A long queue is
    not a problem if the head is moving; the head being stuck IS a
    problem regardless of queue depth.

    A contract's head is the oldest non-final tx for that contract. The
    head is "stuck" when:
      - it was created more than `head_stuck_after_minutes` ago
        (enough time to expect *some* progress), AND
      - no tx for that contract has a fresh `blocked_at` within the
        `recent_activity_window_minutes` window (i.e. no worker is
        currently doing anything for the contract).

    `active_workers` is derived from unexpired worker claims so it
    correctly reflects workers processing OLD txs (the previous "txs
    created in last 1h with worker_id" filter falsely reported zero
    workers when traffic was bursty).

    Note on the "claim window": workers do NOT heartbeat `blocked_at`
    during execution — it's set once at claim time, cleared on
    completion. So "fresh blocked_at" must mean "claim not yet
    expired", aligned with TRANSACTION_TIMEOUT_MINUTES, not "claimed
    within the last few minutes" (which would falsely flag a long
    consensus round as inactive).
    """
    import os

    from backend.database_handler.session_factory import get_database_manager

    HEAD_STUCK_AFTER_MINUTES = int(
        os.environ.get("HEALTH_HEAD_STUCK_AFTER_MINUTES", "15")
    )
    # Aligned with the worker's claim timeout. While blocked_at sits
    # within this window, a worker still legitimately owns the tx and
    # we must not call its contract "stuck". Past this window, recovery
    # would also reset the claim. Default 30 matches docker-compose /
    # prod manifests.
    CLAIM_WINDOW_MINUTES = int(os.environ.get("TRANSACTION_TIMEOUT_MINUTES", "30"))
    # Match the dashboard alert threshold so consensus.status and the
    # top-level "issues" tag agree on what counts as degraded.
    DEGRADED_AT_STUCK_HEADS = int(os.environ.get("HEALTH_DEGRADED_AT_STUCK_HEADS", "3"))
    # Finalization-stall threshold: ACCEPTED/UNDETERMINED/*_TIMEOUT txs
    # that haven't reached FINALIZED within this many seconds count as
    # stuck. Default 600s (10 min) — finalization is supposed to be
    # quick after the finality window opens.
    STUCK_FINALIZATION_AFTER_SECONDS = int(
        os.environ.get("HEALTH_STUCK_FINALIZATION_AFTER_SECONDS", "600")
    )
    DEGRADED_AT_STUCK_FINALIZATIONS = int(
        os.environ.get("HEALTH_DEGRADED_AT_STUCK_FINALIZATIONS", "3")
    )
    NO_PROGRESS_WINDOW_MINUTES = int(
        os.environ.get("HEALTH_NO_PROGRESS_WINDOW_MINUTES", "30")
    )
    NO_PROGRESS_MIN_BACKLOG = int(os.environ.get("HEALTH_NO_PROGRESS_MIN_BACKLOG", "3"))
    NO_PROGRESS_QUERY_TIMEOUT_MS = int(
        os.environ.get("HEALTH_NO_PROGRESS_QUERY_TIMEOUT_MS", "5000")
    )
    RECOVERY_STORM_MIN_RECOVERIES = int(
        os.environ.get("HEALTH_RECOVERY_STORM_MIN_RECOVERIES", "2")
    )
    MAX_RECOVERY_EXHAUSTED_NOTICE_WINDOW_MINUTES = int(
        os.environ.get("HEALTH_MAX_RECOVERY_EXHAUSTED_NOTICE_WINDOW_MINUTES", "60")
    )

    # Statuses where the consensus state machine is actively working.
    # The "head of queue stuck" check uses ONLY these: ACCEPTED-class
    # statuses are post-consensus and live under the separate
    # finalization-stall detector below.
    CONSENSUS_ACTIVE_STATUSES_SQL = "'ACTIVATED','PROPOSING','COMMITTING','REVEALING'"
    # Broader set including finalization-pending statuses. Used only
    # in the "is any worker actively claiming on this contract" NOT
    # EXISTS clause — a finalization worker counts as active work and
    # should mask a stuck consensus head.
    ANY_INFLIGHT_STATUSES_SQL = (
        "'ACTIVATED','PROPOSING','COMMITTING','REVEALING',"
        "'ACCEPTED','UNDETERMINED','LEADER_TIMEOUT','VALIDATORS_TIMEOUT'"
    )
    FINALIZATION_ELIGIBLE_STATUSES_SQL = (
        "'ACCEPTED','UNDETERMINED','LEADER_TIMEOUT','VALIDATORS_TIMEOUT'"
    )
    PRE_CONSENSUS_BACKLOG_STATUSES_SQL = (
        "'PENDING','ACTIVATED','PROPOSING','COMMITTING','REVEALING'"
    )

    try:
        if not _rpc_router_ref:
            return {"status": "not_initialized", "error": "RPC router not available"}

        db_manager = get_database_manager()

        def _query_consensus():
            from sqlalchemy import text

            with db_manager.engine.connect() as conn:
                # Active workers: distinct worker_ids with an unexpired
                # claim. Catches workers processing old txs that the
                # previous "created_at > 1h ago" filter incorrectly
                # excluded.
                active_workers_row = conn.execute(
                    text(
                        """
                        SELECT COUNT(DISTINCT worker_id) AS n
                        FROM transactions
                        WHERE worker_id IS NOT NULL
                          AND blocked_at IS NOT NULL
                          AND blocked_at > NOW() - make_interval(mins => :claim_window)
                        """
                    ),
                    {"claim_window": CLAIM_WINDOW_MINUTES},
                ).fetchone()
                active_workers_count = active_workers_row.n if active_workers_row else 0

                # Stuck heads: per contract, the oldest consensus-active
                # tx whose contract has no unexpired worker claim AND
                # head is old enough to expect progress. Head CTE uses
                # only the consensus-active set — ACCEPTED-class rows
                # are post-consensus and would otherwise pollute the
                # signal (one stranded UNDETERMINED on an unused
                # contract is not a stuck head). The NOT EXISTS clause
                # uses the broader set so a finalization worker
                # currently processing on the same contract correctly
                # masks the alarm.
                # Returns the count of AFFECTED CONTRACTS, not the
                # count of queued txs behind them.
                stuck_row = conn.execute(
                    text(
                        f"""
                        WITH heads AS (
                            SELECT DISTINCT ON (to_address)
                                to_address,
                                hash,
                                status,
                                created_at
                            FROM transactions
                            WHERE status IN ({CONSENSUS_ACTIVE_STATUSES_SQL})
                              AND to_address IS NOT NULL
                            ORDER BY to_address, created_at ASC, hash ASC
                        )
                        SELECT COUNT(*) AS stuck_heads
                        FROM heads h
                        WHERE h.created_at < NOW() - make_interval(mins => :head_stuck_minutes)
                          AND NOT EXISTS (
                              SELECT 1
                              FROM transactions t2
                              WHERE t2.to_address = h.to_address
                                AND t2.status IN ({ANY_INFLIGHT_STATUSES_SQL})
                                AND t2.blocked_at IS NOT NULL
                                AND t2.blocked_at > NOW() - make_interval(mins => :claim_window)
                          )
                        """
                    ),
                    {
                        "head_stuck_minutes": HEAD_STUCK_AFTER_MINUTES,
                        "claim_window": CLAIM_WINDOW_MINUTES,
                    },
                ).fetchone()
                stuck_head_contracts = stuck_row.stuck_heads if stuck_row else 0

                # Stuck finalizations: ACCEPTED-class txs waiting too
                # long to reach FINALIZED. Two paths:
                #   1. timestamp_awaiting_finalization set and stale
                #      (normal "finalizer not running" case)
                #   2. timestamp_awaiting_finalization NULL and the row
                #      is old (catches future bugs like the May 2026
                #      insufficient-balance SEND path, where a tx
                #      reached UNDETERMINED without ever stamping the
                #      timestamp — invisible to claim_next_finalization
                #      forever)
                stuck_fin_row = conn.execute(
                    text(
                        f"""
                        SELECT COUNT(*) AS n
                        FROM transactions
                        WHERE status IN ({FINALIZATION_ELIGIBLE_STATUSES_SQL})
                          AND (
                              (timestamp_awaiting_finalization IS NOT NULL
                               AND EXTRACT(EPOCH FROM NOW())::bigint
                                   - timestamp_awaiting_finalization
                                   > :stuck_seconds)
                              OR
                              (timestamp_awaiting_finalization IS NULL
                               AND created_at
                                   < NOW() - make_interval(secs => :stuck_seconds))
                          )
                        """
                    ),
                    {"stuck_seconds": STUCK_FINALIZATION_AFTER_SECONDS},
                ).fetchone()
                stuck_finalization_count = stuck_fin_row.n if stuck_fin_row else 0

                # Recovery storm: a non-terminal transaction that has already
                # been reset several times is a high-confidence poison-tx
                # signal. This catches the crash-loop pattern even when each
                # individual retry creates fresh blocked_at activity and masks
                # the stuck-head detector.
                recovery_row = conn.execute(
                    text(
                        f"""
                        SELECT
                            COUNT(*) AS n,
                            COALESCE(MAX(recovery_count), 0) AS max_recovery_count
                        FROM transactions
                        WHERE status IN ({PRE_CONSENSUS_BACKLOG_STATUSES_SQL})
                          AND recovery_count >= :min_recoveries
                        """
                    ),
                    {"min_recoveries": RECOVERY_STORM_MIN_RECOVERIES},
                ).fetchone()
                recovery_storm_count = recovery_row.n if recovery_row else 0
                max_recovery_count = (
                    recovery_row.max_recovery_count if recovery_row else 0
                )

                max_recovery_exhausted_row = conn.execute(
                    text(
                        """
                        SELECT COUNT(*) AS n
                        FROM transactions
                        WHERE status = 'CANCELED'
                          AND consensus_data ->> 'error'
                              = 'max_recovery_cycles_exceeded'
                          AND CASE
                              WHEN consensus_data
                                   ->> 'max_recovery_exhausted_at'
                                   ~ '^[0-9]+(\\.[0-9]+)?$'
                              THEN to_timestamp(
                                  (
                                      consensus_data
                                      ->> 'max_recovery_exhausted_at'
                                  )::double precision
                              )
                              ELSE created_at
                          END > NOW() - CAST(:notice_window AS INTERVAL)
                        """
                    ),
                    {
                        "notice_window": (
                            f"{MAX_RECOVERY_EXHAUSTED_NOTICE_WINDOW_MINUTES} minutes"
                        )
                    },
                ).fetchone()
                max_recovery_exhausted_count = (
                    max_recovery_exhausted_row.n if max_recovery_exhausted_row else 0
                )

                # Total in-flight (non-final) tx count, for context.
                # Consensus-active only — finalization-pending rows
                # are tracked separately via stuck_finalization_count.
                processing_row = conn.execute(
                    text(
                        f"""
                        SELECT COUNT(*) AS n
                        FROM transactions
                        WHERE status IN ({CONSENSUS_ACTIVE_STATUSES_SQL})
                          AND to_address IS NOT NULL
                        """
                    )
                ).fetchone()
                total_processing = processing_row.n if processing_row else 0

                no_progress_window_seconds = NO_PROGRESS_WINDOW_MINUTES * 60
                no_progress_check_error = False

                # No-progress detector: first do a cheap backlog gate. The
                # progress scan has to inspect JSON history and can be expensive
                # on large Rally-style datasets; if there is no old backlog,
                # scanning history cannot change the alert decision.
                backlog_row = conn.execute(
                    text(
                        f"""
                        SELECT
                            COUNT(*) AS backlog_count,
                            MIN(created_at) AS oldest_created_at,
                            EXTRACT(EPOCH FROM (NOW() - MIN(created_at)))::bigint
                                AS oldest_backlog_age_seconds
                        FROM transactions
                        WHERE status IN ({PRE_CONSENSUS_BACKLOG_STATUSES_SQL})
                        """
                    )
                ).fetchone()

                no_progress_backlog_count = (
                    backlog_row.backlog_count if backlog_row else 0
                )
                oldest_backlog_age_seconds = (
                    backlog_row.oldest_backlog_age_seconds
                    if backlog_row and backlog_row.oldest_created_at
                    else None
                )

                should_scan_progress = (
                    no_progress_backlog_count >= NO_PROGRESS_MIN_BACKLOG
                    and oldest_backlog_age_seconds is not None
                    and oldest_backlog_age_seconds > no_progress_window_seconds
                )

                seconds_since_consensus_progress = None
                last_progress_epoch = 0
                if should_scan_progress:
                    try:
                        conn.execute(
                            text(
                                f"SET LOCAL statement_timeout = {NO_PROGRESS_QUERY_TIMEOUT_MS}"
                            )
                        )
                        progress_row = conn.execute(
                            text(
                                """
                                SELECT
                                    MAX(
                                        GREATEST(
                                            COALESCE(
                                                CASE
                                                    WHEN consensus_history
                                                         -> 'current_monitoring'
                                                         ->> 'ACCEPTED'
                                                         ~ '^[0-9]+(\\.[0-9]+)?$'
                                                    THEN (
                                                        consensus_history
                                                        -> 'current_monitoring'
                                                        ->> 'ACCEPTED'
                                                    )::double precision
                                                END,
                                                0
                                            ),
                                            COALESCE(
                                                CASE
                                                    WHEN consensus_history
                                                         -> 'current_monitoring'
                                                         ->> 'FINALIZED'
                                                         ~ '^[0-9]+(\\.[0-9]+)?$'
                                                    THEN (
                                                        consensus_history
                                                        -> 'current_monitoring'
                                                        ->> 'FINALIZED'
                                                    )::double precision
                                                END,
                                                0
                                            )
                                        )
                                    ) AS last_progress_epoch
                                FROM transactions
                                WHERE consensus_history IS NOT NULL
                                """
                            )
                        ).fetchone()
                        last_progress_epoch = (
                            progress_row.last_progress_epoch if progress_row else 0
                        )
                        seconds_since_consensus_progress = (
                            int(time.time() - last_progress_epoch)
                            if last_progress_epoch
                            else None
                        )
                    except Exception as exc:
                        no_progress_check_error = True
                        logger.warning(
                            "No-progress health query skipped after timeout/error: %s",
                            exc,
                        )

                # The progress scan is an alert-quality check, not a liveness
                # requirement. If it times out on a large table, surface that
                # as check_error but do not assert a consensus outage.
                no_recent_progress = (
                    should_scan_progress
                    and not no_progress_check_error
                    and (
                        not last_progress_epoch
                        or (
                            seconds_since_consensus_progress is not None
                            and seconds_since_consensus_progress
                            > no_progress_window_seconds
                        )
                    )
                )
                no_consensus_progress = (
                    no_progress_backlog_count >= NO_PROGRESS_MIN_BACKLOG
                    and oldest_backlog_age_seconds is not None
                    and oldest_backlog_age_seconds > no_progress_window_seconds
                    and no_recent_progress
                )

                status = (
                    "degraded"
                    if (
                        stuck_head_contracts >= DEGRADED_AT_STUCK_HEADS
                        or stuck_finalization_count >= DEGRADED_AT_STUCK_FINALIZATIONS
                        or recovery_storm_count > 0
                        or max_recovery_exhausted_count > 0
                        or no_consensus_progress
                    )
                    else "healthy"
                )

                return {
                    "status": status,
                    "total_processing_transactions": total_processing,
                    # Field name preserved for backwards-compat with the
                    # external dashboard. Semantics: count of CONTRACTS
                    # whose consensus head is stuck.
                    "total_orphaned_transactions": stuck_head_contracts,
                    "stuck_finalization_count": stuck_finalization_count,
                    "recovery_storm_count": recovery_storm_count,
                    "max_recovery_count": max_recovery_count,
                    "max_recovery_exhausted_count": max_recovery_exhausted_count,
                    "no_consensus_progress": no_consensus_progress,
                    "no_progress_backlog_count": no_progress_backlog_count,
                    "oldest_backlog_age_seconds": oldest_backlog_age_seconds,
                    "seconds_since_consensus_progress": (
                        seconds_since_consensus_progress
                    ),
                    "no_progress_check_error": no_progress_check_error,
                    "no_progress_window_minutes": NO_PROGRESS_WINDOW_MINUTES,
                    "active_workers": active_workers_count,
                }

        return await asyncio.to_thread(_query_consensus)

    except Exception as e:
        logger.exception("Consensus health check failed")
        return {"status": "error", "error": str(e)}


async def _check_llm_provider_health() -> Dict[str, Any]:
    """Per-(provider, model) failure-rate detection from recent receipts.

    Mines `consensus_data->'leader_receipt'` and
    `consensus_data->'validators'` on txs created in the last
    `LLM_PROVIDER_WINDOW_MINUTES` minutes. For each (provider, model)
    pair, counts validator runs whose `execution_result == 'ERROR'` and
    reports those whose failure rate is above
    `LLM_PROVIDER_FAILURE_THRESHOLD` AND have at least
    `LLM_PROVIDER_MIN_SAMPLES` runs in the window.

    Output schema (always shaped, never partial):

        {
          "status": "healthy" | "degraded" | "no_data" | "error",
          "alert_providers": [
              {
                "provider": "...", "model": "...",
                "samples": int, "failures": int, "failure_rate": float,
                "sample_error": {error_code, causes, http_status, brief}
              }, ...
          ],
          "window_minutes": int,
          "total_samples": int,
        }

    Limitations (acknowledged):
      - Window is on `tx.created_at`, not "consensus ran in the last N
        min" — the schema has no `status_changed_at`. For very slow
        contracts a tx that finished consensus 6h after submission falls
        outside the window. Acceptable trade-off for a 15-minute alert.
      - Cannot detect "primary failed but fallback rescued it":
        backend/node/llm.lua's try_provider returns SUCCESS as soon as
        any provider succeeds, with no persisted record of the primary
        attempt. This metric catches all-providers-down (the primary
        failure mode that took an instance into DEGRADED yesterday) but
        not stealth fallback-masked outages. A future improvement would
        persist `llm_attempts[]` from Lua at call time.

    Privacy: never expose raw stderr (contains LLM prompts), node_config
    (contains validator private keys), or raw_error.ctx.host_data
    (contains payloads). Only emits structured signals: error_code,
    causes (joined string), HTTP status, and a 200-char cap of
    error_description.
    """
    import os

    from backend.database_handler.session_factory import get_database_manager

    WINDOW_MINUTES = int(os.environ.get("LLM_PROVIDER_WINDOW_MINUTES", "15"))
    MIN_SAMPLES = int(os.environ.get("LLM_PROVIDER_MIN_SAMPLES", "25"))
    FAILURE_THRESHOLD = float(os.environ.get("LLM_PROVIDER_FAILURE_THRESHOLD", "0.5"))

    try:
        if not _rpc_router_ref:
            return {
                "status": "not_initialized",
                "error": "RPC router not available",
            }

        db_manager = get_database_manager()

        def _query_llm_health():
            from sqlalchemy import text

            with db_manager.engine.connect() as conn:
                # One row per (provider, model) over the window.
                # Concatenates leader_receipt[] and validators[] arrays so
                # leader-only txs (where validators is empty) still count.
                # Status filter excludes in-flight rows where
                # consensus_data could still mutate.
                agg_rows = conn.execute(
                    text(
                        """
                        WITH receipts AS (
                            SELECT
                                v.receipt->'node_config'->'primary_model'->>'provider'
                                    AS provider,
                                v.receipt->'node_config'->'primary_model'->>'model'
                                    AS model,
                                v.receipt->>'execution_result' AS execution_result,
                                v.receipt->'genvm_result'->>'error_code' AS error_code,
                                -- Use ->> not -> so a JSONB null literal
                                -- becomes SQL NULL (otherwise IS NOT NULL
                                -- is true for `raw_error: null`).
                                (v.receipt->'genvm_result'->>'raw_error') IS NOT NULL
                                    AS has_raw_error
                            FROM transactions t,
                                 jsonb_array_elements(
                                     COALESCE(
                                         t.consensus_data->'leader_receipt',
                                         '[]'::jsonb
                                     )
                                     || COALESCE(
                                         t.consensus_data->'validators',
                                         '[]'::jsonb
                                     )
                                 ) AS v(receipt)
                            WHERE t.consensus_data IS NOT NULL
                              AND t.created_at > NOW()
                                  - make_interval(mins => :window_minutes)
                              AND t.status IN (
                                  'FINALIZED', 'ACCEPTED', 'UNDETERMINED',
                                  'LEADER_TIMEOUT', 'VALIDATORS_TIMEOUT'
                              )
                              AND v.receipt->'node_config'->'primary_model'->>'provider'
                                      IS NOT NULL
                        )
                        SELECT
                            provider,
                            model,
                            COUNT(*) AS samples,
                            -- Only count errors that have a structured
                            -- LLM/manager error_code OR raw_error block.
                            -- Contract-side Python exceptions (e.g. a user
                            -- contract using a non-existent SDK attribute)
                            -- also surface as execution_result = 'ERROR',
                            -- but have error_code = null AND raw_error =
                            -- null because the failure happened inside the
                            -- user code before any LLM call. Counting those
                            -- here would let one broken contract make every
                            -- validator (across all models) look like an
                            -- LLM provider failure — exactly the false
                            -- signal that fired Studio Prod's nonstop
                            -- llm_provider_failure alert (May 2026).
                            COUNT(*) FILTER (
                                WHERE execution_result = 'ERROR'
                                  AND (
                                      error_code IS NOT NULL
                                      OR has_raw_error
                                  )
                            ) AS failures
                        FROM receipts
                        GROUP BY provider, model
                        """
                    ),
                    {"window_minutes": WINDOW_MINUTES},
                ).fetchall()

                if not agg_rows:
                    return {
                        "status": "no_data",
                        "alert_providers": [],
                        "window_minutes": WINDOW_MINUTES,
                        "total_samples": 0,
                    }

                total_samples = sum(r.samples for r in agg_rows)

                # Pick which (provider, model) pairs are alert-worthy
                # before doing the (more expensive) sample-error query.
                alert_keys = []
                for r in agg_rows:
                    if (
                        r.samples >= MIN_SAMPLES
                        and r.failures / r.samples >= FAILURE_THRESHOLD
                    ):
                        alert_keys.append((r.provider, r.model, r))

                # Sample errors: one most-recent ERROR per alert (provider,
                # model). Done as a separate query to keep the aggregate
                # cheap when there are no alerts. Allowlists structured
                # fields only — no raw stderr, no node_config.
                sample_errors_by_key: dict[tuple, dict] = {}
                if alert_keys:
                    err_rows = conn.execute(
                        text(
                            """
                            WITH error_rows AS (
                                SELECT
                                    v.receipt->'node_config'->'primary_model'->>'provider'
                                        AS provider,
                                    v.receipt->'node_config'->'primary_model'->>'model'
                                        AS model,
                                    v.receipt->'genvm_result'->>'error_code'
                                        AS error_code,
                                    v.receipt->'genvm_result'->'raw_error'->'causes'
                                        AS causes,
                                    v.receipt->'genvm_result'->'raw_error'->'ctx'->>'status'
                                        AS http_status,
                                    SUBSTRING(
                                        v.receipt->'genvm_result'->>'error_description'
                                        FROM 1 FOR 200
                                    ) AS description_brief,
                                    t.created_at AS tx_created_at,
                                    ROW_NUMBER() OVER (
                                        PARTITION BY
                                            v.receipt->'node_config'->'primary_model'->>'provider',
                                            v.receipt->'node_config'->'primary_model'->>'model'
                                        ORDER BY t.created_at DESC
                                    ) AS rn
                                FROM transactions t,
                                     jsonb_array_elements(
                                         COALESCE(
                                             t.consensus_data->'leader_receipt',
                                             '[]'::jsonb
                                         )
                                         || COALESCE(
                                             t.consensus_data->'validators',
                                             '[]'::jsonb
                                         )
                                     ) AS v(receipt)
                                WHERE t.consensus_data IS NOT NULL
                                  AND t.created_at > NOW()
                                      - make_interval(mins => :window_minutes)
                                  AND t.status IN (
                                      'FINALIZED', 'ACCEPTED', 'UNDETERMINED',
                                      'LEADER_TIMEOUT', 'VALIDATORS_TIMEOUT'
                                  )
                                  AND v.receipt->>'execution_result' = 'ERROR'
                                  -- Match the aggregate filter: only sample
                                  -- structured LLM/manager errors, skip
                                  -- contract-side Python crashes.
                                  AND (
                                      v.receipt->'genvm_result'->>'error_code'
                                          IS NOT NULL
                                      OR (v.receipt->'genvm_result'->>'raw_error')
                                          IS NOT NULL
                                  )
                                  AND v.receipt->'node_config'->'primary_model'->>'provider'
                                          IS NOT NULL
                            )
                            SELECT provider, model, error_code, causes,
                                   http_status, description_brief
                            FROM error_rows
                            WHERE rn = 1
                            """
                        ),
                        {"window_minutes": WINDOW_MINUTES},
                    ).fetchall()
                    for er in err_rows:
                        causes_summary = None
                        if er.causes:
                            try:
                                causes_summary = ", ".join(str(c) for c in er.causes)[
                                    :200
                                ]
                            except (TypeError, ValueError):
                                causes_summary = str(er.causes)[:200]
                        sample_errors_by_key[(er.provider, er.model)] = {
                            "error_code": er.error_code,
                            "causes": causes_summary,
                            "http_status": er.http_status,
                            "description_brief": er.description_brief,
                        }

                alert_providers = []
                for provider, model, r in alert_keys:
                    alert_providers.append(
                        {
                            "provider": provider,
                            "model": model,
                            "samples": int(r.samples),
                            "failures": int(r.failures),
                            "failure_rate": round(r.failures / r.samples, 3),
                            "sample_error": sample_errors_by_key.get((provider, model)),
                        }
                    )
                # Sort: highest failure rate first, then highest sample
                # count, for stable & operator-friendly output.
                alert_providers.sort(key=lambda a: (-a["failure_rate"], -a["samples"]))

                status = "degraded" if alert_providers else "healthy"
                return {
                    "status": status,
                    "alert_providers": alert_providers,
                    "window_minutes": WINDOW_MINUTES,
                    "total_samples": total_samples,
                }

        return await asyncio.to_thread(_query_llm_health)

    except Exception as e:
        logger.exception("LLM provider health check failed")
        return {"status": "error", "error": str(e)}


async def _check_memory_health() -> Dict[str, Any]:
    """Check memory usage and CPU usage."""
    try:
        import psutil

        process = psutil.Process()
        memory_info = process.memory_info()

        # cpu_percent(interval=0.1) blocks for 100ms — run in thread to avoid
        # freezing the event loop (this runs every health-check cycle).
        cpu_pct = await asyncio.to_thread(process.cpu_percent, 0.1)

        return {
            "status": "healthy",
            "memory_usage_mb": memory_info.rss / 1024 / 1024,
            "memory_percent": process.memory_percent(),
            "cpu_percent": cpu_pct,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


async def _get_aggregate_counts() -> tuple[int, int, int]:
    """Query total decisions, unique users, and pending transactions from database."""
    from sqlalchemy import text

    def _query():
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

    try:
        return await asyncio.to_thread(_query)
    except Exception as e:
        logger.warning(f"Failed to get aggregate counts: {e}")
        return (0, 0, 0)


async def _get_pending_contracts() -> list[dict]:
    """Query pending transactions grouped by contract address."""
    from sqlalchemy import text

    def _query():
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

    try:
        return await asyncio.to_thread(_query)
    except Exception as e:
        logger.warning(f"Failed to get pending contracts: {e}")
        return []


async def _check_redis_health() -> str:
    """Check Redis connectivity using async client."""
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        return "not_configured"

    try:
        import redis.asyncio as aioredis

        redis_client = aioredis.from_url(redis_url)
        try:
            await redis_client.ping()
            return "healthy"
        finally:
            await redis_client.aclose()
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

        # Test database connectivity — run in thread to avoid blocking event loop
        def _db_ping_detail():
            start_t = time.time()
            with db_manager.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return (time.time() - start_t) * 1000

        db_healthy = False
        query_time_ms = 0
        try:
            query_time_ms = await asyncio.to_thread(_db_ping_detail)
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


@health_router.get("/health/ratelimit")
async def health_ratelimit() -> Dict[str, Any]:
    """Show per-address gen_call rate limit state."""
    from backend.protocol_rpc.endpoints import (
        _address_request_log,
        _RATE_LIMIT_WINDOW,
        _RATE_LIMIT_MAX,
        _genvm_semaphore,
        _GENVM_CONCURRENCY,
    )
    import time as _time

    now = _time.monotonic()
    cutoff = now - _RATE_LIMIT_WINDOW
    addresses = {}
    for addr, timestamps in _address_request_log.items():
        recent = [t for t in timestamps if t > cutoff]
        if recent:
            addresses[addr] = {
                "requests_in_window": len(recent),
                "limit": _RATE_LIMIT_MAX,
                "oldest_in_window_age_s": round(now - min(recent), 1),
            }
    return {
        "window_seconds": _RATE_LIMIT_WINDOW,
        "max_per_window": _RATE_LIMIT_MAX,
        "genvm_concurrency_limit": _GENVM_CONCURRENCY,
        "genvm_semaphore_available": _genvm_semaphore._value,  # noqa: SLF001
        "active_addresses": len(addresses),
        "addresses": addresses,
    }


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

        def _collect_cpu():
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

        return await asyncio.to_thread(_collect_cpu)
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

        def _query_consensus_detail():
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
                            if (
                                tx_info
                                and tx_info.get("worker_id") not in active_workers
                            ):
                                orphaned_tx_hashes.append(tx_info.get("hash"))

                    contract_data["orphaned_transactions"] = len(orphaned_tx_hashes)
                    if orphaned_tx_hashes:
                        contract_data["orphaned_transaction_hashes"] = (
                            orphaned_tx_hashes
                        )
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

        return await asyncio.to_thread(_query_consensus_detail)

    except Exception as e:
        logger.exception("Consensus health check failed")
        return {"status": "error", "error": str(e)}


@health_router.get("/metrics")
async def metrics():
    """Return worker metrics for autoscaling in Prometheus format."""
    from fastapi.responses import Response
    from prometheus_client import (
        CollectorRegistry,
        Gauge,
        generate_latest,
        CONTENT_TYPE_LATEST,
    )
    from backend.database_handler.session_factory import get_database_manager

    try:

        def _query_metrics():
            from sqlalchemy import text

            db_manager = get_database_manager()
            with db_manager.engine.connect() as conn:
                row = conn.execute(
                    text(
                        """
                        WITH per_contract AS (
                            SELECT
                                to_address,
                                BOOL_OR(status = 'PENDING') AS has_pending,
                                BOOL_OR(status IN ('PROPOSING', 'COMMITTING', 'UNDETERMINED')) AS has_inflight
                            FROM transactions
                            WHERE status IN ('PENDING', 'PROPOSING', 'COMMITTING', 'UNDETERMINED')
                            GROUP BY to_address
                        )
                        SELECT
                            COALESCE(COUNT(*) FILTER (WHERE has_inflight), 0) AS occupied,
                            COALESCE(COUNT(*) FILTER (WHERE has_pending AND NOT has_inflight), 0) AS runnable
                        FROM per_contract
                        """
                    )
                ).fetchone()

                occupied = row[0] if row else 0
                runnable = row[1] if row else 0
                return occupied, runnable

        occupied_count, runnable_count = await asyncio.to_thread(_query_metrics)

        base = occupied_count + runnable_count
        # Add 10% headroom for burst absorption, minimum 0 (HPA minReplicas handles floor)
        needed_workers_count = math.ceil(base * 1.10) if base > 0 else 0

        # Create a fresh registry for each request to avoid duplicate metrics
        registry = CollectorRegistry()
        occupied_contracts = Gauge(
            "genlayer_occupied_contracts",
            "Contracts with an in-flight transaction (worker actively processing)",
            registry=registry,
        )
        runnable_contracts = Gauge(
            "genlayer_runnable_contracts",
            "Contracts with pending work and no in-flight transaction",
            registry=registry,
        )
        needed_workers = Gauge(
            "genlayer_needed_workers",
            "Workers needed: distinct schedulable contracts + 10% headroom",
            registry=registry,
        )
        occupied_contracts.set(occupied_count)
        runnable_contracts.set(runnable_count)
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
