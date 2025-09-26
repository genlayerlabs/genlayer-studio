"""Minimal in-process broadcast utility used for WebSocket fan-out."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Dict, Iterable


@dataclass(slots=True)
class _BroadcastMessage:
    message: str


class _BroadcastSubscriber:
    """Async iterator over messages emitted on a channel."""

    def __init__(self, queue: asyncio.Queue):
        self._queue = queue

    def __aiter__(self):
        return self

    async def __anext__(self) -> _BroadcastMessage:
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item


class _BroadcastSubscription(AbstractAsyncContextManager[_BroadcastSubscriber]):
    def __init__(self, manager: "Broadcast", channel: str):
        self._manager = manager
        self._channel = channel
        self._queue: asyncio.Queue | None = None

    async def __aenter__(self) -> _BroadcastSubscriber:
        self._queue = asyncio.Queue()
        await self._manager._register(self._channel, self._queue)
        return _BroadcastSubscriber(self._queue)

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._queue:
            await self._manager._unregister(self._channel, self._queue)
            self._queue = None


class Broadcast:
    """Lightweight broadcast hub; API-compatible subset of Starlette's Broadcast."""

    def __init__(self, backend: str | None = None) -> None:
        self._backend = backend or "memory://"
        self._channels: Dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._lock = asyncio.Lock()
        self._closed = False

    async def connect(self) -> None:
        self._closed = False

    async def disconnect(self) -> None:
        async with self._lock:
            self._closed = True
            queues: Iterable[asyncio.Queue] = {
                queue
                for subscribers in self._channels.values()
                for queue in subscribers
            }
            self._channels.clear()

        for queue in queues:
            queue.put_nowait(None)

    def subscribe(self, channel: str) -> _BroadcastSubscription:
        return _BroadcastSubscription(self, channel)

    async def publish(self, *, channel: str, message: str) -> None:
        async with self._lock:
            if self._closed:
                return
            subscribers = list(self._channels.get(channel, []))

        for queue in subscribers:
            queue.put_nowait(_BroadcastMessage(message))

    async def _register(self, channel: str, queue: asyncio.Queue) -> None:
        async with self._lock:
            self._channels[channel].add(queue)

    async def _unregister(self, channel: str, queue: asyncio.Queue) -> None:
        async with self._lock:
            subscribers = self._channels.get(channel)
            if subscribers and queue in subscribers:
                subscribers.remove(queue)
                if not subscribers:
                    self._channels.pop(channel, None)
        queue.put_nowait(None)
