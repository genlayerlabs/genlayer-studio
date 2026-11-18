"""Tests for sim_resetStudioState (DXP-725).

Covers the RPC <-> worker handshake: workers must pause admission, cancel and
drain their work and acknowledge before anything is deleted; failures (Redis
down, a delayed worker, a worker unable to stop) must surface as errors and
delete nothing; and work submitted right after a reset must be processed.
"""

import asyncio
import json
import time
from unittest.mock import MagicMock

import pytest

from backend.consensus import worker_service
from backend.protocol_rpc import endpoints
from backend.protocol_rpc.exceptions import JSONRPCError
from backend.protocol_rpc.message_handler.redis_worker_handler import (
    STUDIO_RESET_CHANNEL,
    STUDIO_RESET_FINISHED,
    STUDIO_RESET_REQUEST,
)


def _make_worker():
    from backend.consensus.worker import ConsensusWorker

    worker = ConsensusWorker(
        get_session=MagicMock(),
        msg_handler=MagicMock(),
        consensus_service=MagicMock(),
        validators_manager=MagicMock(),
        genvm_manager=MagicMock(),
        worker_id="test-worker",
    )
    worker.release_transaction = MagicMock()
    return worker


class _Hung:
    """A processing task that blocks until cancelled."""

    def __init__(self, ignore_cancel_for: float = 0.0):
        self.started = asyncio.Event()
        self.cancelled = False
        self.ignore_cancel_for = ignore_cancel_for

    async def run(self):
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            if self.ignore_cancel_for:
                # Simulates an execution tree that is slow to tear down.
                await asyncio.shield(asyncio.sleep(self.ignore_cancel_for))
            raise


class FakeRedis:
    """In-memory stand-in for the RPC's Redis client.

    Each entry in `workers` is a (worker, msg_handler) pair. PUBLISH on the
    reset channel delivers to them through worker_service's real handler,
    and their acks land in the list that BLPOP reads from. `deliver=False`
    simulates a worker that is subscribed but never answers in time.
    """

    def __init__(self, workers=(), deliver=True, publish_error=None, extra=0):
        self.workers = list(workers)
        self.deliver = deliver
        self.publish_error = publish_error
        self.extra_subscribers = extra
        self.lists: dict[str, asyncio.Queue] = {}
        self.published: list[dict] = []
        self.deleted: list[str] = []
        self.closed = False
        self._tasks: list[asyncio.Task] = []

    def _queue(self, key):
        return self.lists.setdefault(key, asyncio.Queue())

    def handler_for(self, worker_id):
        redis = self

        class _Handler:
            def __init__(self):
                self.worker_id = worker_id

            async def ack_studio_reset(self, ack_key, ack):
                await redis._queue(ack_key).put(json.dumps(ack))

        return _Handler()

    async def publish(self, channel, message):
        if self.publish_error:
            raise self.publish_error
        event = json.loads(message)
        self.published.append(event)
        if channel == STUDIO_RESET_CHANNEL and self.deliver:
            for worker, handler in self.workers:
                self._tasks.append(
                    asyncio.create_task(
                        worker_service.handle_studio_reset_event(event, worker, handler)
                    )
                )
        return len(self.workers) + self.extra_subscribers

    async def blpop(self, keys, timeout):
        try:
            value = await asyncio.wait_for(self._queue(keys[0]).get(), timeout)
        except asyncio.TimeoutError:
            return None
        return keys[0], value

    async def delete(self, key):
        self.deleted.append(key)

    async def close(self):
        self.closed = True
        for task in self._tasks:
            await task


