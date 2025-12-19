# backend/consensus/worker_service.py

import os
import asyncio
import signal
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

from backend.consensus.worker import ConsensusWorker
from backend.protocol_rpc.message_handler.redis_worker_handler import (
    RedisWorkerMessageHandler,
)
from backend.protocol_rpc.configuration import GlobalConfiguration
from backend.rollup.consensus_service import ConsensusService
import backend.validators as validators
from backend.database_handler.models import Base
from loguru import logger

from backend.protocol_rpc.app_lifespan import create_genvm_manager


# region agent log
def _agent_log(hypothesis_id: str, location: str, message: str, data: dict):
    """Best-effort NDJSON log for debug mode; never raises. Avoid secrets."""
    import json as _json
    import os as _os
    import time as _time

    payload = {
        "sessionId": "debug-session",
        "runId": _os.getenv("AGENT_DEBUG_RUN_ID", "pre-fix"),
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(_time.time() * 1000),
    }
    try:
        with open(
            "/Users/cristiamdasilva/genlayer/genlayer-studio/.cursor/debug.log", "a"
        ) as f:
            f.write(_json.dumps(payload) + "\n")
    except Exception:
        try:
            print("AGENT_DEBUG " + _json.dumps(payload), flush=True)
        except Exception:
            pass


# endregion


# Load environment variables
load_dotenv()


def get_db_name(database: str) -> str:
    return "genlayer_state" if database == "genlayer" else database


# Global variables for the worker
worker: Optional[ConsensusWorker] = None
worker_task: Optional[asyncio.Task] = None

# GenVM manager health probe cache (avoid probing on every /health hit)
_genvm_health_last_check: float = 0.0
_genvm_health_last_ok: bool = True
_genvm_health_last_error: str | None = None

