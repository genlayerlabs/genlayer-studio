"""Snapshot restore must make workers drop in-flight work before the DB is rewritten (DXP-726)."""

import asyncio
import threading
import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from backend.consensus import worker_control
from backend.protocol_rpc.exceptions import JSONRPCError


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
        poll_interval=0,
    )


# --------------------------------------------------------------------------- #
# Worker side
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_release_in_flight_cancels_task_and_releases_its_claim():
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
        worker._generic_error_retries["0xtx"] = {"count": 1}

        await worker.release_in_flight()

    assert task.cancelled()
    assert worker._active_tasks == set()
    release_transaction.assert_called_once()
    assert release_transaction.call_args.args[1] == "0xtx"
    release_all.assert_called_once()
    assert worker.current_transactions == {}
    assert worker._generic_error_retries == {}


@pytest.mark.asyncio
async def test_main_loop_survives_release_with_multiple_active_tasks():
    """Cancelling in-flight tasks while run() is looping must not kill run()
    (CancelledError from task.result() is not an Exception)."""
    worker = _make_worker()
    worker.max_parallel_txs = 3
    claims = 0
    all_started = asyncio.Event()
    started = 0

    async def long_task():
        nonlocal started
        started += 1
        if started == 2:
            all_started.set()
        await asyncio.Event().wait()

    async def fake_claim(_session):
        nonlocal claims
        claims += 1
        if claims <= 2:
            worker._active_tasks.add(asyncio.create_task(long_task()))
            return True
        return False

    with (
        patch.object(worker, "_try_claim_work", side_effect=fake_claim),
        patch.object(worker, "recover_stuck_transactions", return_value=0),
        patch.object(worker, "release_all_claims", return_value=0),
    ):
        run_task = asyncio.create_task(worker.run())
        await asyncio.wait_for(all_started.wait(), 2)
        claims_before = claims

        await worker.release_in_flight()

        # Let the loop iterate a few times after the cancellation.
        for _ in range(20):
            await asyncio.sleep(0)
        await asyncio.sleep(0.05)
        assert not run_task.done(), run_task
        assert claims > claims_before  # still claiming work

        worker.stop()
        await asyncio.wait_for(run_task, 2)

    assert run_task.exception() is None


@pytest.mark.asyncio
async def test_main_loop_skips_already_cancelled_tasks():
    worker = _make_worker()

    async def never():
        await asyncio.Event().wait()

    cancelled = asyncio.create_task(never())
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    worker._active_tasks.add(cancelled)

    async def stop_after_one(_session):
        worker.running = False
        return False

    with patch.object(worker, "_try_claim_work", side_effect=stop_after_one):
        await asyncio.wait_for(worker.run(), 2)

    assert cancelled not in worker._active_tasks


@pytest.mark.asyncio
async def test_closed_restore_gate_blocks_all_claims():
    worker = _make_worker()
    with (
        patch.object(worker_control, "claim_gate_open", return_value=False),
        patch.object(worker, "claim_next_appeal", new_callable=AsyncMock) as a,
        patch.object(worker, "claim_next_finalization", new_callable=AsyncMock) as f,
        patch.object(worker, "claim_next_transaction", new_callable=AsyncMock) as t,
    ):
        assert await worker._try_claim_work(MagicMock(spec=Session)) is False

    a.assert_not_awaited()
    f.assert_not_awaited()
    t.assert_not_awaited()


