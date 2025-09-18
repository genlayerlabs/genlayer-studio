# backend/protocol_rpc/fastapi_server.py

import os
import sys
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
from backend.protocol_rpc.message_handler.fastapi_handler import (
    MessageHandler,
    setup_loguru_config,
)

# from backend.protocol_rpc.endpoints import register_all_rpc_endpoints
from backend.protocol_rpc.fastapi_rpc_handler import (
    RPCHandler,
    JSONRPCRequest,
    JSONRPCResponse,
)
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
        print(f"=== ConnectionManager.connect() called ===")
        print(f"WebSocket object: {websocket}")
        print(f"WebSocket type: {type(websocket)}")
        print(f"Current active connections: {len(self.active_connections)}")

        try:
            print(f"=== Calling websocket.accept() ===")
            await websocket.accept()
            print(f"=== websocket.accept() completed successfully ===")

            self.active_connections.append(websocket)
            print(f"=== WebSocket added to connections list ===")
            print(f"New total connections: {len(self.active_connections)}")
        except Exception as e:
            print(f"=== ERROR in ConnectionManager.connect() ===")
            print(f"Error during accept: {e}")
            print(f"Error type: {type(e)}")
            import traceback

            print(f"Full traceback: {traceback.format_exc()}")
            raise

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

db_uri = f"postgresql+psycopg2://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"

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
SessionLocal = sessionmaker(
    autocommit=False, autoflush=False, bind=engine, expire_on_commit=False
)


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
    print("=== FASTAPI APPLICATION STARTUP ===")
    print(f"Python version: {sys.version}")
    print(f"FastAPI running on port: {os.getenv('RPCPORT', '4000')}")
    print(f"Process ID: {os.getpid()}")
    print(f"Working directory: {os.getcwd()}")
    print(f"Python path: {sys.path}")
    print(f"Command line: {sys.argv}")
    print(f"Environment variables:")
    print(f"  UVICORN_WORKER: {os.getenv('UVICORN_WORKER', 'NOT_SET')}")
    print(f"  BACKEND_BUILD_TARGET: {os.getenv('BACKEND_BUILD_TARGET', 'NOT_SET')}")
    print(f"  LOG_LEVEL: {os.getenv('LOG_LEVEL', 'NOT_SET')}")
    print(f"  FLASK_ENV: {os.getenv('FLASK_ENV', 'NOT_SET')}")
    print(f"  FLASK_APP: {os.getenv('FLASK_APP', 'NOT_SET')}")
    print(f"  PYTHONPATH: {os.getenv('PYTHONPATH', 'NOT_SET')}")
    print(f"  WEB_CONCURRENCY: {os.getenv('WEB_CONCURRENCY', 'NOT_SET')}")
    print(f"All environment variables:")
    for key, value in sorted(os.environ.items()):
        print(f"    {key}={value}")
    print("Starting up FastAPI application...")

    # Initialize database
    Base.metadata.create_all(bind=engine)

    # Initialize components
    session = SessionLocal()

    try:
        # Store components in app state
        # Use the ConnectionManager for WebSocket support
        app_state["msg_handler"] = MessageHandler(manager, config=GlobalConfiguration())
        app_state["transactions_processor"] = TransactionsProcessor(session)
        app_state["accounts_manager"] = AccountsManager(session)
        app_state["snapshot_manager"] = SnapshotManager(session)
        app_state["llm_provider_registry"] = LLMProviderRegistry(session)
        app_state["llm_provider_registry"].update_defaults()
        app_state["consensus_service"] = ConsensusService()
        app_state["transactions_parser"] = TransactionParser(
            app_state["consensus_service"]
        )

        # Start validators manager first - it will create its own registry
        # Use SessionLocal() to create a new session for validators
        app_state["validators_manager"] = validators.Manager(SessionLocal())

        # Initialize validators using the validators manager's registry
        validators_config = os.environ.get("VALIDATORS_CONFIG_JSON")
        if validators_config:
            await initialize_validators(
                validators_config,
                app_state["validators_manager"].registry,
                AccountsManager(session),
            )

        await app_state["validators_manager"].restart()

        # Use the validators manager's registry for all validator operations
        app_state["validators_registry"] = app_state["validators_manager"].registry
        app_state["modifiable_validators_registry"] = app_state[
            "validators_manager"
        ].registry

        # Initialize consensus
        def get_session():
            return SessionLocal()

        app_state["consensus"] = ConsensusAlgorithm(
            get_session,
            app_state["msg_handler"],
            app_state["consensus_service"],
            app_state["validators_manager"],
        )

        # Start consensus background tasks
        import threading

        stop_event = threading.Event()
        app_state["consensus_stop_event"] = stop_event

        # Create async tasks for consensus loops
        asyncio.create_task(
            app_state["consensus"].run_crawl_snapshot_loop(stop_event=stop_event)
        )
        asyncio.create_task(
            app_state["consensus"].run_process_pending_transactions_loop(
                stop_event=stop_event
            )
        )
        asyncio.create_task(
            app_state["consensus"].run_appeal_window_loop(stop_event=stop_event)
        )

        print("Consensus background tasks started")

        # Store SQLAlchemy db for dev endpoints (if needed)
        # Since we're using SQLAlchemy directly, we can create a simple wrapper
        class SQLAlchemyDBWrapper:
            @property
            def engine(self):
                return engine

        app_state["sqlalchemy_db"] = SQLAlchemyDBWrapper()

        # Initialize RPC handler with app_state
        app_state["rpc_handler"] = RPCHandler(app_state)

        print("=== FASTAPI APPLICATION STARTED SUCCESSFULLY ===")

        # Debug: Print all registered routes
        print("=== REGISTERED ROUTES ===")
        for route in app.routes:
            if hasattr(route, "path"):
                route_type = (
                    "WebSocket"
                    if "WebSocket" in str(type(route))
                    else str(getattr(route, "methods", "Unknown"))
                )
                print(f"  {route_type}: {route.path}")

        # Test if WebSocket endpoints are callable
        print("=== TESTING WEBSOCKET ENDPOINT REGISTRATION ===")
        try:
            import inspect

            print(f"websocket_endpoint function: {websocket_endpoint}")
            print(
                f"websocket_socketio_endpoint function: {websocket_socketio_endpoint}"
            )
            print(f"websocket_handler function: {websocket_handler}")
        except Exception as e:
            print(f"Error checking WebSocket functions: {e}")

        # Check if the server is using the correct ASGI application
        print("=== SERVER CONFIGURATION ===")
        print(f"FastAPI app instance: {app}")
        print(f"App state keys: {list(app_state.keys())}")
        print(f"ConnectionManager instance: {manager}")
        print(f"Active connections: {len(manager.active_connections)}")

        yield

        # Cleanup on shutdown
        print("Shutting down FastAPI application...")

        # Stop consensus tasks
        if "consensus_stop_event" in app_state:
            app_state["consensus_stop_event"].set()
            print("Stopping consensus background tasks...")

        MAIN_LOOP_EXITING.set_result(True)
        await asyncio.wrap_future(MAIN_LOOP_DONE)

    finally:
        session.close()


