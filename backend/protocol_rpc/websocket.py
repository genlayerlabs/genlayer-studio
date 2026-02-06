"""WebSocket utilities backed by Starlette Broadcast channels."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any, Awaitable, Callable, Dict

from fastapi import WebSocket
from fastapi.websockets import WebSocketDisconnect

from backend.protocol_rpc.broadcast import Broadcast

GLOBAL_CHANNEL = "__broadcast__"


async def _forward_messages(websocket: WebSocket, subscriber: Any) -> None:
    """Forward broadcast messages to the websocket client."""
    try:
        async for event in subscriber:
            await websocket.send_text(event.message)
    except asyncio.CancelledError:  # Expected during shutdown/unsubscribe
        raise
    except Exception:
        # Ignore send failures caused by client disconnection.
        # This covers WebSocketDisconnect, RuntimeError, ClientDisconnected (uvicorn),
        # ConnectionClosedError (websockets), IncompleteReadError (asyncio), etc.
        pass


async def websocket_handler(websocket: WebSocket, broadcast: Broadcast) -> None:
    """Primary WebSocket handler supporting subscribe/unsubscribe semantics."""
    await websocket.accept()

    subscriptions: Dict[str, Dict[str, Any]] = {}

    async def subscribe(channel: str) -> None:
        if channel in subscriptions:
            return

        context = broadcast.subscribe(channel=channel)
        subscriber = await context.__aenter__()
        task = asyncio.create_task(_forward_messages(websocket, subscriber))
        subscriptions[channel] = {"context": context, "task": task}

    async def unsubscribe(channel: str) -> None:
        record = subscriptions.pop(channel, None)
        if not record:
            return

        task = record["task"]
        context = record["context"]

        task.cancel()
        with suppress(Exception):
            await context.__aexit__(None, None, None)
        with suppress(asyncio.CancelledError):
            await task

    async def cleanup() -> None:
        for channel in list(subscriptions.keys()):
            await unsubscribe(channel)

    await subscribe(GLOBAL_CHANNEL)
    await websocket.send_json({"event": "connect", "data": {"id": str(id(websocket))}})

    try:
        while True:
            data = await websocket.receive_text()

            try:
                message = json.loads(data)
            except json.JSONDecodeError:
                await websocket.send_json({"event": "error", "data": "Invalid JSON"})
                continue

            event = message.get("event")
            payload = message.get("data", {})

            if event == "subscribe":
                topics: list[str] | None
                if isinstance(payload, str):
                    topics = [payload]
                elif isinstance(payload, list) and all(
                    isinstance(item, str) for item in payload
                ):
                    topics = payload
                else:
                    topics = None

                if topics is None:
                    await websocket.send_json(
                        {
                            "event": "error",
                            "data": "Invalid subscribe payload; expected string or list of strings",
                        }
                    )
                    continue

                for topic in topics:
                    await subscribe(topic)
                    await websocket.send_json(
                        {"event": "subscribed", "data": {"room": topic}}
                    )

            elif event == "unsubscribe":
                topics: list[str] | None
                if isinstance(payload, str):
                    topics = [payload]
                elif isinstance(payload, list) and all(
                    isinstance(item, str) for item in payload
                ):
                    topics = payload
                else:
                    topics = None

                if topics is None:
                    await websocket.send_json(
                        {
                            "event": "error",
                            "data": "Invalid unsubscribe payload; expected string or list of strings",
                        }
                    )
                    continue

                for topic in topics:
                    await unsubscribe(topic)
                    await websocket.send_json(
                        {"event": "unsubscribed", "data": {"room": topic}}
                    )

            elif event == "ping":
                # Respond to ping with pong to keep connection alive
                await websocket.send_json(
                    {"event": "pong", "data": {"timestamp": payload.get("timestamp")}}
                )

            else:
                await websocket.send_json(
                    {"event": "message", "data": f"Received event: {event}"}
                )

    except WebSocketDisconnect:
        pass
    finally:
        await cleanup()


def create_emit_event_function(
    broadcast: Broadcast,
) -> Callable[[str, str, Any], Awaitable[None]]:
    """Expose a coroutine for publishing events to a broadcast channel."""

    async def emit_event(room: str, event: str, data: Any) -> None:
        payload = json.dumps({"event": event, "data": data})
        await broadcast.publish(channel=room, message=payload)

    return emit_event
