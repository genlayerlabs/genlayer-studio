# backend/protocol_rpc/fastapi_server.py

import os
import json
import asyncio
from os import environ
from typing import Dict, Any, List, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from backend.protocol_rpc.configuration import GlobalConfiguration
from backend.protocol_rpc.message_handler.fastapi_handler import MessageHandler, setup_loguru_config
# from backend.protocol_rpc.endpoints import register_all_rpc_endpoints
from backend.protocol_rpc.fastapi_rpc_handler import RPCHandler, JSONRPCRequest, JSONRPCResponse
from backend.protocol_rpc.validators_init import initialize_validators
from backend.protocol_rpc.transactions_parser import TransactionParser
from backend.database_handler.transactions_processor import TransactionsProcessor
from backend.database_handler.validators_registry import (
    ValidatorsRegistry,
    ModifiableValidatorsRegistry,
)
from backend.database_handler.llm_providers import LLMProviderRegistry
from backend.database_handler.accounts_manager import AccountsManager
from backend.database_handler.snapshot_manager import SnapshotManager
from backend.database_handler.session_manager import managed_session
from backend.consensus.base import ConsensusAlgorithm
from backend.database_handler.models import Base, TransactionStatus
from backend.rollup.consensus_service import ConsensusService
from backend.protocol_rpc.aio import MAIN_SERVER_LOOP, MAIN_LOOP_EXITING, MAIN_LOOP_DONE
from backend.domain.types import TransactionType
import backend.validators as validators
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Set up logging
setup_loguru_config()

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.room_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        # Remove from all rooms
        for room in self.room_connections.values():
            if websocket in room:
                room.remove(websocket)

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)
    
    async def join_room(self, room: str, websocket: WebSocket):
        if room not in self.room_connections:
            self.room_connections[room] = []
        if websocket not in self.room_connections[room]:
            self.room_connections[room].append(websocket)
    
    async def leave_room(self, room: str, websocket: WebSocket):
        if room in self.room_connections and websocket in self.room_connections[room]:
            self.room_connections[room].remove(websocket)
    
    async def emit_to_room(self, room: str, event: str, data: Any):
        """Emit an event to all connections in a room."""
        if room in self.room_connections:
            message = json.dumps({"event": event, "data": data})
            for connection in self.room_connections[room]:
                try:
                    await connection.send_text(message)
                except:
                    # Connection might be closed
                    pass

# Global instances
manager = ConnectionManager()
app_state = {}

def get_db_name(database: str) -> str:
    return "genlayer_state" if database == "genlayer" else database

# Database setup
# Prefer explicit environment variables for DB configuration to match migrations and compose
db_user = os.environ.get("DBUSER", "postgres")
db_password = os.environ.get("DBPASSWORD", "postgres")
db_host = os.environ.get("DBHOST", "localhost")
db_port = os.environ.get("DBPORT", "5432")
db_name = os.environ.get("DBNAME") or get_db_name("genlayer")

db_uri = (
    f"postgresql+psycopg2://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
)

# Create sync engine for existing code
engine = create_engine(
    db_uri,
    pool_size=int(os.environ.get("DATABASE_POOL_SIZE", 20)),
    max_overflow=int(os.environ.get("DATABASE_MAX_OVERFLOW", 10)),
    pool_pre_ping=True,
    pool_recycle=3600,
    pool_timeout=30,
)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)