# Create FastAPI app
app = FastAPI(title="GenLayer RPC API", version="1.0.0", lifespan=lifespan)

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


# Debug middleware to catch all requests
@app.middleware("http")
async def debug_requests(request: Request, call_next):
    """Debug middleware to log all incoming requests for production debugging."""
    url_path = str(request.url.path)

    # Log Socket.IO related requests
    if "socket.io" in url_path:
        print(f"=== SOCKET.IO REQUEST DETECTED ===")
        print(f"Method: {request.method}")
        print(f"Path: {url_path}")
        print(f"Query params: {request.url.query}")
        print(f"Client: {request.client}")
        print(f"Headers: {dict(request.headers)}")

    # Log WebSocket upgrade requests
    if request.headers.get("upgrade", "").lower() == "websocket":
        print(f"=== WEBSOCKET UPGRADE REQUEST ===")
        print(f"Path: {url_path}")
        print(f"Client: {request.client}")
        print(f"Headers: {dict(request.headers)}")

    # Log ALL /ws requests (both HTTP and WebSocket)
    if url_path == "/ws" or url_path.startswith("/ws/"):
        print(f"=== /WS PATH REQUEST ===")
        print(f"Method: {request.method}")
        print(f"Path: {url_path}")
        print(f"Client: {request.client}")
        print(f"Upgrade header: {request.headers.get('upgrade', 'NONE')}")
        print(f"Connection header: {request.headers.get('connection', 'NONE')}")
        print(f"All headers: {dict(request.headers)}")

    # Log requests from frontend IPs specifically
    client_host = str(request.client.host) if request.client else "UNKNOWN"
    if client_host.startswith("172.18.0") or "frontend" in client_host:
        print(f"=== FRONTEND REQUEST ===")
        print(f"Method: {request.method}, Path: {url_path}")
        print(f"Client: {request.client}")

    response = await call_next(request)

    # Log response details for WebSocket-related requests
    if "socket.io" in url_path or "/ws" in url_path:
        print(f"=== RESPONSE FOR {url_path} ===")
        print(f"Status: {response.status_code}")
        print(f"Headers: {dict(response.headers)}")

    return response


