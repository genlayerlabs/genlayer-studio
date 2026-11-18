"""Snapshot restore must make workers drop in-flight work before the DB is rewritten (DXP-726)."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from backend.consensus import worker_control


def _make_worker():
    from backend.consensus.worker import ConsensusWorker

    def get_session_side_effect():
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=MagicMock(spec=Session))
        ctx.__exit__ = MagicMock(return_value=None)
        return ctx

    return ConsensusWorker(
        get_session=get_session_side_effect,
        msg_handler=MagicMock(),
        consensus_service=MagicMock(),
        validators_manager=MagicMock(),
        genvm_manager=MagicMock(),
        worker_id="test-worker",
    )


# --------------------------------------------------------------------------- #
# Worker side
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_pause_cancels_in_flight_task_and_releases_its_claim():
    worker = _make_worker()
    started = asyncio.Event()

    async def in_flight():
        async with worker._transaction_context("0xtx", {}, MagicMock(spec=Session)):
            started.set()
            await asyncio.Event().wait()  # simulate a long consensus round

    with (
        patch.object(worker, "release_transaction") as release_transaction,
        patch.object(worker, "release_all_claims", return_value=0) as release_all,
    ):
        task = asyncio.create_task(in_flight())
        worker._active_tasks.add(task)
        await started.wait()
        assert "0xtx" in worker.current_transactions

        worker._generic_error_retries["0xtx"] = {"count": 1}
        await worker.pause_and_release(pause_seconds=60)

    assert task.cancelled()
    assert task not in worker._active_tasks
    release_transaction.assert_called_once()
    assert release_transaction.call_args.args[1] == "0xtx"
    release_all.assert_called_once()
    assert worker.current_transactions == {}
    assert worker._generic_error_retries == {}
    assert worker.is_paused()


@pytest.mark.asyncio
async def test_claim_racing_with_pause_is_released_not_processed():
    worker = _make_worker()
    worker._paused_until = time.monotonic() + 60

    with (
        patch.object(worker, "release_transaction") as release_transaction,
        patch.object(worker, "process_transaction", new_callable=AsyncMock) as process,
    ):
        await worker._process_transaction_task({"hash": "0xtx"})

    process.assert_not_awaited()
    release_transaction.assert_called_once()
    assert release_transaction.call_args.args[1] == "0xtx"


@pytest.mark.asyncio
async def test_paused_worker_does_not_claim_work():
    worker = _make_worker()
    worker._paused_until = time.monotonic() + 60
    worker.poll_interval = 0

    calls = 0

    async def fake_sleep(_):
        nonlocal calls
        calls += 1
        if calls >= 3:
            worker.running = False

    with (
        patch.object(worker, "_try_claim_work", new_callable=AsyncMock) as claim,
        patch.object(
            worker, "recover_stuck_transactions", new_callable=AsyncMock
        ) as recover,
        patch(
            "backend.consensus.worker.asyncio.sleep",
            new_callable=AsyncMock,
            side_effect=fake_sleep,
        ),
    ):
        await worker.run()

    claim.assert_not_awaited()
    recover.assert_not_awaited()


@pytest.mark.asyncio
async def test_control_events_pause_and_resume():
    worker = _make_worker()

    with patch.object(worker, "release_all_claims", return_value=0):
        await worker.handle_control_event(
            {"event": worker_control.PAUSE_EVENT, "data": {"pause_seconds": 60}}
        )
    assert worker.is_paused()

    await worker.handle_control_event({"event": worker_control.RESUME_EVENT})
    assert not worker.is_paused()


def test_pause_expires_without_resume():
    worker = _make_worker()
    worker._paused_until = time.monotonic() - 1
    assert not worker.is_paused()
    assert worker._paused_until is None


# --------------------------------------------------------------------------- #
# RPC side
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_restore_pauses_workers_waits_for_release_then_resumes():
    from backend.protocol_rpc import endpoints

    order = []
    snapshot_manager = MagicMock()
    snapshot_manager.get_snapshot.return_value = object()
    snapshot_manager.restore_snapshot.side_effect = lambda _id: (
        order.append("restore") or True
    )

    async def publish(event, data=None):
        order.append(event)
        return 1

    async def wait(session, timeout_seconds):
        order.append("wait")
        return True

    with (
        patch.object(endpoints.worker_control, "publish_worker_control", publish),
        patch.object(endpoints.worker_control, "wait_for_claims_released", wait),
    ):
        assert await endpoints.restore_snapshot(snapshot_manager, 7) is True

    assert order == [
        worker_control.PAUSE_EVENT,
        "wait",
        "restore",
        worker_control.RESUME_EVENT,
    ]
    snapshot_manager.restore_snapshot.assert_called_once_with(7)


@pytest.mark.asyncio
async def test_restore_resumes_workers_even_if_restore_fails():
    from backend.protocol_rpc import endpoints

    snapshot_manager = MagicMock()
    snapshot_manager.get_snapshot.return_value = object()
    snapshot_manager.restore_snapshot.side_effect = RuntimeError("boom")
    publish = AsyncMock(return_value=1)

    with (
        patch.object(endpoints.worker_control, "publish_worker_control", publish),
        patch.object(
            endpoints.worker_control,
            "wait_for_claims_released",
            AsyncMock(return_value=True),
        ),
    ):
        with pytest.raises(RuntimeError):
            await endpoints.restore_snapshot(snapshot_manager, 7)

    assert publish.await_args_list[-1].args[0] == worker_control.RESUME_EVENT


@pytest.mark.asyncio
async def test_restore_unknown_snapshot_does_not_pause_workers():
    from backend.protocol_rpc import endpoints

    snapshot_manager = MagicMock()
    snapshot_manager.get_snapshot.return_value = None
    publish = AsyncMock()

    with patch.object(endpoints.worker_control, "publish_worker_control", publish):
        assert await endpoints.restore_snapshot(snapshot_manager, 7) is False

    publish.assert_not_awaited()
    snapshot_manager.restore_snapshot.assert_not_called()


@pytest.mark.asyncio
async def test_wait_for_claims_released_polls_until_clear():
    counts = iter([2, 1, 0])
    with patch.object(
        worker_control, "count_claimed_transactions", lambda _s: next(counts)
    ):
        assert (
            await worker_control.wait_for_claims_released(
                MagicMock(), timeout_seconds=5, poll_interval=0
            )
            is True
        )


@pytest.mark.asyncio
async def test_wait_for_claims_released_times_out():
    with patch.object(worker_control, "count_claimed_transactions", lambda _s: 1):
        assert (
            await worker_control.wait_for_claims_released(
                MagicMock(), timeout_seconds=0, poll_interval=0
            )
            is False
        )


@pytest.mark.asyncio
async def test_publish_without_redis_is_a_noop(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert await worker_control.publish_worker_control("x") is None