def get_db():
    """Dependency to get database session."""
    db = SessionLocal()
    try:
        yield db
        db.commit()  # Commit if no exception occurred
    except Exception:
        db.rollback()  # Rollback on any exception
        raise
    finally:
        db.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle."""
    print("Starting up FastAPI application...")
    
    # Initialize database
    Base.metadata.create_all(bind=engine)
    
    # Initialize components
    session = SessionLocal()
    
    try:
        # Store components in app state
        # Use the ConnectionManager for WebSocket support
        app_state['msg_handler'] = MessageHandler(manager, config=GlobalConfiguration())
        app_state['transactions_processor'] = TransactionsProcessor(session)
        app_state['accounts_manager'] = AccountsManager(session)
        app_state['snapshot_manager'] = SnapshotManager(session)
        app_state['llm_provider_registry'] = LLMProviderRegistry(session)
        app_state['llm_provider_registry'].update_defaults()
        app_state['consensus_service'] = ConsensusService()
        app_state['transactions_parser'] = TransactionParser(app_state['consensus_service'])
        
        # Start validators manager first - it will create its own registry
        # Use SessionLocal() to create a new session for validators
        app_state['validators_manager'] = validators.Manager(SessionLocal())
        
        # Initialize validators using the validators manager's registry
        validators_config = os.environ.get("VALIDATORS_CONFIG_JSON")
        if validators_config:
            await initialize_validators(
                validators_config,
                app_state['validators_manager'].registry,
                AccountsManager(session),
            )
        
        await app_state['validators_manager'].restart()
        
        # Use the validators manager's registry for all validator operations
        app_state['validators_registry'] = app_state['validators_manager'].registry
        app_state['modifiable_validators_registry'] = app_state['validators_manager'].registry
        
        # Initialize consensus
        def get_session():
            return SessionLocal()
        
        app_state['consensus'] = ConsensusAlgorithm(
            get_session,
            app_state['msg_handler'],
            app_state['consensus_service'],
            app_state['validators_manager'],
        )
        
        # Start consensus background tasks
        import threading
        stop_event = threading.Event()
        app_state['consensus_stop_event'] = stop_event
        
        # Create async tasks for consensus loops
        asyncio.create_task(app_state['consensus'].run_crawl_snapshot_loop(stop_event=stop_event))
        asyncio.create_task(app_state['consensus'].run_process_pending_transactions_loop(stop_event=stop_event))
        asyncio.create_task(app_state['consensus'].run_appeal_window_loop(stop_event=stop_event))
        
        print("Consensus background tasks started")
        
        # Store SQLAlchemy db for dev endpoints (if needed)
        # Since we're using SQLAlchemy directly, we can create a simple wrapper
        class SQLAlchemyDBWrapper:
            @property
            def engine(self):
                return engine
        
        app_state['sqlalchemy_db'] = SQLAlchemyDBWrapper()
        
        # Initialize RPC handler with app_state
        app_state['rpc_handler'] = RPCHandler(app_state)
        
        print("FastAPI application started successfully")
        
        yield
        
        # Cleanup on shutdown
        print("Shutting down FastAPI application...")
        
        # Stop consensus tasks
        if 'consensus_stop_event' in app_state:
            app_state['consensus_stop_event'].set()
            print("Stopping consensus background tasks...")
        
        MAIN_LOOP_EXITING.set()
        await MAIN_LOOP_DONE.wait()
        
    finally:
        session.close()

# Create FastAPI app
app = FastAPI(
    title="GenLayer RPC API",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}

# JSON-RPC endpoint
@app.post("/api", response_model=JSONRPCResponse)
async def jsonrpc_endpoint(request: Request):
    """Main JSON-RPC endpoint."""
    try:
        body = await request.json()

        # Parse request
        rpc_request = JSONRPCRequest(**body)

        # Fast path: healthcheck ping without DB or full initialization
        if rpc_request.method == "ping":
            return JSONRPCResponse(jsonrpc="2.0", result="OK", id=rpc_request.id)

        # For other methods, require handler and DB session
        rpc_handler = app_state.get('rpc_handler')
        if not rpc_handler:
            return JSONRPCResponse(
                jsonrpc="2.0",
                error={"code": -32603, "message": "RPC handler not initialized"},
                id=rpc_request.id,
            )

        db: Session | None = None
        try:
            db = SessionLocal()
            response = await rpc_handler.handle_request(
                rpc_request,
                db,
                app_state,
            )
            # Commit if handler did not raise
            try:
                db.commit()
            except Exception:
                db.rollback()
                raise
            return response
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass

    except json.JSONDecodeError:
        return JSONRPCResponse(
            jsonrpc="2.0",
            error={"code": -32700, "message": "Parse error"},
            id=None,
        )
    except Exception as e:
        return JSONRPCResponse(
            jsonrpc="2.0",
            error={"code": -32603, "message": str(e)},
            id=body.get("id") if 'body' in locals() else None,
        )

# WebSocket endpoint with native WebSocket support
# Use both /socket.io/ and /ws for compatibility
@app.websocket("/socket.io/")
async def websocket_socketio_endpoint(websocket: WebSocket):
    """Socket.IO-compatible WebSocket endpoint."""
    return await websocket_handler(websocket)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Standard WebSocket endpoint."""
    return await websocket_handler(websocket)

async def websocket_handler(websocket: WebSocket):
    """WebSocket handler for real-time communication."""
    await manager.connect(websocket)
    client_id = id(websocket)
    print(f"WebSocket client {client_id} connected")
    
    # Send initial connect confirmation
    await websocket.send_text(json.dumps({
        "event": "connect",
        "data": {"id": str(client_id)}
    }))
    
    try:
        while True:
            # Receive message from client
            data = await websocket.receive_text()
            
            try:
                message = json.loads(data)
                event = message.get("event")
                payload = message.get("data", {})
                
                if event == "subscribe":
                    # Handle room subscriptions
                    topics = payload if isinstance(payload, list) else [payload]
                    for topic in topics:
                        await manager.join_room(topic, websocket)
                        await websocket.send_text(json.dumps({
                            "event": "subscribed",
                            "data": {"room": topic}
                        }))
                        print(f"Client {client_id} joined room: {topic}")
                
                elif event == "unsubscribe":
                    # Handle room unsubscriptions
                    topics = payload if isinstance(payload, list) else [payload]
                    for topic in topics:
                        await manager.leave_room(topic, websocket)
                        await websocket.send_text(json.dumps({
                            "event": "unsubscribed",
                            "data": {"room": topic}
                        }))
                        print(f"Client {client_id} left room: {topic}")
                
                else:
                    # Handle other events
                    await websocket.send_text(json.dumps({
                        "event": "message",
                        "data": f"Received event: {event}"
                    }))
                    
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({
                    "event": "error",
                    "data": "Invalid JSON"
                }))
                
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        print(f"Client {client_id} disconnected")
    except Exception as e:
        print(f"WebSocket error: {e}")
        manager.disconnect(websocket)

# Method to emit events (to be used by other parts of the application)
async def emit_event(room: str, event: str, data: Any):
    """Emit an event to all clients in a room."""
    await manager.emit_to_room(room, event, data)

# Store emit function in app state for other components to use
app_state['emit_event'] = emit_event

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("RPCPORT", "4000")),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
        access_log=True,
    )