# JSON-RPC endpoint (supports single and batch requests)
@app.post("/api")
async def jsonrpc_endpoint(request: Request):
    """Main JSON-RPC endpoint with JSON-RPC 2.0 batch support."""
    try:
        body = await request.json()

        rpc_handler = app_state.get("rpc_handler")

        async def handle_one(payload: Dict[str, Any]) -> JSONRPCResponse:
            # Parse request
            rpc_request = JSONRPCRequest(**payload)

            # Fast path: healthcheck ping without DB or full initialization
            if rpc_request.method == "ping":
                return JSONRPCResponse(jsonrpc="2.0", result="OK", id=rpc_request.id)

            # For other methods, require handler and DB session
            if not rpc_handler:
                return JSONRPCResponse(
                    jsonrpc="2.0",
                    error={
                        "code": -32603,
                        "message": "RPC handler not initialized",
                    },
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

        # Handle batch requests
        if isinstance(body, list):
            if len(body) == 0:
                invalid = JSONRPCResponse(
                    jsonrpc="2.0",
                    error={"code": -32600, "message": "Invalid Request"},
                    id=None,
                )
                return JSONResponse(content=[invalid.model_dump(exclude_none=True)])

            responses: List[Dict[str, Any]] = []
            for item in body:
                if not isinstance(item, dict):
                    resp = JSONRPCResponse(
                        jsonrpc="2.0",
                        error={"code": -32600, "message": "Invalid Request"},
                        id=None,
                    )
                else:
                    try:
                        resp = await handle_one(item)
                    except json.JSONDecodeError:
                        resp = JSONRPCResponse(
                            jsonrpc="2.0",
                            error={"code": -32700, "message": "Parse error"},
                            id=item.get("id") if isinstance(item, dict) else None,
                        )
                    except Exception as e:
                        resp = JSONRPCResponse(
                            jsonrpc="2.0",
                            error={"code": -32603, "message": str(e)},
                            id=item.get("id") if isinstance(item, dict) else None,
                        )
                responses.append(resp.model_dump(exclude_none=True))
            return JSONResponse(content=responses)

        # Handle single request object
        if isinstance(body, dict):
            resp = await handle_one(body)
            return JSONResponse(content=resp.model_dump(exclude_none=True))

        # Invalid top-level type
        response = JSONRPCResponse(
            jsonrpc="2.0",
            error={"code": -32600, "message": "Invalid Request"},
            id=None,
        )
        return JSONResponse(content=response.model_dump(exclude_none=True))

    except json.JSONDecodeError:
        response = JSONRPCResponse(
            jsonrpc="2.0", error={"code": -32700, "message": "Parse error"}, id=None
        )
        return JSONResponse(content=response.model_dump(exclude_none=True))
    except Exception as e:
        response = JSONRPCResponse(
            jsonrpc="2.0",
            error={"code": -32603, "message": str(e)},
            id=None,
        )
        return JSONResponse(content=response.model_dump(exclude_none=True))


# WebSocket endpoint with native WebSocket support
# Use both /socket.io/ and /ws for compatibility
@app.websocket("/socket.io/")
async def websocket_socketio_endpoint(websocket: WebSocket):
    """Socket.IO-compatible WebSocket endpoint."""
    print(f"=== WEBSOCKET /socket.io/ ENDPOINT HIT ===")
    print(f"Client: {websocket.client}")
    print(f"Headers: {dict(websocket.headers)}")
    return await websocket_handler(websocket)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Standard WebSocket endpoint."""
    print(f"=== WEBSOCKET /ws ENDPOINT HIT ===")
    print(f"Client: {websocket.client}")
    print(f"Headers: {dict(websocket.headers)}")
    return await websocket_handler(websocket)


async def websocket_handler(websocket: WebSocket):
    """WebSocket handler for real-time communication."""
    client_id = id(websocket)
    print(f"=== WEBSOCKET HANDLER CALLED ===")
    print(f"Client ID: {client_id}")
    print(f"WebSocket state: {websocket.client_state}")
    print(f"WebSocket scope: {websocket.scope}")

    try:
        print(f"=== ATTEMPTING TO ACCEPT WEBSOCKET ===")
        await manager.connect(websocket)
        print(f"=== WEBSOCKET CLIENT {client_id} CONNECTED SUCCESSFULLY ===")
        print(f"Manager total connections: {len(manager.active_connections)}")
    except Exception as e:
        print(f"=== WEBSOCKET CONNECTION FAILED ===")
        print(f"Error type: {type(e)}")
        print(f"Error message: {e}")
        print(f"WebSocket state after error: {websocket.client_state}")
        import traceback

        print(f"Full traceback: {traceback.format_exc()}")
        raise

    # Send initial connect confirmation
    await websocket.send_text(
        json.dumps({"event": "connect", "data": {"id": str(client_id)}})
    )

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
                        await websocket.send_text(
                            json.dumps({"event": "subscribed", "data": {"room": topic}})
                        )
                        print(f"Client {client_id} joined room: {topic}")

                elif event == "unsubscribe":
                    # Handle room unsubscriptions
                    topics = payload if isinstance(payload, list) else [payload]
                    for topic in topics:
                        await manager.leave_room(topic, websocket)
                        await websocket.send_text(
                            json.dumps(
                                {"event": "unsubscribed", "data": {"room": topic}}
                            )
                        )
                        print(f"Client {client_id} left room: {topic}")

                else:
                    # Handle other events
                    await websocket.send_text(
                        json.dumps(
                            {"event": "message", "data": f"Received event: {event}"}
                        )
                    )

            except json.JSONDecodeError:
                await websocket.send_text(
                    json.dumps({"event": "error", "data": "Invalid JSON"})
                )

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
app_state["emit_event"] = emit_event

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("RPCPORT", "4000")),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
        access_log=True,
    )
