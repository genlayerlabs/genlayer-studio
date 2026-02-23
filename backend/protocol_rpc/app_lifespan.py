"""Reusable application services setup for the RPC FastAPI stack."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from backend.consensus.base import ConsensusAlgorithm
from backend.database_handler.llm_providers import LLMProviderRegistry
from backend.database_handler.session_factory import (
    DatabaseSessionManager,
    set_database_manager,
)
from backend.protocol_rpc.transactions_parser import TransactionParser
from backend.protocol_rpc.configuration import GlobalConfiguration
from backend.protocol_rpc.fastapi_rpc_router import FastAPIRPCRouter
from backend.protocol_rpc.message_handler.fastapi_handler import (
    MessageHandler,
    setup_loguru_config,
)
from backend.protocol_rpc.rpc_decorators import rpc
from backend.protocol_rpc.rpc_endpoint_manager import RPCEndpointManager
from backend.protocol_rpc.validators_init import initialize_validators
from backend.protocol_rpc.websocket import create_emit_event_function
from backend.protocol_rpc.broadcast import Broadcast
from backend.rollup.consensus_service import ConsensusService
from backend.protocol_rpc.redis_subscriber import RedisEventSubscriber
from backend.protocol_rpc.health import (
    start_background_health_checker,
    stop_background_health_checker,
    record_genvm_execution_success,
    record_genvm_execution_failure,
)
from backend.services.usage_metrics_service import UsageMetricsService
import backend.validators as validators
from backend.node.base import Manager as GenVMManager


@dataclass(frozen=True)
class RPCAppSettings:
    """Runtime settings for the RPC FastAPI application."""

    database_url: str
    validators_config_json: Optional[str] = None

    @classmethod
    def from_environment(cls) -> "RPCAppSettings":
        db_user = os.environ.get("DBUSER", "postgres")
        db_password = os.environ.get("DBPASSWORD", "postgres")
        db_host = os.environ.get("DBHOST", "localhost")
        db_port = os.environ.get("DBPORT", "5432")
        db_name = os.environ.get("DBNAME") or _get_db_name("genlayer")

        database_url = f"postgresql+psycopg2://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
        return cls(
            database_url=database_url,
            validators_config_json=os.environ.get("VALIDATORS_CONFIG_JSON"),
        )


def _get_db_name(database: str) -> str:
    return "genlayer_state" if database == "genlayer" else database


@dataclass
class RPCAppState:
    """Aggregated services initialised for the RPC application."""

    db_manager: DatabaseSessionManager
    broadcast: Broadcast
    msg_handler: MessageHandler
    consensus_service: ConsensusService
    transactions_parser: TransactionParser
    emit_event: Any
    validators_manager: validators.Manager
    validators_registry: Any
    consensus: ConsensusAlgorithm
    consensus_stop_event: threading.Event
    background_tasks: list[asyncio.Task]
    sqlalchemy_db: Any
    rpc_router: FastAPIRPCRouter
    genvm_manager: GenVMManager

    def apply_to_app(self, app) -> None:
        """Populate FastAPI state with the configured services."""

        state = app.state
        state.broadcast = self.broadcast
        state.msg_handler = self.msg_handler
        state.consensus_service = self.consensus_service
        state.transactions_parser = self.transactions_parser
        state.emit_event = self.emit_event
        state.validators_manager = self.validators_manager
        state.validators_registry = self.validators_registry
        state.consensus = self.consensus
        state.consensus_stop_event = self.consensus_stop_event
        state.background_tasks = self.background_tasks
        state.sqlalchemy_db = self.sqlalchemy_db
        state.db_manager = self.db_manager
        state.rpc_router = self.rpc_router
        state.rpc_context = state
        state.genvm_manager = self.genvm_manager


@dataclass
class _RPCAppResources:
    """Internal resources that need explicit cleanup."""

    validators_session: Session
    background_tasks: list[asyncio.Task]
    consensus_stop_event: threading.Event
    broadcast: Broadcast


def _verify_database_ready(db_manager: DatabaseSessionManager) -> None:
    """Ensure the database connection and migrations are ready."""

    with db_manager.engine.connect() as connection:
        connection.execute(text("SELECT 1"))
        try:
            connection.execute(text("SELECT version_num FROM alembic_version"))
        except (ProgrammingError, OperationalError) as exc:  # pragma: no cover
            raise RuntimeError(
                "Database migrations are missing. Run migrations before starting the RPC API."
            ) from exc


async def _initialise_validators(
    validators_config_json: Optional[str],
    db_manager: DatabaseSessionManager,
    validators_manager: validators.Manager,
) -> None:
    if not validators_config_json:
        return

    init_session = db_manager.open_session()
    try:
        await initialize_validators(
            validators_config_json, init_session, validators_manager
        )
        init_session.commit()
    finally:
        init_session.close()


def _seed_llm_providers(db_manager: DatabaseSessionManager) -> None:
    session = db_manager.open_session()
    try:
        LLMProviderRegistry(session).update_defaults()
        session.commit()
    finally:
        session.close()


class _SQLAlchemyDBWrapper:
    def __init__(self, db_manager: DatabaseSessionManager) -> None:
        self._db_manager = db_manager

    @property
    def engine(self):
        return self._db_manager.engine


async def create_genvm_manager() -> GenVMManager:
    genvm_manager = await GenVMManager.create()

    genvm_manager.llm_config_base["backends"] = {}
    genvm_manager.llm_config_base["lua_script_path"] = str(
        Path(__file__).parent.parent.joinpath("node", "llm.lua").resolve()
    )

    webdriver_host = os.getenv("WEBDRIVERHOST", "webdriver")
    webdriver_port = os.getenv("WEBDRIVERPORT", "5001")
    webdriver_protocol = os.getenv("WEBDRIVERPROTOCOL") or os.getenv(
        "RPCPROTOCOL", "http"
    )
    genvm_manager.web_config_base["webdriver_host"] = (
        f"{webdriver_protocol}://{webdriver_host}:{webdriver_port}"
    )
    genvm_manager.web_config_base["lua_script_path"] = str(
        Path(__file__).parent.parent.joinpath("node", "web.lua").resolve()
    )

    error: Exception | None = None
    for _retries in range(5):
        try:
            await genvm_manager.start_module("web", genvm_manager.web_config_base)
        except Exception as e:
            error = e
            await asyncio.sleep(5)
        else:
            error = None
            break
    if error is not None:
        raise error
    await genvm_manager.start_module(
        "llm", genvm_manager.llm_config_base, {"allow_empty_backends": True}
    )

    return genvm_manager


@asynccontextmanager
async def rpc_app_lifespan(app, settings: RPCAppSettings) -> AsyncIterator[RPCAppState]:
    """Prepare RPC services and ensure graceful shutdown."""
    from loguru import logger
    import time

    startup_time = time.time()
    logger.info("[STARTUP] Beginning application startup sequence")

    setup_loguru_config()
    logger.info("[STARTUP] Logging configured")

    logger.info(
        f"[STARTUP] Initializing database connection: {settings.database_url.split('@')[1] if '@' in settings.database_url else 'local'}"
    )
    db_manager = DatabaseSessionManager(settings.database_url)
    set_database_manager(db_manager)

    logger.info("[STARTUP] Verifying database readiness and migrations")
    _verify_database_ready(db_manager)

    logger.info("[STARTUP] Seeding LLM providers")
    _seed_llm_providers(db_manager)

    broadcast_backend = os.environ.get("WEBSOCKET_BROADCAST_BACKEND", "memory://")
    logger.info(f"[STARTUP] Connecting to broadcast backend: {broadcast_backend}")
    broadcast = Broadcast(broadcast_backend)
    await broadcast.connect()

    logger.info("[STARTUP] Initializing message handler and consensus services")
    msg_handler = MessageHandler(broadcast, config=GlobalConfiguration())
    consensus_service = ConsensusService()
    transactions_parser = TransactionParser(consensus_service)
    emit_event = create_emit_event_function(broadcast)

    genvm_manager = await create_genvm_manager()

    # Wire GenVM execution failure tracking callbacks (same pattern as worker_service.py)
    from backend.node.genvm.origin.base_host import set_genvm_callbacks

    set_genvm_callbacks(
        on_success=record_genvm_execution_success,
        on_failure=record_genvm_execution_failure,
    )
    logger.info("[STARTUP] GenVM execution failure tracking callbacks registered")

    validators_session = db_manager.open_session()
    # Manages the validators registry with locking for concurrent access
    # Handles LLM and web modules and configuration file changes
    # Loads GenVM configuration
    logger.info("[STARTUP] Initializing validators manager")
    validators_manager = validators.Manager(validators_session, genvm_manager)

    # Delete all validators from the database and create new ones based on env.VAlIDATORS_CONFIG_JSON
    if settings.validators_config_json:
        logger.info("[STARTUP] Initializing validators from config")
        await _initialise_validators(
            settings.validators_config_json, db_manager, validators_manager
        )

    # Restart web and llm modules, created the validators Snapshot, and registers providers and models to the LLM module
    logger.info("[STARTUP] Restarting validators and creating snapshot")
    await validators_manager.restart()

    validators_registry = validators_manager.registry
    logger.info(
        f"[STARTUP] Validators registry initialized with {len(validators_registry.nodes if hasattr(validators_registry, 'nodes') else [])} validators"
    )

    def get_session() -> Session:
        return db_manager.open_session()

    # Create consensus algorithm instance (for RPC endpoint use, not for background processing)
    # Background consensus loops are handled by separate worker processes
    logger.info("[STARTUP] Creating consensus algorithm (for RPC endpoints only)")
    consensus = ConsensusAlgorithm(
        get_session,
        msg_handler,
        consensus_service,
        validators_manager,
        genvm_manager,
    )

    # No background consensus tasks in RPC process - handled by separate worker services
    stop_event = threading.Event()
    background_tasks = []
    logger.info(
        "[STARTUP] RPC process will not run consensus loops (handled by worker services)"
    )

    sql_db = _SQLAlchemyDBWrapper(db_manager)

    # Registers the RPC methods via decorators, injects dependencies, and orchestrates the invokes with logging for execution
    logger.info("[STARTUP] Setting up RPC endpoint manager")
    endpoint_manager = RPCEndpointManager(
        logger=msg_handler,
        dependency_overrides_provider=app,
    )

    # Import registers RPC methods via decorators (module import has side effects).
    logger.info("[STARTUP] Registering RPC methods")
    from backend.protocol_rpc import rpc_methods

    rpc_method_count = 0
    for definition in rpc.to_list():
        endpoint_manager.register(definition)
        rpc_method_count += 1
    logger.info(f"[STARTUP] Registered {rpc_method_count} RPC methods")

    # Creates the RPC router with the endpoint manager to handle the HTTP requests
    logger.info("[STARTUP] Creating RPC router")
    rpc_router = FastAPIRPCRouter(endpoint_manager=endpoint_manager)

    # Initialize usage metrics service for external API reporting
    logger.info("[STARTUP] Initializing usage metrics service")
    usage_metrics_service = UsageMetricsService()

    # Start background health checker for fast /health endpoint responses
    logger.info("[STARTUP] Starting background health checker")
    start_background_health_checker(rpc_router, usage_metrics_service)

    app_state = RPCAppState(
        db_manager=db_manager,
        broadcast=broadcast,
        msg_handler=msg_handler,
        consensus_service=consensus_service,
        transactions_parser=transactions_parser,
        emit_event=emit_event,
        validators_manager=validators_manager,
        validators_registry=validators_registry,
        consensus=consensus,
        consensus_stop_event=stop_event,
        background_tasks=background_tasks,
        sqlalchemy_db=sql_db,
        rpc_router=rpc_router,
        genvm_manager=genvm_manager,
    )

    resources = _RPCAppResources(
        validators_session=validators_session,
        background_tasks=background_tasks,
        consensus_stop_event=stop_event,
        broadcast=broadcast,
    )

    startup_duration = time.time() - startup_time
    logger.info(
        f"[STARTUP] Application startup completed in {startup_duration:.2f} seconds"
    )

    # Start a periodic status logger if in debug mode
    if os.environ.get("LOG_LEVEL", "INFO").upper() == "DEBUG":
        from backend.consensus.monitoring import periodic_status_logger, get_monitor

        logger.info("[STARTUP] Starting periodic monitoring status logger")
        monitor_task = asyncio.create_task(periodic_status_logger(interval=300))
        resources.background_tasks.append(monitor_task)

    # Initialize Redis subscriber - REQUIRED for distributed worker architecture
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        error_msg = (
            "FATAL: REDIS_URL environment variable is required. "
            "GenLayer Studio uses a distributed worker architecture where consensus workers "
            "publish events to Redis, and RPC instances subscribe to receive them. "
            "Please set REDIS_URL in your environment (e.g., redis://redis:6379/0)."
        )
        logger.error(error_msg)
        raise RuntimeError(error_msg)

    try:
        redis_subscriber = RedisEventSubscriber(
            redis_url=redis_url,
            broadcast=broadcast,
            instance_id=f"rpc-{os.getpid()}",
        )
        await redis_subscriber.connect()
        await redis_subscriber.start()

        # Register handler for validator change events
        async def handle_validator_change(event_data):
            """Reload validators when they change."""
            logger.info(f"RPC worker reloading validators due to change event")
            await validators_manager.restart()

        redis_subscriber.register_handler("validator_created", handle_validator_change)
        redis_subscriber.register_handler("validator_updated", handle_validator_change)
        redis_subscriber.register_handler("validator_deleted", handle_validator_change)
        redis_subscriber.register_handler(
            "all_validators_deleted", handle_validator_change
        )
        redis_subscriber.register_handler(
            "validators_replaced", handle_validator_change
        )

        logger.info(
            f"[STARTUP] Redis subscriber connected at {redis_url} for worker event broadcasting"
        )
    except Exception as e:
        error_msg = (
            f"FATAL: Failed to connect to Redis at {redis_url}. "
            f"Redis is required for receiving events from consensus workers. "
            f"Ensure Redis is running and accessible. Error: {e}"
        )
        logger.error(error_msg)
        raise RuntimeError(error_msg) from e

    try:
        yield app_state

    finally:
        if redis_subscriber:
            await redis_subscriber.stop()
            logger.info("[SHUTDOWN] Redis subscriber stopped")

        # Close usage metrics service
        if usage_metrics_service:
            await usage_metrics_service.close()
            logger.info("[SHUTDOWN] Usage metrics service closed")

        # Stop background health checker
        stop_background_health_checker()
        logger.info("[SHUTDOWN] Background health checker stopped")

        logger.info("[SHUTDOWN] Beginning graceful shutdown sequence")
        shutdown_start = time.time()

        logger.info("[SHUTDOWN] Signaling consensus tasks to stop")
        resources.consensus_stop_event.set()

        logger.info(
            f"[SHUTDOWN] Cancelling {len(resources.background_tasks)} background tasks"
        )
        for task in resources.background_tasks:
            task.cancel()

        logger.info("[SHUTDOWN] Waiting for tasks to complete")
        await asyncio.gather(*resources.background_tasks, return_exceptions=True)

        logger.info("[SHUTDOWN] Closing validators session")
        resources.validators_session.close()

        logger.info("[SHUTDOWN] Disconnecting broadcast backend")
        await resources.broadcast.disconnect()

        await genvm_manager.close()

        # Log final monitoring status if available
        try:
            from backend.consensus.monitoring import get_monitor

            monitor = get_monitor()
            status = monitor.get_status()
            logger.info(
                f"[SHUTDOWN] Final monitoring status - "
                f"Tasks processed: {status.get('active_tasks', 0)}, "
                f"Memory usage: {status.get('memory_usage_mb', 0):.1f}MB"
            )
        except Exception:
            pass

        shutdown_duration = time.time() - shutdown_start
        logger.info(
            f"[SHUTDOWN] Graceful shutdown completed in {shutdown_duration:.2f} seconds"
        )
