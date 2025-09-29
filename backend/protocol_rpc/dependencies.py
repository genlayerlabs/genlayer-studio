"""FastAPI dependency functions for RPC handlers."""

from __future__ import annotations

from typing import (
    Annotated,
    Any,
    Awaitable,
    Callable,
    Generator,
    Optional,
    TypeVar,
    cast,
)

from fastapi import Depends, HTTPException, Request, status
from starlette.websockets import WebSocket
from sqlalchemy.orm import Session

from backend.database_handler.accounts_manager import AccountsManager
from backend.database_handler.llm_providers import LLMProviderRegistry
from backend.database_handler.snapshot_manager import SnapshotManager
from backend.database_handler.transactions_processor import TransactionsProcessor
from backend.database_handler.session_factory import DatabaseSessionManager
from backend.protocol_rpc.fastapi_rpc_router import FastAPIRPCRouter
from backend.protocol_rpc.message_handler.fastapi_handler import MessageHandler
from backend.protocol_rpc.broadcast import Broadcast

EmitEventCallable = Callable[[str, str, Any], Awaitable[None]]


def _get_app_state(request: Request) -> Any:
    state = getattr(request.app, "state", None)
    if state is None:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Application state is not configured",
        )
    return state


T = TypeVar("T")


def _require_state_attr(state: Any, attr: str, detail: str) -> T:
    value = getattr(state, attr, None)
    if value is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail)
    return cast(T, value)


def _peek_state_attr(state: Any, attr: str) -> Any:
    return getattr(state, attr, None)


def get_db_manager(request: Request) -> DatabaseSessionManager:
    return _require_state_attr(
        _get_app_state(request), "db_manager", "Database manager not initialized"
    )


def get_db_session(
    db_manager: Annotated[DatabaseSessionManager, Depends(get_db_manager)],
) -> Generator[Session, None, None]:
    session = db_manager.open_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_message_handler(request: Request) -> MessageHandler:
    return _require_state_attr(
        _get_app_state(request), "msg_handler", "Message handler not initialized"
    )


def get_broadcast(request: Request) -> Broadcast:
    return _require_state_attr(
        _get_app_state(request), "broadcast", "Broadcast channel not initialized"
    )


def get_broadcast_optional(request: Request) -> Optional[Broadcast]:
    return _peek_state_attr(_get_app_state(request), "broadcast")


async def websocket_broadcast(websocket: WebSocket) -> Broadcast:
    state = getattr(websocket.app, "state", None)
    broadcast = _peek_state_attr(state, "broadcast") if state else None
    if broadcast is None:
        await websocket.close(code=1011)
        raise RuntimeError("Broadcast channel not initialized")
    return broadcast


def get_emit_event(request: Request) -> EmitEventCallable:
    return _require_state_attr(
        _get_app_state(request), "emit_event", "Emit helper not initialized"
    )


def get_emit_event_optional(request: Request) -> Optional[EmitEventCallable]:
    return _peek_state_attr(_get_app_state(request), "emit_event")


def get_rpc_router(request: Request) -> FastAPIRPCRouter:
    return _require_state_attr(
        _get_app_state(request), "rpc_router", "RPC router not initialized"
    )


def get_rpc_router_optional(request: Request) -> Optional[FastAPIRPCRouter]:
    return _peek_state_attr(_get_app_state(request), "rpc_router")


def get_validators_manager(request: Request):
    return _require_state_attr(
        _get_app_state(request),
        "validators_manager",
        "Validators manager not initialized",
    )


def get_validators_registry(request: Request):
    state = _get_app_state(request)
    manager = _peek_state_attr(state, "validators_manager")
    if manager is not None:
        return manager.registry
    return _require_state_attr(
        state, "validators_registry", "Validators registry not initialized"
    )


def get_consensus(request: Request):
    return _require_state_attr(
        _get_app_state(request), "consensus", "Consensus engine not initialized"
    )


def get_consensus_service(request: Request):
    return _require_state_attr(
        _get_app_state(request),
        "consensus_service",
        "Consensus service not initialized",
    )


def get_transactions_parser(request: Request):
    return _require_state_attr(
        _get_app_state(request),
        "transactions_parser",
        "Transactions parser not initialized",
    )


def get_sqlalchemy_db(request: Request):
    return _require_state_attr(
        _get_app_state(request), "sqlalchemy_db", "SQLAlchemy DB not initialized"
    )


def get_accounts_manager(
    session: Annotated[Session, Depends(get_db_session)],
) -> AccountsManager:
    return AccountsManager(session)


def get_transactions_processor(
    session: Annotated[Session, Depends(get_db_session)],
) -> TransactionsProcessor:
    return TransactionsProcessor(session)


def get_snapshot_manager(
    session: Annotated[Session, Depends(get_db_session)],
) -> SnapshotManager:
    return SnapshotManager(session)


def get_llm_provider_registry(
    session: Annotated[Session, Depends(get_db_session)],
) -> LLMProviderRegistry:
    return LLMProviderRegistry(session)
