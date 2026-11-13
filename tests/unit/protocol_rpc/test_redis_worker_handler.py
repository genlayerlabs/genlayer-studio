from unittest.mock import AsyncMock, Mock

import pytest

from backend.protocol_rpc.message_handler.redis_worker_handler import (
    RedisWorkerMessageHandler,
)
from backend.protocol_rpc.message_handler.types import EventScope, EventType, LogEvent
from backend.protocol_rpc.message_handler.worker_handler import WorkerMessageHandler


def _scope_only_event(scope: EventScope) -> LogEvent:
    return LogEvent(
        name="scope-only",
        type=EventType.INFO,
        scope=scope,
        message="scope-only event",
    )


@pytest.mark.parametrize(
    ("scope", "channel"),
    [
        (EventScope.TRANSACTION, RedisWorkerMessageHandler.TRANSACTION_CHANNEL),
        (EventScope.CONSENSUS, RedisWorkerMessageHandler.CONSENSUS_CHANNEL),
    ],
)
def test_scope_only_events_are_published(scope, channel):
    handler = RedisWorkerMessageHandler.__new__(RedisWorkerMessageHandler)
    handler._log_message = Mock()
    handler._socket_emit = Mock()
    event = _scope_only_event(scope)

    handler.send_message(event)

    handler._socket_emit.assert_called_once_with(event)
    assert handler._get_channel_for_event(event) == channel


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [EventScope.TRANSACTION, EventScope.CONSENSUS])
async def test_scope_only_events_are_published_async(scope):
    handler = RedisWorkerMessageHandler.__new__(RedisWorkerMessageHandler)
    handler._log_message = Mock()
    handler._publish_to_redis = AsyncMock()
    event = _scope_only_event(scope)

    await handler.send_message_async(event)

    handler._publish_to_redis.assert_awaited_once_with(event)


@pytest.mark.parametrize("scope", [EventScope.TRANSACTION, EventScope.CONSENSUS])
def test_scope_only_events_are_forwarded_by_http_worker(scope):
    handler = WorkerMessageHandler.__new__(WorkerMessageHandler)
    handler._log_message = Mock()
    handler._socket_emit = Mock()
    event = _scope_only_event(scope)

    handler.send_message(event)

    handler._socket_emit.assert_called_once_with(event)