@pytest.fixture(autouse=True)
def _local_mode(monkeypatch):
    monkeypatch.delenv("VITE_IS_HOSTED", raising=False)
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    monkeypatch.setattr(endpoints, "STUDIO_RESET_ACK_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(worker_service, "STUDIO_RESET_CANCEL_TIMEOUT_SECONDS", 0.2)


def _session(calls: list):
    session = MagicMock()
    counts = iter([3, 2])

    def _delete(**_kwargs):
        calls.append("delete")
        return next(counts)

    session.query.return_value.delete.side_effect = _delete
    session.commit.side_effect = lambda: calls.append("commit")
    return session


def _events(redis: FakeRedis, name: str) -> list[dict]:
    return [e for e in redis.published if e["event"] == name]


# ---------------------------------------------------------------------------
# RPC endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_waits_for_worker_to_drain_before_deleting(monkeypatch):
    worker = _make_worker()
    hung = _Hung()
    task = asyncio.create_task(hung.run())
    worker._active_tasks.add(task)
    await hung.started.wait()

    calls: list[str] = []
    session = _session(calls)
    redis = FakeRedis()
    redis.workers = [(worker, redis.handler_for("w1"))]
    monkeypatch.setattr(endpoints, "_studio_reset_redis", lambda: redis)

    result = await endpoints.reset_studio_state(session=session)

    assert hung.cancelled and task.done()
    assert calls == ["delete", "delete", "commit"]
    assert result == {
        "deleted_transactions": 3,
        "deleted_accounts": 2,
        "workers_reset": 1,
        "tasks_cancelled": 1,
    }
    finished = _events(redis, STUDIO_RESET_FINISHED)
    assert finished and finished[0]["data"]["success"] is True
    assert redis.deleted and redis.closed
    await asyncio.sleep(0)
    # FINISHED lifted the pause.
    assert not worker.is_admission_paused()


@pytest.mark.asyncio
async def test_redis_unavailable_is_an_error_and_deletes_nothing(monkeypatch):
    calls: list[str] = []
    session = _session(calls)
    redis = FakeRedis(publish_error=ConnectionError("redis down"))
    monkeypatch.setattr(endpoints, "_studio_reset_redis", lambda: redis)

    with pytest.raises(JSONRPCError, match="Nothing was deleted"):
        await endpoints.reset_studio_state(session=session)

    assert calls == []
    assert redis.closed


@pytest.mark.asyncio
async def test_redis_client_creation_failure_is_an_error(monkeypatch):
    def _boom():
        raise ConnectionError("bad url")

    monkeypatch.setattr(endpoints, "_studio_reset_redis", _boom)
    session = MagicMock()

    with pytest.raises(JSONRPCError, match="Nothing was deleted"):
        await endpoints.reset_studio_state(session=session)
    session.query.assert_not_called()


@pytest.mark.asyncio
async def test_delayed_worker_fails_reset_and_deletes_nothing(monkeypatch):
    worker = _make_worker()
    calls: list[str] = []
    session = _session(calls)
    # One worker answers, a second subscriber never does.
    redis = FakeRedis(extra=1)
    redis.workers = [(worker, redis.handler_for("w1"))]
    monkeypatch.setattr(endpoints, "_studio_reset_redis", lambda: redis)

    with pytest.raises(JSONRPCError, match="1 of 2 consensus workers did not"):
        await endpoints.reset_studio_state(session=session)

    assert calls == []
    finished = _events(redis, STUDIO_RESET_FINISHED)
    assert finished and finished[0]["data"]["success"] is False
    await asyncio.sleep(0)
    # The responsive worker is released right away, not left paused.
    assert not worker.is_admission_paused()


@pytest.mark.asyncio
async def test_worker_unable_to_stop_tasks_fails_reset(monkeypatch):
    worker = _make_worker()
    hung = _Hung(ignore_cancel_for=1.0)
    worker._active_tasks.add(asyncio.create_task(hung.run()))
    await hung.started.wait()

    calls: list[str] = []
    session = _session(calls)
    redis = FakeRedis()
    redis.workers = [(worker, redis.handler_for("w1"))]
    monkeypatch.setattr(endpoints, "_studio_reset_redis", lambda: redis)

    with pytest.raises(JSONRPCError, match="could not stop their running"):
        await endpoints.reset_studio_state(session=session)

    assert calls == []


@pytest.mark.asyncio
async def test_no_running_workers_still_resets(monkeypatch):
    calls: list[str] = []
    session = _session(calls)
    redis = FakeRedis()
    monkeypatch.setattr(endpoints, "_studio_reset_redis", lambda: redis)

    result = await endpoints.reset_studio_state(session=session)

    assert calls == ["delete", "delete", "commit"]
    assert result["workers_reset"] == 0


@pytest.mark.asyncio
async def test_acks_for_other_resets_are_ignored(monkeypatch):
    calls: list[str] = []
    session = _session(calls)
    redis = FakeRedis(deliver=False, extra=1)
    original_publish = redis.publish

    async def publish(channel, message):
        count = await original_publish(channel, message)
        event = json.loads(message)
        if event["event"] == STUDIO_RESET_REQUEST:
            await redis._queue(event["data"]["ack_key"]).put(
                json.dumps({"reset_id": "someone-else", "ok": True})
            )
        return count

    redis.publish = publish
    monkeypatch.setattr(endpoints, "_studio_reset_redis", lambda: redis)

    with pytest.raises(JSONRPCError, match="did not confirm"):
        await endpoints.reset_studio_state(session=session)
    assert calls == []


@pytest.mark.asyncio
async def test_blocked_in_hosted_mode(monkeypatch):
    monkeypatch.setenv("VITE_IS_HOSTED", "true")
    session = MagicMock()

    with pytest.raises(JSONRPCError):
        await endpoints.reset_studio_state(session=session)

    session.query.assert_not_called()


# ---------------------------------------------------------------------------
# Worker side
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_state_cancels_tasks_and_clears_bookkeeping():
    worker = _make_worker()
    hung = _Hung()
    task = asyncio.create_task(hung.run())
    worker._active_tasks.add(task)
    await hung.started.wait()
    worker.current_transactions["0xabc"] = {"type": "transaction"}
    worker._no_validators_retries["0xabc"] = {"count": 1}
    worker._generic_error_retries["0xabc"] = {"count": 1}
    worker._leader_crash_retries["0xabc"] = {"count": 1}

    result = await worker.reset_state(cancel_timeout=1)

    assert result == {"cancelled": 1, "still_running": 0}
    assert task.cancelled()
    assert worker.current_transactions == {}
    assert worker._no_validators_retries == {}
    assert worker._generic_error_retries == {}
    assert worker._leader_crash_retries == {}


@pytest.mark.asyncio
async def test_reset_state_reports_tasks_that_outlive_the_timeout():
    worker = _make_worker()
    hung = _Hung(ignore_cancel_for=0.5)
    task = asyncio.create_task(hung.run())
    worker._active_tasks.add(task)
    await hung.started.wait()

    result = await worker.reset_state(cancel_timeout=0.05)

    assert result == {"cancelled": 1, "still_running": 1}
    # Still tracked so run() keeps cleaning it up.
    assert task in worker._active_tasks
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_stale_reset_request_is_ignored():
    """A worker that only sees the request after the RPC gave up must not
    cancel anything (that work may have been submitted after the reset)."""
    worker = _make_worker()
    hung = _Hung()
    task = asyncio.create_task(hung.run())
    worker._active_tasks.add(task)
    await hung.started.wait()

    ack = await worker.handle_studio_reset_request(
        "r1", deadline=time.time() - 1, cancel_timeout=1
    )

    assert ack is None
    assert not task.done()
    assert not worker.is_admission_paused()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_claim_during_pause_is_released_not_started():
    worker = _make_worker()
    worker.pause_admission("r1", max_seconds=60)

    async def claim(_session):
        return {"hash": "0xnew"}

    worker.claim_next_appeal = claim
    session = MagicMock()

    assert await worker._try_claim_work(session) is False
    worker.release_transaction.assert_called_once_with(session, "0xnew")
    assert worker._active_tasks == set()


@pytest.mark.asyncio
async def test_submission_right_after_reset_is_processed():
    worker = _make_worker()
    worker.pause_admission("r1", max_seconds=60)
    worker.resume_admission("r1")

    async def no_claim(_session):
        return None

    async def claim_tx(_session):
        return {"hash": "0xpost-reset"}

    processed = asyncio.Event()

    async def process(_data):
        processed.set()

    worker.claim_next_appeal = no_claim
    worker.claim_next_finalization = no_claim
    worker.claim_next_transaction = claim_tx
    worker._is_in_backoff = lambda _data: False
    worker._process_transaction_task = process

    assert await worker._try_claim_work(MagicMock()) is True
    await asyncio.wait_for(processed.wait(), timeout=1)
    worker.release_transaction.assert_not_called()


def test_pause_only_lifted_by_its_own_reset_or_expiry(monkeypatch):
    worker = _make_worker()
    now = [1000.0]
    monkeypatch.setattr("backend.consensus.worker.time.monotonic", lambda: now[0])

    worker.pause_admission("r1", max_seconds=10)
    worker.resume_admission("other")
    assert worker.is_admission_paused()

    now[0] += 11
    assert not worker.is_admission_paused()


@pytest.mark.asyncio
async def test_run_loop_survives_cancelled_tasks():
    worker = _make_worker()
    worker.poll_interval = 0.01
    worker.pause_admission("r1", max_seconds=60)  # keep run() off the DB
    cancelled = asyncio.create_task(asyncio.sleep(60))
    worker._active_tasks.add(cancelled)
    cancelled.cancel()

    runner = asyncio.create_task(worker.run())
    await asyncio.sleep(0.05)
    worker.stop()
    await asyncio.wait_for(runner, timeout=1)

    assert runner.exception() is None
    assert worker._active_tasks == set()


@pytest.mark.asyncio
async def test_worker_without_consensus_worker_acks_ok():
    redis = FakeRedis()
    handler = redis.handler_for("w1")
    await worker_service.handle_studio_reset_event(
        {
            "event": STUDIO_RESET_REQUEST,
            "data": {"reset_id": "r1", "ack_key": "k", "deadline": time.time() + 5},
        },
        None,
        handler,
    )
    ack = json.loads(await redis._queue("k").get())
    assert ack["ok"] is True and ack["reset_id"] == "r1"
