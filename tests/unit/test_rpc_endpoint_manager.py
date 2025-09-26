import pytest
from fastapi import Depends, FastAPI
from starlette.requests import Request

from backend.protocol_rpc.exceptions import JSONRPCError, MethodNotFound
from backend.protocol_rpc.rpc_endpoint_manager import (
    JSONRPCRequest,
    RPCEndpointDefinition,
    RPCEndpointManager,
)


class StubMessageHandler:
    def __init__(self):
        self.messages = []

    def send_message(self, event):
        self.messages.append(event)


@pytest.mark.asyncio
async def test_manager_invokes_endpoint_with_dependencies():
    handler_calls = {}
    stub_logger = StubMessageHandler()
    app = FastAPI()

    def provide_session() -> str:
        return "db-session"

    async def endpoint(value: int, session: str = Depends(provide_session)):
        handler_calls["value"] = value
        handler_calls["session"] = session
        return value + 1

    manager = RPCEndpointManager(stub_logger, dependency_overrides_provider=app)
    manager.register(RPCEndpointDefinition(name="sum_plus_one", handler=endpoint))

    request_payload = JSONRPCRequest(method="sum_plus_one", params=[41], id=1)
    request = Request(
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

    response = await manager.invoke(request_payload, request)

    assert response.result == 42
    assert handler_calls == {"value": 41, "session": "db-session"}
    assert [event.name for event in stub_logger.messages] == [
        "endpoint_call",
        "endpoint_success",
    ]


@pytest.mark.asyncio
async def test_manager_returns_jsonrpc_error_response():
    stub_logger = StubMessageHandler()
    app = FastAPI()

    async def failing_endpoint():
        raise JSONRPCError(code=123, message="boom")

    manager = RPCEndpointManager(stub_logger, dependency_overrides_provider=app)
    manager.register(RPCEndpointDefinition(name="explode", handler=failing_endpoint))

    request_payload = JSONRPCRequest(method="explode", params=None, id=7)
    request = Request(
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

    response = await manager.invoke(request_payload, request)

    assert response.error == {"code": 123, "message": "boom"}
    assert stub_logger.messages[-1].name == "endpoint_error"


@pytest.mark.asyncio
async def test_manager_raises_method_not_found_for_unknown_method():
    stub_logger = StubMessageHandler()
    app = FastAPI()
    manager = RPCEndpointManager(stub_logger, dependency_overrides_provider=app)

    request_payload = JSONRPCRequest(method="unknown", params=None, id=9)
    request = Request(
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

    with pytest.raises(MethodNotFound):
        await manager.invoke(request_payload, request)


@pytest.mark.asyncio
async def test_manager_returns_invalid_params_when_arguments_missing():
    stub_logger = StubMessageHandler()
    app = FastAPI()

    async def requires_value(value: int):
        return value

    manager = RPCEndpointManager(stub_logger, dependency_overrides_provider=app)
    manager.register(RPCEndpointDefinition(name="needs_value", handler=requires_value))

    request_payload = JSONRPCRequest(method="needs_value", params=None, id=11)
    request = Request(
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

    response = await manager.invoke(request_payload, request)

    assert response.error["code"] == -32602
    assert "missing" in response.error["message"].lower()
