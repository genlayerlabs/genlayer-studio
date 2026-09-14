import asyncio
import contextvars
import json
import threading
from unittest.mock import MagicMock

import pytest
from fastapi import Depends, FastAPI
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from backend.protocol_rpc import endpoints
from backend.protocol_rpc.dependencies import get_db_session
from backend.protocol_rpc.exceptions import JSONRPCError, NotFoundError
from backend.protocol_rpc.message_handler.fastapi_handler import MessageHandler
from backend.protocol_rpc.rpc_endpoint_manager import (
    JSONRPCRequest,
    RPCEndpointDefinition,
    RPCEndpointManager,
)


def make_request(app):
    return Request(
        {
            "type": "http",
            "method": "POST",
            "headers": [],
            "app": app,
            "query_string": b"",
            "path": "/api",
            "root_path": "",
            "scheme": "http",
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("with_dependency", [False, True])
@pytest.mark.parametrize("fails", [False, True])
async def test_sync_rpc_runs_off_loop_and_cleans_up_dependencies(
    with_dependency, fails
):
    app = FastAPI()
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    request_context = contextvars.ContextVar("test_request_context")
    token = request_context.set("client-session")
    session = MagicMock()
    app.state.db_manager = MagicMock()
    app.state.db_manager.open_session.return_value = session

    def work():
        assert threading.get_ident() != loop_thread
        assert request_context.get() == "client-session"
        # A callback scheduled by the worker must run while it is still busy.
        callback_ran = threading.Event()
        loop.call_soon_threadsafe(callback_ran.set)
        assert callback_ran.wait(timeout=2)
        if fails:
            raise JSONRPCError(code=123, message="query failed")
        return 42

    def handler_with_dependency(db=Depends(get_db_session)):
        assert db is session
        return work()

    handler = handler_with_dependency if with_dependency else work
    manager = RPCEndpointManager(MagicMock(), dependency_overrides_provider=app)
    manager.register(RPCEndpointDefinition(name="test", handler=handler))
    try:
        response = await manager.invoke(
            JSONRPCRequest(method="test", id=1), make_request(app)
        )
    finally:
        request_context.reset(token)
    if fails:
        assert response.error == {"code": 123, "message": "query failed"}
    else:
        assert response.result == 42
    if with_dependency:
        session.close.assert_called_once()
        if fails:
            session.rollback.assert_called_once()
            session.commit.assert_not_called()
        else:
            session.commit.assert_called_once()
            session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("sync_wrapper", [False, True])
async def test_async_rpc_result_stays_on_application_loop(sync_wrapper):
    app = FastAPI()
    loop = asyncio.get_running_loop()

    async def async_handler():
        assert asyncio.get_running_loop() is loop
        return 42

    def wrapper():
        return async_handler()

    manager = RPCEndpointManager(MagicMock(), dependency_overrides_provider=app)
    manager.register(
        RPCEndpointDefinition(
            name="test", handler=wrapper if sync_wrapper else async_handler
        )
    )
    response = await manager.invoke(
        JSONRPCRequest(method="test", id=1), make_request(app)
    )
    assert response.result == 42


@pytest.mark.asyncio
@pytest.mark.parametrize("clone_in_worker", [False, True])
async def test_worker_notifications_publish_on_owner_loop(clone_in_worker):
    loop = asyncio.get_running_loop()
    published = asyncio.Event()
    messages = []

    async def publish(*, channel, message):
        assert asyncio.get_running_loop() is loop
        messages.append((channel, json.loads(message)))
        published.set()

    broadcast = MagicMock()
    broadcast.publish = publish
    handler = MessageHandler(broadcast, MagicMock())

    def send():
        current = (
            handler.with_client_session("client-1") if clone_in_worker else handler
        )
        current.send_transaction_status_update("0xabc", "CANCELED")

    await run_in_threadpool(send)
    await asyncio.wait_for(published.wait(), timeout=2)
    assert messages[0][0] == "0xabc"
    assert messages[0][1]["event"] == "transaction_status_updated"
    assert messages[0][1]["data"]["data"]["status"] == "CANCELED"


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["schema", "gen_call", "eth_call"])
async def test_contract_snapshot_checkout_is_off_loop(monkeypatch, endpoint):
    loop_thread = threading.get_ident()
    address = "0x" + "ab" * 20
    session = MagicMock()
    observed = []

    def snapshot(contract_address, db_session):
        observed.append(threading.get_ident())
        assert threading.get_ident() != loop_thread
        assert db_session is session
        raise endpoints.ContractNotFoundError(contract_address)

    monkeypatch.setattr(endpoints, "ContractSnapshot", snapshot)
    monkeypatch.setattr(endpoints, "_check_rate_limit", lambda *args: None)
    monkeypatch.setattr(endpoints, "_genvm_admission_semaphore", asyncio.Semaphore(1))
    monkeypatch.setattr(endpoints, "handle_consensus_data_call", lambda *args: None)
    params = {"to": address, "from": address, "type": "read", "data": "0x1234"}
    validator_snapshot = MagicMock()
    validator_snapshot.nodes = [MagicMock()]
    validators = MagicMock()
    validators.snapshot.return_value.__aenter__.return_value = validator_snapshot

    with pytest.raises(NotFoundError):
        if endpoint == "schema":
            await endpoints.get_contract_schema(
                session, MagicMock(), MagicMock(), address
            )
        elif endpoint == "gen_call":
            await endpoints._gen_call_with_validator(
                session,
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                validator_snapshot,
                params,
            )
        else:
            await endpoints.eth_call(
                session,
                MagicMock(),
                MagicMock(),
                MagicMock(),
                validators,
                MagicMock(),
                MagicMock(),
                params,
            )
    assert len(observed) == 1


@pytest.mark.asyncio
async def test_eth_consensus_data_query_is_off_loop(monkeypatch):
    loop_thread = threading.get_ident()

    def intercept(*args):
        assert threading.get_ident() != loop_thread
        return "0x1234"

    monkeypatch.setattr(endpoints, "handle_consensus_data_call", intercept)
    result = await endpoints.eth_call(
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        {"to": "0x" + "ab" * 20, "data": "0x1234"},
    )
    assert result == "0x1234"