@pytest.mark.asyncio
async def test_closed_restore_gate_blocks_recovery_writes():
    worker = _make_worker()
    session = MagicMock(spec=Session)
    with patch.object(worker_control, "claim_gate_open", return_value=False):
        assert await worker.recover_stuck_transactions(session) == 0
    session.execute.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_release_in_flight_is_bounded_when_cleanup_hangs():
    worker = _make_worker()
    worker.release_timeout_seconds = 0.05
    started = asyncio.Event()
    unblock = asyncio.Event()

    async def hung_cleanup():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await unblock.wait()  # cleanup that ignores further cancellation
            raise

    with patch.object(worker, "release_all_claims", return_value=0) as release_all:
        task = asyncio.create_task(hung_cleanup())
        worker._active_tasks.add(task)
        await started.wait()

        await asyncio.wait_for(worker.release_in_flight(), 2)

        assert not worker.is_releasing  # claiming can resume
        assert not task.done()
        assert task in worker._active_tasks  # still occupies its slot
        release_all.assert_not_called()  # its claim keeps blocking the restore

        unblock.set()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_control_event_dispatch():
    worker = _make_worker()
    with patch.object(worker, "release_in_flight", new_callable=AsyncMock) as rel:
        await worker.handle_control_event(
            {"event": worker_control.RELEASE_IN_FLIGHT_EVENT}
        )
        await worker.handle_control_event({"event": "unknown"})
    rel.assert_awaited_once()


# --------------------------------------------------------------------------- #
# RPC side
# --------------------------------------------------------------------------- #


def _recording_gate(order):
    @asynccontextmanager
    async def gate(engine, timeout_seconds):
        order.append("gate")
        try:
            yield
        finally:
            order.append("ungate")

    return gate


def _releasable(order=None):
    async def check(session):
        if order is not None:
            order.append("check")

    return check


def _manager(order=None, exists=True):
    manager = MagicMock()
    manager.get_snapshot.return_value = object() if exists else None
    manager.restore_snapshot.side_effect = (
        lambda _id: (order.append("restore") if order is not None else None) or True
    )
    return manager


@pytest.mark.asyncio
async def test_restore_order_gate_release_wait_restore_ungate():
    from backend.protocol_rpc import endpoints

    order = []

    async def publish(event, data=None):
        order.append(event)
        return 1

    async def wait(session, timeout_seconds):
        order.append(("wait", timeout_seconds))

    manager = _manager(order)
    with (
        patch.object(endpoints.worker_control, "restore_gate", _recording_gate(order)),
        patch.object(
            endpoints.worker_control, "ensure_claims_releasable", _releasable(order)
        ),
        patch.object(endpoints.worker_control, "publish_worker_control", publish),
        patch.object(endpoints.worker_control, "wait_for_claims_released", wait),
    ):
        assert await endpoints.restore_snapshot(manager, 7) is True

    assert order == [
        "gate",
        "check",
        worker_control.RELEASE_IN_FLIGHT_EVENT,
        ("wait", worker_control.DEFAULT_RELEASE_TIMEOUT_SECONDS),
        "restore",
        "ungate",
    ]


@pytest.mark.asyncio
async def test_restore_aborts_without_touching_db_when_claims_remain():
    from backend.protocol_rpc import endpoints

    order = []
    manager = _manager(order)
    with (
        patch.object(endpoints.worker_control, "restore_gate", _recording_gate(order)),
        patch.object(
            endpoints.worker_control, "ensure_claims_releasable", _releasable()
        ),
        patch.object(
            endpoints.worker_control,
            "publish_worker_control",
            AsyncMock(return_value=1),
        ),
        patch.object(
            endpoints.worker_control,
            "wait_for_claims_released",
            AsyncMock(side_effect=worker_control.WorkerCoordinationError("held")),
        ),
    ):
        with pytest.raises(JSONRPCError) as exc:
            await endpoints.restore_snapshot(manager, 7)

    assert "held" in exc.value.message
    # The release event already went out: the error must not claim otherwise.
    assert "told to cancel" in exc.value.message
    manager.restore_snapshot.assert_not_called()
    assert order == ["gate", "ungate"]


@pytest.mark.asyncio
async def test_restore_aborts_before_release_event_when_claims_are_unreleasable():
    from backend.protocol_rpc import endpoints

    manager = _manager()
    publish = AsyncMock(return_value=1)
    with (
        patch.object(endpoints.worker_control, "restore_gate", _recording_gate([])),
        patch.object(
            endpoints.worker_control,
            "ensure_claims_releasable",
            AsyncMock(side_effect=worker_control.WorkerCoordinationError("dead")),
        ),
        patch.object(endpoints.worker_control, "publish_worker_control", publish),
    ):
        with pytest.raises(JSONRPCError) as exc:
            await endpoints.restore_snapshot(manager, 7)

    publish.assert_not_awaited()
    assert "told to cancel" not in exc.value.message
    manager.restore_snapshot.assert_not_called()


