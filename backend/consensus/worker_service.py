# backend/consensus/worker_service.py

import os
import asyncio
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

from backend.consensus.worker import ConsensusWorker
from backend.protocol_rpc.message_handler.base import MessageHandler
from backend.protocol_rpc.configuration import GlobalConfiguration
from backend.rollup.consensus_service import ConsensusService
import backend.validators as validators
from backend.database_handler.models import Base

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
    
    print("Starting Consensus Worker Service...")
    
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
        autocommit=False, 
        autoflush=False, 
        bind=engine, 
        expire_on_commit=False
    )
    
    def get_session():
        return SessionLocal()
    
    # Initialize components
    msg_handler = MessageHandler(None, config=GlobalConfiguration())  # No WebSocket in worker
    consensus_service = ConsensusService()
    
    # Initialize validators manager
    validators_manager = validators.Manager(SessionLocal())
    await validators_manager.restart()
    
    # Get worker configuration from environment
    worker_id = os.environ.get("WORKER_ID", None)  # Auto-generate if not set
    poll_interval = int(os.environ.get("WORKER_POLL_INTERVAL", "5"))
    transaction_timeout = int(os.environ.get("TRANSACTION_TIMEOUT_MINUTES", "30"))
    
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
    
    # Start the worker in a background task
    worker_task = asyncio.create_task(worker.run())
    
    print(f"Consensus Worker {worker.worker_id} started successfully")
    
    yield
    
    # Cleanup on shutdown
    print("Shutting down Consensus Worker Service...")
    
    if worker:
        worker.stop()
    
    if worker_task:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
    
    print("Consensus Worker Service stopped")


# Create FastAPI app
app = FastAPI(
    title="Consensus Worker Service",
    version="1.0.0",
    lifespan=lifespan
)


@app.get("/health")
async def health_check():
    """Health check endpoint for the worker."""
    global worker
    
    if worker is None:
        return {
            "status": "initializing",
            "worker_id": None
        }
    
    return {
        "status": "healthy" if worker.running else "stopping",
        "worker_id": worker.worker_id
    }


@app.get("/status")
async def worker_status():
    """Get detailed status of the worker."""
    global worker
    
    if worker is None:
        return {
            "error": "Worker not initialized"
        }
    
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