# Restart tracking
worker_restart_count: int = 0
worker_last_crash_time: Optional[float] = None
worker_permanently_failed: bool = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage the worker lifecycle."""
    global worker, worker_task

    # Set up signal handlers for graceful shutdown logging
    def handle_signal(sig, frame):
        logger.warning(f"Received signal {sig}, initiating graceful shutdown...")

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    logger.info("Starting Consensus Worker Service...")
    print("Starting Consensus Worker Service...")

    # CRITICAL: Kill any orphaned GenVM processes from previous crashes
    # These zombie processes can consume gigabytes of memory outside Docker limits
    logger.info("Cleaning up orphaned GenVM processes from previous crashes...")
    _pkill_rc = os.system("pkill -9 -f 'genvm (llm|web)' 2>/dev/null || true")
    _agent_log(
        "H3",
        "backend/consensus/worker_service.py:pkill_startup",
        "pkill cleanup executed",
        {
            "rc": int(_pkill_rc),
            "GENVMROOT": os.getenv("GENVMROOT"),
            "GENVM_TAG": os.getenv("GENVM_TAG"),
            "PATH_has_genvm": "/genvm/bin" in (os.getenv("PATH") or ""),
        },
    )
    logger.info("GenVM cleanup complete")

    # Database setup
    database_name = "genlayer"
    db_uri = f"postgresql+psycopg2://{os.environ.get('DBUSER')}:{os.environ.get('DBPASSWORD')}@{os.environ.get('DBHOST')}/{get_db_name(database_name)}"

    # Create engine with appropriate pool settings for worker
    engine = create_engine(
        db_uri,
        pool_size=5,  # Smaller pool for worker
        max_overflow=5,
        pool_pre_ping=True,
        pool_recycle=3600,
        pool_timeout=30,
    )

    # Create session factory
    SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=engine, expire_on_commit=False
    )

    def get_session():
        return SessionLocal()

    # Get worker configuration from environment
    worker_id = os.environ.get("WORKER_ID", None)  # Auto-generate if not set
    poll_interval = int(os.environ.get("WORKER_POLL_INTERVAL", "5"))
    transaction_timeout = int(os.environ.get("TRANSACTION_TIMEOUT_MINUTES", "30"))
    redis_url = os.environ.get("REDIS_URL")

    # Validate Redis configuration - REQUIRED for worker service
    if not redis_url:
        error_msg = (
            "FATAL: REDIS_URL environment variable is required for consensus workers. "
            "Consensus workers use Redis pub/sub to broadcast events to RPC instances. "
            "Please set REDIS_URL in your environment (e.g., redis://redis:6379/0)."
        )
        logger.error(error_msg)
        raise RuntimeError(error_msg)

    # Initialize Redis-based message handler for horizontal scaling
    msg_handler = RedisWorkerMessageHandler(
        config=GlobalConfiguration(), worker_id=worker_id, redis_url=redis_url
    )

    # Initialize Redis connection (will raise on failure)
    try:
        await msg_handler.initialize()
        logger.info(f"Worker {msg_handler.worker_id} connected to Redis at {redis_url}")
    except Exception as e:
        error_msg = (
            f"FATAL: Failed to connect to Redis at {redis_url}. "
            f"Consensus workers require Redis for event broadcasting. "
            f"Error: {e}"
        )
        logger.error(error_msg)
        raise RuntimeError(error_msg) from e
    consensus_service = ConsensusService()

    genvm_manager = await create_genvm_manager()
    _agent_log(
        "H1",
        "backend/consensus/worker_service.py:genvm_manager_ready",
        "genvm_manager created",
        {
            "manager_url": getattr(genvm_manager, "url", None),
            "GENVMROOT": os.getenv("GENVMROOT"),
            "GENVM_TAG": os.getenv("GENVM_TAG"),
        },
    )

    # Initialize validators manager (MUST use global to prevent garbage collection)
    global validators_manager  # Declare before assignment to use the global variable
    validators_manager = validators.Manager(SessionLocal(), genvm_manager)
    await validators_manager.restart()
    logger.info("Validators manager initialized and restarted")
    _agent_log(
        "H4",
        "backend/consensus/worker_service.py:validators_restarted",
        "validators_manager.restart finished",
        {"ok": True},
    )

    # Subscribe to validator change events
    async def handle_validator_change(event_data):
        """Reload validators when notified of changes."""
        logger.info(f"Received validator change event: {event_data}")
        await validators_manager.restart()

    # Subscribe to validator events channel
    await msg_handler.subscribe_to_validator_events(handle_validator_change)

    # Create and start the worker
    worker = ConsensusWorker(
        get_session=get_session,
        msg_handler=msg_handler,
        consensus_service=consensus_service,
        validators_manager=validators_manager,
        genvm_manager=genvm_manager,
        worker_id=worker_id,
        poll_interval=poll_interval,
        transaction_timeout_minutes=transaction_timeout,
    )

    # Get restart configuration from environment
    max_restarts = int(os.environ.get("WORKER_MAX_RESTARTS", "5"))
    restart_window_seconds = int(os.environ.get("WORKER_RESTART_WINDOW_SECONDS", "300"))
    base_backoff_seconds = float(os.environ.get("WORKER_RESTART_BACKOFF_SECONDS", "5"))

    # Start the worker in a background task with automatic restart on crash
    async def run_worker_with_auto_restart():
        global worker_restart_count, worker_last_crash_time, worker_permanently_failed

        while True:
            try:
                # Reset the running flag in case this is a restart
                worker.running = True
                logger.info(
                    f"Worker {worker.worker_id} starting (restart count: {worker_restart_count})"
                )
                await worker.run()

                # If run() exits normally (worker.stop() was called), break the loop
                if not worker.running:
                    logger.info(f"Worker {worker.worker_id} stopped gracefully")
                    break

            except asyncio.CancelledError:
                # Task was cancelled externally (shutdown), don't restart
                logger.info(
                    f"Worker {worker.worker_id} task cancelled, exiting restart loop"
                )
                raise

            except Exception as e:
                current_time = time.time()

                # Check if we should reset the restart counter (outside restart window)
                if (
                    worker_last_crash_time is not None
                    and current_time - worker_last_crash_time > restart_window_seconds
                ):
                    logger.info(
                        f"Worker {worker.worker_id} crash outside restart window "
                        f"({restart_window_seconds}s), resetting restart counter"
                    )
                    worker_restart_count = 0

                worker_restart_count += 1
                worker_last_crash_time = current_time

                logger.error(
                    f"Worker {worker.worker_id} crashed with exception: {e}",
                    exc_info=True,
                )

                if worker_restart_count > max_restarts:
                    logger.critical(
                        f"Worker {worker.worker_id} exceeded max restarts ({max_restarts}) "
                        f"within {restart_window_seconds}s window. Marking as permanently failed."
                    )
                    worker_permanently_failed = True
                    # Don't re-raise, just exit the loop - health check will report failure
                    break

                # Calculate backoff with exponential increase, capped at 60 seconds
                backoff = min(
                    base_backoff_seconds * (2 ** (worker_restart_count - 1)), 60
                )
                logger.warning(
                    f"Worker {worker.worker_id} will restart in {backoff:.1f}s "
                    f"(attempt {worker_restart_count}/{max_restarts})"
                )

                await asyncio.sleep(backoff)

                logger.info(f"Restarting worker {worker.worker_id}...")

    worker_task = asyncio.create_task(run_worker_with_auto_restart())

    print(f"Consensus Worker {worker.worker_id} started successfully")

    try:
        yield
    finally:
        # CRITICAL: Always cleanup GenVM processes, even on crash
        # This prevents memory leaks from orphaned genvm subprocesses

        # Cleanup on shutdown
        logger.info("Shutting down Consensus Worker Service...")
        print("Shutting down Consensus Worker Service...")

        if worker:
            worker.stop()

        if worker_task:
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

        # Terminate validators manager to shut down background tasks
        if validators_manager:
            try:
                logger.info("Terminating validators manager and GenVM subprocesses...")
                await validators_manager.terminate()
                logger.info("Validators manager terminated successfully")
            except Exception as e:
                logger.error(f"Error terminating validators manager: {e}")

        # Clean up message handler
        if msg_handler:
            try:
                await msg_handler.close()
            except Exception as e:
                logger.error(f"Error closing message handler: {e}")

        # Final safety check: Kill any remaining genvm processes
        logger.info("Final cleanup: killing any remaining GenVM processes...")
        os.system("pkill -9 -f 'genvm (llm|web)' 2>/dev/null || true")
        logger.info("GenVM cleanup complete")

        print("Consensus Worker Service stopped")

        await genvm_manager.close()


# Create FastAPI app
app = FastAPI(title="Consensus Worker Service", version="1.0.0", lifespan=lifespan)
start_time = time.time()


@app.get("/health")
async def health_check():
    """Health check endpoint for the worker."""
    import psutil
    from datetime import datetime
    from fastapi.responses import JSONResponse
    import aiohttp

    global worker, worker_task, worker_restart_count, worker_permanently_failed
    global _genvm_health_last_check, _genvm_health_last_ok, _genvm_health_last_error

    endpoint_start = time.time()

    if worker is None:
        return {"status": "initializing", "worker_id": None}

    # Check if worker has permanently failed (exceeded max restarts)
    if worker_permanently_failed:
        return JSONResponse(
            status_code=503,
            content={
                "status": "failed",
                "worker_id": worker.worker_id,
                "error": "max_restarts_exceeded",
                "restart_count": worker_restart_count,
            },
        )

    # Check if worker task has died and isn't restarting
    if worker_task and worker_task.done():
        # Worker task finished unexpectedly - this is a failure
        try:
            # This will re-raise any exception from the task
            worker_task.result()
        except Exception as e:
            logger.error(f"Worker task died with exception: {e}")
        return JSONResponse(
            status_code=503,
            content={
                "status": "failed",
                "worker_id": worker.worker_id,
                "error": "worker_task_died",
                "restart_count": worker_restart_count,
            },
        )

    # Get basic metrics
    metrics = {}
    try:
        process = psutil.Process()
        metrics = {
            "memory_mb": round(process.memory_info().rss / 1024 / 1024, 2),
            "cpu_percent": round(process.cpu_percent(), 2),
            "memory_percent": round(process.memory_percent(), 2),
        }
    except:
        pass

    # Optional: probe local GenVM manager responsiveness.
    # This catches the exact failure mode you described: /health looks fine, but GenVM's
    # HTTP server (127.0.0.1:3999) is wedged and never responds, so consensus execution hangs.
    #
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
            timeout_s = float(os.getenv("GENVM_MANAGER_HEALTH_TIMEOUT_SECONDS", "2"))
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

            _agent_log(
                "H5",
                "backend/consensus/worker_service.py:health_genvm_probe",
                "genvm manager health probe executed",
                {
                    "status_url": status_url,
                    "timeout_s": timeout_s,
                    "ok": _genvm_health_last_ok,
                    "error": _genvm_health_last_error,
                },
            )

        if not _genvm_health_last_ok:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unhealthy",
                    "worker_id": worker.worker_id,
                    "error": "genvm_manager_unresponsive",
                    "detail": _genvm_health_last_error,
                },
            )
    except Exception as e:
        # Don't fail health checks due to probe implementation issues
        logger.warning(f"GenVM health probe failed unexpectedly: {e}")

    # Get current transaction info with time calculation
    current_tx = None
    if worker.current_transaction:
        tx = worker.current_transaction.copy()
        if tx.get("blocked_at"):
            try:
                blocked_at = tx["blocked_at"]

                # Handle both datetime objects and ISO strings
                if isinstance(blocked_at, str):
                    blocked_at = datetime.fromisoformat(
                        blocked_at.replace("Z", "+00:00")
                    )

                # Convert to naive UTC datetime for comparison
                if blocked_at.tzinfo is not None:
                    blocked_at = blocked_at.replace(tzinfo=None)

                elapsed = datetime.utcnow() - blocked_at

                # Check if blocked for too long - pod is unhealthy
                # Env override: WORKER_BLOCKED_TX_UNHEALTHY_AFTER_MINUTES (default: 14 minutes)
                try:
                    blocked_tx_unhealthy_after_minutes = int(
                        os.getenv("WORKER_BLOCKED_TX_UNHEALTHY_AFTER_MINUTES", "14")
                    )
                except ValueError:
                    blocked_tx_unhealthy_after_minutes = 14
                if blocked_tx_unhealthy_after_minutes <= 0:
                    blocked_tx_unhealthy_after_minutes = 14

                blocked_tx_unhealthy_after_seconds = (
                    blocked_tx_unhealthy_after_minutes * 60
                )

                if elapsed.total_seconds() > blocked_tx_unhealthy_after_seconds:
                    return JSONResponse(
                        status_code=500,
                        content={
                            "status": "unhealthy",
                            "worker_id": worker.worker_id,
                            "error": f"Transaction blocked for more than {blocked_tx_unhealthy_after_minutes} minutes",
                            "blocked_duration_seconds": elapsed.total_seconds(),
                        },
                    )

                # Format as human-readable time ago
                minutes = int(elapsed.total_seconds() / 60)
                if minutes < 60:
                    tx["blocked_at"] = f"{minutes}m ago"
                else:
                    hours = minutes // 60
                    tx["blocked_at"] = f"{hours}h ago"
            except Exception as e:
                # Log the error but don't fail the health check
                logger.error(f"Error parsing blocked_at timestamp: {e}")
                pass
        current_tx = tx

    return {
        "status": "healthy" if worker.running else "stopping",
        "worker_id": worker.worker_id,
        "current_transaction": current_tx,
        "restart_count": worker_restart_count,
        **metrics,
    }


@app.get("/status")
async def worker_status():
    """Get detailed status of the worker."""
    global worker, worker_restart_count, worker_last_crash_time, worker_permanently_failed

    if worker is None:
        return {"error": "Worker not initialized"}

    return {
        "worker_id": worker.worker_id,
        "running": worker.running,
        "poll_interval": worker.poll_interval,
        "transaction_timeout_minutes": worker.transaction_timeout_minutes,
        "restart_count": worker_restart_count,
        "last_crash_time": worker_last_crash_time,
        "permanently_failed": worker_permanently_failed,
    }


@app.post("/stop")
async def stop_worker():
    """Gracefully stop the worker (for testing/maintenance)."""
    global worker

    if worker:
        worker.stop()
        return {"message": f"Worker {worker.worker_id} stopping"}

    return {"error": "No worker to stop"}


if __name__ == "__main__":
    import uvicorn

    # Run the worker service
    uvicorn.run(
        "worker_service:app",
        host="0.0.0.0",
        port=int(os.getenv("WORKER_PORT", "4001")),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
        reload=False,
    )
