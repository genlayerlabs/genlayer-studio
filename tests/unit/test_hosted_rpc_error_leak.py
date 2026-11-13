from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.requests import Request

from backend.database_handler.session_factory import DatabaseSessionManager
from backend.protocol_rpc import endpoints
from backend.protocol_rpc.exceptions import JSONRPCError
from backend.protocol_rpc.rpc_endpoint_manager import (
    JSONRPCRequest,
    RPCEndpointDefinition,
    RPCEndpointManager,
)

SECRET = "0x" + "ab" * 32


class StubMessageHandler:
    def __init__(self) -> None:
        self.messages: list[object] = []

    def send_message(self, event: object) -> None:
        self.messages.append(event)


def _request(app: FastAPI) -> Request:
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
            "server": ("localhost", 4000),
        }
    )


async def _invoke_failing(exc: Exception):
    app = FastAPI()

    async def failing_endpoint():
        raise exc

    manager = RPCEndpointManager(
        StubMessageHandler(), dependency_overrides_provider=app
    )
    manager.register(RPCEndpointDefinition(name="explode", handler=failing_endpoint))
    return await manager.invoke(
        JSONRPCRequest(method="explode", params=None, id=1), _request(app)
    )


def test_create_validator_is_blocked_in_hosted_mode(monkeypatch):
    monkeypatch.setenv("VITE_IS_HOSTED", "true")
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    validators_manager = MagicMock()

    with pytest.raises(JSONRPCError) as exc_info:
        endpoints.create_validator(
            session=MagicMock(),
            validators_manager=validators_manager,
            stake=1,
            provider="openai",
            model="gpt-4o",
        )

    assert exc_info.value.message == "Operation not available in hosted mode"
    validators_manager.registry.create_validator.assert_not_called()


@pytest.mark.asyncio
async def test_database_errors_do_not_reach_rpc_response():
    exc = IntegrityError(
        "INSERT INTO validators (stake, private_key) VALUES (%(stake)s, %(pk)s)",
        {"stake": "0x0000000000000000000000000000000000000001", "pk": SECRET},
        Exception("invalid input syntax for type integer"),
    )

    response = await _invoke_failing(exc)

    assert response.error["message"] == "Internal database error"
    assert SECRET not in str(response.error)
    assert "INSERT" not in str(response.error)


@pytest.mark.asyncio
async def test_other_unexpected_errors_keep_their_message():
    response = await _invoke_failing(RuntimeError("contract not found"))

    assert response.error["message"] == "contract not found"


def test_session_manager_engine_hides_bound_parameters(tmp_path):
    manager = DatabaseSessionManager(f"sqlite:///{tmp_path / 'db.sqlite'}")
    with manager.engine.begin() as conn:
        conn.execute(text("CREATE TABLE v (stake INTEGER NOT NULL, pk TEXT)"))

    with pytest.raises(SQLAlchemyError) as exc_info:
        with manager.engine.begin() as conn:
            conn.execute(
                text("INSERT INTO v (stake, pk) VALUES (NULL, :pk)"), {"pk": SECRET}
            )

    assert SECRET not in str(exc_info.value)
