import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.protocol_rpc.fastapi_rpc_router import FastAPIRPCRouter
from backend.protocol_rpc.health import create_readiness_check_with_state
from backend.protocol_rpc.rpc_endpoint_manager import (
    RPCEndpointDefinition,
    RPCEndpointManager,
)


class StubMessageHandler:
    """Minimal message handler for capturing log events during tests."""

    def __init__(self) -> None:
        self.events: list[object] = []

    def send_message(
        self, event: object
    ) -> None:  # pragma: no cover - inspected in assertions when needed
        self.events.append(event)


@pytest.fixture
def jsonrpc_test_app() -> tuple[FastAPI, RPCEndpointManager]:
    app = FastAPI()
    stub_logger = StubMessageHandler()
    manager = RPCEndpointManager(
        logger=stub_logger,
        dependency_overrides_provider=app,
    )
    router = FastAPIRPCRouter(endpoint_manager=manager)
    app.state.rpc_router = router

    @app.post("/api")
    async def jsonrpc_endpoint(
        request: Request,
    ):  # pragma: no cover - exercised by tests
        return await router.handle_http_request(request)

    return app, manager


def test_jsonrpc_batch_request(jsonrpc_test_app):
    app, manager = jsonrpc_test_app

    def double(value: int) -> int:
        return value * 2

    manager.register(RPCEndpointDefinition(name="double", handler=double))

    with TestClient(app) as client:
        payload = [
            {"jsonrpc": "2.0", "method": "double", "params": [21], "id": "one"},
            {"jsonrpc": "2.0", "method": "double", "params": [1], "id": "two"},
        ]
        response = client.post("/api", json=payload)

    assert response.status_code == 200
    assert response.json() == [
        {"jsonrpc": "2.0", "result": 42, "id": "one"},
        {"jsonrpc": "2.0", "result": 2, "id": "two"},
    ]


def test_jsonrpc_batch_limit_exceeded(jsonrpc_test_app):
    app, _ = jsonrpc_test_app

    oversized = [{"jsonrpc": "2.0", "method": "echo", "id": str(i)} for i in range(101)]

    with TestClient(app) as client:
        response = client.post("/api", json=oversized)

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == -32600
    assert body["error"]["data"]["size"] == len(oversized)


def test_jsonrpc_empty_batch_is_invalid(jsonrpc_test_app):
    app, _ = jsonrpc_test_app

    with TestClient(app) as client:
        response = client.post("/api", json=[])

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == -32600
    assert body["id"] is None


def test_jsonrpc_malformed_payload_returns_parse_error(jsonrpc_test_app):
    app, _ = jsonrpc_test_app

    with TestClient(app) as client:
        response = client.post(
            "/api",
            data="{",
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 400
    data = response.json()
    assert data["error"]["code"] == -32700
    assert data["id"] is None


@pytest.mark.asyncio
async def test_create_readiness_check_handles_router_instance(jsonrpc_test_app):
    app, _ = jsonrpc_test_app
    readiness_func = create_readiness_check_with_state(app.state.rpc_router)
    payload = await readiness_func()

    assert payload["rpc_router_initialized"] is True
    assert payload["status"] == "ready"


@pytest.mark.asyncio
async def test_create_readiness_check_handles_missing_router():
    readiness_func = create_readiness_check_with_state(None)
    payload = await readiness_func()

    assert payload["rpc_router_initialized"] is False
    assert payload["status"] == "not_ready"