@pytest.mark.asyncio
async def test_restore_runs_db_work_off_the_event_loop():
    from backend.protocol_rpc import endpoints

    loop_thread = threading.get_ident()
    threads = []
    manager = _manager()
    manager.get_snapshot.side_effect = (
        lambda _id: threads.append(threading.get_ident()) or object()
    )
    manager.restore_snapshot.side_effect = (
        lambda _id: threads.append(threading.get_ident()) or True
    )
    with (
        patch.object(endpoints.worker_control, "restore_gate", _recording_gate([])),
        patch.object(
            endpoints.worker_control, "ensure_claims_releasable", _releasable()
        ),
        patch.object(
            endpoints.worker_control,
            "publish_worker_control",
            AsyncMock(return_value=1),
        ),
        patch.object(endpoints.worker_control, "wait_for_claims_released", AsyncMock()),
    ):
        assert await endpoints.restore_snapshot(manager, 7) is True

    assert len(threads) == 2 and loop_thread not in threads


@pytest.mark.asyncio
async def test_restore_does_not_wait_when_release_event_was_not_delivered():
    from backend.protocol_rpc import endpoints

    wait = AsyncMock()
    with (
        patch.object(endpoints.worker_control, "restore_gate", _recording_gate([])),
        patch.object(
            endpoints.worker_control, "ensure_claims_releasable", _releasable()
        ),
        patch.object(
            endpoints.worker_control,
            "publish_worker_control",
            AsyncMock(return_value=None),
        ),
        patch.object(endpoints.worker_control, "wait_for_claims_released", wait),
    ):
        await endpoints.restore_snapshot(_manager(), 7)

    assert wait.await_args.args[1] == 0


@pytest.mark.asyncio
async def test_restore_aborts_when_gate_cannot_be_acquired():
    from backend.protocol_rpc import endpoints

    @asynccontextmanager
    async def busy_gate(engine, timeout_seconds):
        raise worker_control.WorkerCoordinationError("busy")
        yield  # pragma: no cover

    manager = _manager()
    publish = AsyncMock()
    with (
        patch.object(endpoints.worker_control, "restore_gate", busy_gate),
        patch.object(endpoints.worker_control, "publish_worker_control", publish),
    ):
        with pytest.raises(JSONRPCError):
            await endpoints.restore_snapshot(manager, 7)

    publish.assert_not_awaited()
    manager.restore_snapshot.assert_not_called()


@pytest.mark.asyncio
async def test_restore_unknown_snapshot_does_nothing():
    from backend.protocol_rpc import endpoints

    manager = _manager(exists=False)
    gate = MagicMock()
    with patch.object(endpoints.worker_control, "restore_gate", gate):
        assert await endpoints.restore_snapshot(manager, 7) is False

    gate.assert_not_called()
    manager.restore_snapshot.assert_not_called()


@pytest.mark.asyncio
async def test_wait_for_claims_released_polls_until_clear():
    claims = iter([[("0x1", "w")], [("0x1", "w")], []])
    with patch.object(worker_control, "claimed_transactions", lambda _s: next(claims)):
        await worker_control.wait_for_claims_released(
            MagicMock(), timeout_seconds=5, poll_interval=0
        )


