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

# Load environment variables
load_dotenv()


def get_db_name(database: str) -> str:
    return "genlayer_state" if database == "genlayer" else database


# Global variables for the worker
worker: Optional[ConsensusWorker] = None
worker_task: Optional[asyncio.Task] = None


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
    os.system("pkill -9 -f 'genvm (llm|web)' 2>/dev/null || true")
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

    # Initialize validators manager (MUST use global to prevent garbage collection)
    global validators_manager  # Declare before assignment to use the global variable
    validators_manager = validators.Manager(SessionLocal())
    await validators_manager.restart()
    logger.info("Validators manager initialized and restarted")

    # Create and start the worker
    worker = ConsensusWorker(
        get_session=get_session,
        msg_handler=msg_handler,
        consensus_service=consensus_service,
        validators_manager=validators_manager,
        worker_id=worker_id,
        poll_interval=poll_interval,
        transaction_timeout_minutes=transaction_timeout,
    )

    # Start the worker in a background task with exception handling
    async def run_worker_with_error_handling():
        try:
            await worker.run()
        except Exception as e:
            logger.critical(
                f"FATAL: Worker {worker.worker_id} crashed with unhandled exception: {e}",
                exc_info=True,
            )
            # Re-raise to let the container crash and restart
            raise

    worker_task = asyncio.create_task(run_worker_with_error_handling())

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


# Create FastAPI app
app = FastAPI(title="Consensus Worker Service", version="1.0.0", lifespan=lifespan)
start_time = time.time()


@app.get("/health")
async def health_check():
    """Health check endpoint for the worker."""
    import psutil
    from datetime import datetime

    global worker, worker_task

    endpoint_start = time.time()

    if worker is None:
        return {"status": "initializing", "worker_id": None}

    # Check if worker task has died
    if worker_task and worker_task.done():
        # Worker task finished unexpectedly - this is a failure
        try:
            # This will re-raise any exception from the task
            worker_task.result()
        except Exception as e:
            logger.error(f"Worker task died with exception: {e}")
        return {
            "status": "failed",
            "worker_id": worker.worker_id,
            "error": "worker_task_died",
        }

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

    # Get current transaction info with time calculation
    current_tx = None
    if worker.current_transaction:
        tx = worker.current_transaction.copy()
        if tx.get("blocked_at"):
            try:
                blocked_at = datetime.fromisoformat(
                    tx["blocked_at"].replace("Z", "+00:00")
                )
                elapsed = datetime.utcnow() - blocked_at.replace(tzinfo=None)

                # Format as human-readable time ago
                minutes = int(elapsed.total_seconds() / 60)
                if minutes < 60:
                    tx["blocked_at"] = f"{minutes}m ago"
                else:
                    hours = minutes // 60
                    tx["blocked_at"] = f"{hours}h ago"
            except:
                pass
        current_tx = tx

    return {
        "status": "healthy" if worker.running else "stopping",
        "worker_id": worker.worker_id,
        "current_transaction": current_tx,
        **metrics,
    }


@app.get("/status")
async def worker_status():
    """Get detailed status of the worker."""
    global worker

    if worker is None:
        return {"error": "Worker not initialized"}

    return {
        "worker_id": worker.worker_id,
        "running": worker.running,
        "poll_interval": worker.poll_interval,
        "transaction_timeout_minutes": worker.transaction_timeout_minutes,
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
