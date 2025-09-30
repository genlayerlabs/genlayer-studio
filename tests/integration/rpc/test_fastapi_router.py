import json
from typing import Any

import pytest

from fastapi import FastAPI
from starlette.requests import Request
from backend.protocol_rpc.exceptions import JSONRPCError
from backend.protocol_rpc.fastapi_rpc_router import FastAPIRPCRouter
from backend.protocol_rpc.rpc_endpoint_manager import (
    RPCEndpointDefinition,
    RPCEndpointManager,
)


def make_request(app: FastAPI, payload: Any) -> Request:
    body = json.dumps(payload).encode()
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api",
        "headers": [(b"content-type", b"application/json")],
        "app": app,
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "server": ("localhost", 4000),
    }
    return Request(scope, receive)


class StubMessageHandler:
    def __init__(self) -> None:
        self.messages: list[object] = []

    def send_message(self, event: object) -> None:
        self.messages.append(event)


@pytest.fixture
def router_setup():
    app = FastAPI()
    stub_logger = StubMessageHandler()

    manager = RPCEndpointManager(
        logger=stub_logger,
        dependency_overrides_provider=app,
    )
    router = FastAPIRPCRouter(endpoint_manager=manager)

    return router, manager, app


@pytest.mark.asyncio
async def test_router_dispatches_single_call_success(router_setup):
    router, manager, app = router_setup

    def add(x: int, y: int) -> int:
        return x + y

    manager.register(RPCEndpointDefinition(name="sum", handler=add))

    request = make_request(
        app, {"jsonrpc": "2.0", "method": "sum", "params": [2, 3], "id": 1}
    )
    response = await router.handle_http_request(request)
    data = json.loads(response.body.decode())

    assert data == {"jsonrpc": "2.0", "result": 5, "id": 1}


@pytest.mark.asyncio
async def test_router_returns_jsonrpc_error(router_setup):
    router, manager, app = router_setup

    async def explode() -> None:
        raise JSONRPCError(code=123, message="failure")

    manager.register(RPCEndpointDefinition(name="explode", handler=explode))

    request = make_request(app, {"jsonrpc": "2.0", "method": "explode", "id": 2})
    response = await router.handle_http_request(request)
    data = json.loads(response.body.decode())

    assert data["error"] == {"code": 123, "message": "failure"}


@pytest.mark.asyncio
async def test_router_reports_missing_method(router_setup):
    router, _manager, app = router_setup

    request = make_request(app, {"jsonrpc": "2.0", "method": "missing", "id": 3})
    response = await router.handle_http_request(request)
    data = json.loads(response.body.decode())

    assert data["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_router_handles_batch_requests(router_setup):
    router, manager, app = router_setup

    def identity(value: int) -> int:
        return value

    manager.register(RPCEndpointDefinition(name="identity", handler=identity))

    payload = [
        {"jsonrpc": "2.0", "method": "identity", "params": [1], "id": "a"},
        {"jsonrpc": "2.0", "method": "identity", "params": [2], "id": "b"},
    ]

    response = await router.handle_http_request(make_request(app, payload))
    data = json.loads(response.body.decode())

    assert data == [
        {"jsonrpc": "2.0", "result": 1, "id": "a"},
        {"jsonrpc": "2.0", "result": 2, "id": "b"},
    ]


@pytest.mark.asyncio
async def test_router_returns_no_content_for_notification(router_setup):
    router, _manager, app = router_setup

    payload = {"jsonrpc": "2.0", "method": "notify", "params": ["ping"]}

    response = await router.handle_http_request(make_request(app, payload))

    assert response.status_code == 204
    assert response.body == b""