@pytest.mark.asyncio
async def test_wait_for_claims_released_raises_on_timeout():
    with patch.object(
        worker_control, "claimed_transactions", lambda _s: [("0xabc", "w1")]
    ):
        with pytest.raises(worker_control.WorkerCoordinationError, match="0xabc"):
            await worker_control.wait_for_claims_released(
                MagicMock(), timeout_seconds=0, poll_interval=0
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claimed, subscribers, ok",
    [
        ([], None, True),  # nothing claimed: no Redis needed
        ([("0x1", "w1"), ("0x2", "w2")], 2, True),
        ([("0x1", "w1"), ("0x2", "w2")], 1, False),  # some owner is gone
        ([("0x1", "w1")], None, False),  # can't reach any worker
    ],
)
async def test_ensure_claims_releasable(claimed, subscribers, ok):
    subs = AsyncMock(return_value=subscribers)
    with (
        patch.object(worker_control, "claimed_transactions", lambda _s: claimed),
        patch.object(worker_control, "worker_control_subscribers", subs),
    ):
        if ok:
            await worker_control.ensure_claims_releasable(MagicMock())
        else:
            with pytest.raises(worker_control.WorkerCoordinationError):
                await worker_control.ensure_claims_releasable(MagicMock())
    if not claimed:
        subs.assert_not_awaited()


@pytest.mark.asyncio
async def test_restore_gate_runs_db_calls_off_the_loop_on_one_connection():
    loop_thread = threading.get_ident()
    calls = []
    conn = MagicMock()

    def execute(stmt, params=None):
        calls.append((str(stmt), threading.get_ident()))
        return MagicMock(scalar=MagicMock(return_value=True))

    conn.execute.side_effect = execute
    engine = MagicMock()
    engine.connect.return_value = conn

    async with worker_control.restore_gate(engine, timeout_seconds=1):
        pass

    engine.connect.assert_called_once()
    assert [c[0] for c in calls] == [
        "SELECT pg_try_advisory_lock(:key)",
        "SELECT pg_advisory_unlock(:key)",
    ]
    assert all(t != loop_thread for _, t in calls)
    conn.close.assert_called_once()


@pytest.mark.asyncio
async def test_run_blocking_waits_for_thread_when_cancelled():
    finished = threading.Event()

    def slow():
        time.sleep(0.2)
        finished.set()

    task = asyncio.create_task(worker_control.run_blocking(slow))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


