"""Tests for sim_resetStudioState (DXP-725): endpoint gating and worker reset."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.protocol_rpc import endpoints
from backend.protocol_rpc.exceptions import JSONRPCError
from backend.protocol_rpc.message_handler.redis_worker_handler import (
    STUDIO_RESET_EVENT,
    RedisWorkerMessageHandler,
)


def _make_worker():
    from backend.consensus.worker import ConsensusWorker

    return ConsensusWorker(
        get_session=MagicMock(),
        msg_handler=MagicMock(),
        consensus_service=MagicMock(),
        validators_manager=MagicMock(),
        genvm_manager=MagicMock(),
        worker_id="test-worker",
    )


class TestResetStudioStateEndpoint:
    @pytest.mark.asyncio
    async def test_deletes_rows_commits_then_notifies_workers(self, monkeypatch):
        monkeypatch.delenv("VITE_IS_HOSTED", raising=False)
        monkeypatch.delenv("ADMIN_API_KEY", raising=False)
        calls = []
        session = MagicMock()
        session.query.return_value.delete.side_effect = [3, 2]
        session.commit.side_effect = lambda: calls.append("commit")

        async def fake_publish():
            calls.append("publish")
            return 1

        with patch.object(endpoints, "publish_studio_reset_event", fake_publish):
            result = await endpoints.reset_studio_state(session=session)

        assert result == {
            "deleted_transactions": 3,
            "deleted_accounts": 2,
            "workers_notified": 1,
        }
        # Workers must only be told after the rows are gone.
        assert calls == ["commit", "publish"]

    @pytest.mark.asyncio
    async def test_blocked_in_hosted_mode(self, monkeypatch):
        monkeypatch.setenv("VITE_IS_HOSTED", "true")
        monkeypatch.delenv("ADMIN_API_KEY", raising=False)
        session = MagicMock()

        with pytest.raises(JSONRPCError):
            await endpoints.reset_studio_state(session=session)

        session.query.assert_not_called()

    @pytest.mark.asyncio
    async def test_publish_uses_worker_channel(self):
        redis_client = MagicMock()
        redis_client.publish = AsyncMock(return_value=4)
        redis_client.close = AsyncMock()

        with patch("redis.asyncio.from_url", return_value=redis_client):
            assert await endpoints.publish_studio_reset_event() == 4

        channel, message = redis_client.publish.await_args.args
        assert channel == RedisWorkerMessageHandler.VALIDATOR_CHANNEL
        assert STUDIO_RESET_EVENT in message
        redis_client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_publish_failure_is_not_fatal(self):
        with patch("redis.asyncio.from_url", side_effect=ConnectionError("down")):
            assert await endpoints.publish_studio_reset_event() == 0


class TestWorkerResetState:
    @pytest.mark.asyncio
    async def test_cancels_tasks_and_clears_state(self):
        worker = _make_worker()
        task = asyncio.create_task(asyncio.sleep(3600))
        worker._active_tasks.add(task)
        worker.current_transactions["0xabc"] = {"type": "transaction"}
        worker._no_validators_retries["0xabc"] = {"count": 1}
        worker._generic_error_retries["0xabc"] = {"count": 1}
        worker._leader_crash_retries["0xabc"] = {"count": 1}

        cancelled = await worker.reset_state()

        assert cancelled == 1
        assert task.cancelled()
        assert worker._active_tasks == set()
        assert worker.current_transactions == {}
        assert worker._no_validators_retries == {}
        assert worker._generic_error_retries == {}
        assert worker._leader_crash_retries == {}

    @pytest.mark.asyncio
    async def test_no_active_tasks(self):
        worker = _make_worker()
        assert await worker.reset_state() == 0
