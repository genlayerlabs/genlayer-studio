import json

import anyio
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from backend.protocol_rpc.broadcast import Broadcast
from backend.protocol_rpc.websocket import (
    GLOBAL_CHANNEL,
    create_emit_event_function,
    websocket_handler,
)


class TestApplication:
    """Helper wrapper to expose shared broadcast channel."""

    def __init__(self) -> None:
        self.app = FastAPI()
        self.broadcast = Broadcast("memory://")
        self.emit_event = create_emit_event_function(self.broadcast)

        @self.app.on_event("startup")
        async def startup() -> None:  # pragma: no cover - exercised via TestClient
            await self.broadcast.connect()
            self.app.state.broadcast = self.broadcast
            self.app.state.emit_event = self.emit_event

        @self.app.on_event("shutdown")
        async def shutdown() -> None:  # pragma: no cover - exercised via TestClient
            await self.broadcast.disconnect()

        @self.app.websocket("/ws")
        async def ws_endpoint(
            websocket: WebSocket,
        ) -> None:  # pragma: no cover - exercised by tests
            await websocket_handler(websocket, self.app.state.broadcast)


def test_websocket_subscription_and_room_emit() -> None:
    test_app = TestApplication()

    with TestClient(test_app.app) as client:
        with client.websocket_connect("/ws") as websocket:
            initial = websocket.receive_json()
            assert initial["event"] == "connect"

            websocket.send_json({"event": "subscribe", "data": "tx-123"})
            subscribed = websocket.receive_json()
            assert subscribed == {"event": "subscribed", "data": {"room": "tx-123"}}

            anyio.from_thread.run(
                test_app.emit_event, "tx-123", "update", {"status": "ready"}
            )

            delivered = websocket.receive_json()
            assert delivered == {"event": "update", "data": {"status": "ready"}}


def test_websocket_broadcast_reaches_all_clients() -> None:
    test_app = TestApplication()

    with TestClient(test_app.app) as client:
        with client.websocket_connect("/ws") as first:
            first.receive_json()

            with client.websocket_connect("/ws") as second:
                second.receive_json()

                payload = {"event": "sync", "data": {"height": 7}}
                anyio.from_thread.run(
                    test_app.broadcast.publish,
                    channel=GLOBAL_CHANNEL,
                    message=json.dumps(payload),
                )

                assert first.receive_json() == payload
                assert second.receive_json() == payload