@pytest.mark.asyncio
async def test_publish_without_redis_returns_none(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert await worker_control.publish_worker_control("x") is None


@pytest.mark.asyncio
async def test_no_claims_until_release_cleanup_finishes_even_after_gate_reopens():
    """Restore timed out (gate open again) while this worker's cancellation
    cleanup is still running: the worker must not claim until cleanup is done,
    and the final sweep must not touch work claimed afterwards."""
    worker = _make_worker()
    worker.max_parallel_txs = 2
    events: list[str] = []
    a_running = asyncio.Event()
    b_running = asyncio.Event()
    queue = [{"hash": "0xA"}]

    async def claim_next_transaction(_session):
        if not queue:
            return None
        tx = queue.pop(0)
        events.append(f"claim:{tx['hash']}")
        return tx

    async def process(transaction_data):
        tx_hash = transaction_data["hash"]
        async with worker._transaction_context(
            tx_hash, transaction_data, MagicMock(spec=Session)
        ):
            (a_running if tx_hash == "0xA" else b_running).set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                # Slow cleanup: outlasts the RPC's release timeout.
                await asyncio.sleep(0.3)
                events.append(f"cleaned:{tx_hash}")
                raise

    def release_all_claims(_session):
        events.append("sweep")
        return 0

    with (
        # The RPC gave up and released the gate: claims are allowed DB-side.
        patch.object(worker_control, "claim_gate_open", return_value=True),
        patch.object(worker, "claim_next_appeal", AsyncMock(return_value=None)),
        patch.object(worker, "claim_next_finalization", AsyncMock(return_value=None)),
        patch.object(worker, "claim_next_transaction", claim_next_transaction),
        patch.object(worker, "_process_transaction_task", process),
        patch.object(worker, "_is_in_backoff", return_value=False),
        patch.object(worker, "recover_stuck_transactions", AsyncMock(return_value=0)),
        patch.object(worker, "release_transaction"),
        patch.object(worker, "release_all_claims", side_effect=release_all_claims),
    ):
        worker.poll_interval = 0.01
        run_task = asyncio.create_task(worker.run())
        await asyncio.wait_for(a_running.wait(), 2)

        release = asyncio.create_task(worker.release_in_flight())
        await asyncio.sleep(0)
        # New work becomes claimable (gate already reopened by the RPC).
        queue.append({"hash": "0xB"})
        await asyncio.sleep(0.15)  # cleanup of A still running; loop is polling
        assert not release.done()
        assert queue == [{"hash": "0xB"}], "claimed while releasing"
        assert worker.is_releasing

        await asyncio.wait_for(release, 2)
        assert not worker.is_releasing

        # Claiming resumes once cleanup is done, and the new work is intact.
        await asyncio.wait_for(b_running.wait(), 2)
        assert events == ["claim:0xA", "cleaned:0xA", "sweep", "claim:0xB"]
        assert "0xB" in worker.current_transactions
        b_task = next(iter(worker._active_tasks))
        assert not b_task.done()

        worker.stop()
        b_task.cancel()
        await asyncio.wait_for(run_task, 3)


@pytest.mark.asyncio
async def test_gate_rechecked_after_backoff_release_commits():
    """A backoff release commits and ends the shared gate lock; the next claim
    must re-take it (and stop if a restore grabbed the gate meanwhile)."""
    worker = _make_worker()
    with (
        patch.object(worker_control, "claim_gate_open", side_effect=[True, False]),
        patch.object(
            worker, "claim_next_appeal", AsyncMock(return_value={"hash": "0xA"})
        ),
        patch.object(worker, "_is_in_backoff", return_value=True),
        patch.object(worker, "release_transaction") as release,
        patch.object(worker, "claim_next_finalization", new_callable=AsyncMock) as f,
        patch.object(worker, "claim_next_transaction", new_callable=AsyncMock) as t,
    ):
        assert await worker._try_claim_work(MagicMock(spec=Session)) is False

    release.assert_called_once()
    f.assert_not_awaited()
    t.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_does_not_claim_without_its_liveness_lock():
    worker = _make_worker()
    worker.liveness_ok = False
    with (
        patch.object(worker_control, "claim_gate_open", return_value=True),
        patch.object(worker, "claim_next_appeal", new_callable=AsyncMock) as a,
        patch.object(worker, "claim_next_transaction", new_callable=AsyncMock) as t,
    ):
        assert await worker._try_claim_work(MagicMock(spec=Session)) is False
    a.assert_not_awaited()
    t.assert_not_awaited()


class _FakeLiveness:
    def __init__(self, held_results, acquire_results):
        self.held_results = list(held_results)
        self.acquire_results = list(acquire_results)
        self.closed = 0

    def held(self):
        return self.held_results.pop(0)

    def acquire(self):
        return self.acquire_results.pop(0)

    def close(self):
        self.closed += 1


@pytest.mark.asyncio
async def test_maintain_liveness_drops_work_when_lock_is_lost_then_reacquires():
    worker = _make_worker()
    worker.liveness_ok = False
    states = []

    async def release():
        states.append(("release", worker.liveness_ok))
        return None

    worker.release_in_flight = release
    # start: acquire ok; tick 2: lost, re-acquire fails; tick 3: re-acquire ok
    liveness = _FakeLiveness(
        held_results=[True, False], acquire_results=[True, False, True]
    )
    ticks = 0

    async def fake_sleep(_):
        nonlocal ticks
        states.append(("tick", worker.liveness_ok))
        ticks += 1
        if ticks >= 4:
            raise asyncio.CancelledError

    with patch.object(worker_control.asyncio, "sleep", side_effect=fake_sleep):
        with pytest.raises(asyncio.CancelledError):
            await worker_control.maintain_liveness(worker, liveness, interval=0)

    assert states == [
        ("tick", True),  # acquired at start
        ("tick", True),  # still held
        ("release", False),  # lost: claiming already stopped when work is dropped
        ("tick", False),  # re-acquire failed: still not claiming
        ("tick", True),  # re-acquired
    ]
    assert liveness.closed == 1
