"""
DXP-726: restoring a snapshot must not race with consensus workers.

Real-Postgres tests for the restore gate (advisory lock) that blocks worker
claims during a restore, and for aborting the restore when workers don't let
go of their claims.
"""

import asyncio
import os
from contextlib import contextmanager
from typing import Iterable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

os.environ.setdefault("VITE_FINALITY_WINDOW", "30")
os.environ.setdefault("VITE_FINALITY_WINDOW_APPEAL_FAILED_REDUCTION", "0.2")

from backend.consensus import worker_control
from backend.consensus.worker import ConsensusWorker
from backend.database_handler.models import CurrentState
from backend.database_handler.snapshot_manager import SnapshotManager
from backend.protocol_rpc import endpoints
from backend.protocol_rpc.exceptions import JSONRPCError

CONTRACT = "0xsnapshot_restore_gate_contract"
SENDER = "0x" + "cd" * 20
TX_A = "0x" + "a1" * 32
TX_B = "0x" + "b2" * 32
TX_C = "0x" + "c3" * 32


def _insert_tx(
    session: Session,
    tx_hash: str,
    nonce: int,
    worker_id=None,
    status: str = "PENDING",
    blocked: str = "NOW()",
    consensus_history: str = "NULL",
):
    blocked = blocked if worker_id else "NULL"
    session.execute(
        text(
            f"""
            INSERT INTO transactions (
                hash, status, from_address, to_address, data, value, type, nonce,
                leader_only, execution_mode, appealed, appeal_failed,
                appeal_undetermined, appeal_leader_timeout,
                appeal_validators_timeout, appeal_processing_time,
                blocked_at, worker_id, recovery_count, value_credited, created_at,
                consensus_history
            ) VALUES (
                :hash, :status, :sender, :to, CAST('{{"calldata": "x"}}' AS jsonb),
                0, 2, :nonce, false, 'NORMAL', false, 0, false, false, false, 0,
                {blocked}, :worker_id, 0, false, NOW(), {consensus_history}
            )
            """
        ),
        {
            "hash": tx_hash,
            "sender": SENDER,
            "to": CONTRACT,
            "nonce": nonce,
            "worker_id": worker_id,
            "status": status,
        },
    )
    session.commit()


def _claim_of(session: Session, tx_hash: str):
    session.rollback()
    return session.execute(
        text("SELECT worker_id FROM transactions WHERE hash = :h"), {"h": tx_hash}
    ).first()


@pytest.fixture
def session(engine: Engine) -> Iterable[Session]:
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    s.add(CurrentState(id=CONTRACT, data={"state": {"accepted": {}, "finalized": {}}}))
    s.commit()
    yield s
    s.close()


@pytest.fixture
def alive(engine: Engine):
    """Mark worker ids as running: hold their liveness locks for the test."""
    held: list[worker_control.WorkerLiveness] = []

    def make_alive(*worker_ids: str) -> list[worker_control.WorkerLiveness]:
        for worker_id in worker_ids:
            liveness = worker_control.WorkerLiveness(engine, worker_id)
            assert liveness.acquire()
            held.append(liveness)
        return held[-len(worker_ids) :]

    yield make_alive
    for liveness in held:
        liveness.close()


@pytest.fixture
def worker(engine: Engine) -> ConsensusWorker:
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def get_session():
        s = Session_()
        try:
            yield s
        finally:
            s.close()

    return ConsensusWorker(
        get_session=get_session,
        msg_handler=MagicMock(),
        consensus_service=MagicMock(),
        validators_manager=MagicMock(),
        genvm_manager=MagicMock(),
        worker_id="worker-that-missed-the-event",
    )


@pytest.mark.asyncio
async def test_gate_blocks_claims_and_recovery_from_worker_that_never_got_the_event(
    engine: Engine, session: Session, worker: ConsensusWorker
):
    """Delayed/never-delivered release event + an initially unclaimed tx:
    while the gate is held the worker cannot claim it; afterwards it can."""
    _insert_tx(session, TX_A, nonce=0)

    with patch.object(worker, "_process_transaction_task", new_callable=AsyncMock):
        async with worker_control.restore_gate(engine, timeout_seconds=1):
            with sessionmaker(bind=engine)() as ws:
                assert await worker._try_claim_work(ws) is False
                assert await worker.recover_stuck_transactions(ws) == 0
            assert _claim_of(session, TX_A).worker_id is None

        with sessionmaker(bind=engine)() as ws:
            assert await worker._try_claim_work(ws) is True
        assert _claim_of(session, TX_A).worker_id == worker.worker_id

        # Clean up spawned (mocked) tasks
        for t in worker._active_tasks:
            await t


@pytest.mark.asyncio
async def test_restore_runs_with_gate_held_and_worker_cannot_claim_mid_restore(
    engine: Engine, session: Session, worker: ConsensusWorker
):
    manager = SnapshotManager(session)
    snapshot_id = manager.create_snapshot().snapshot_id  # empty transactions
    _insert_tx(session, TX_A, nonce=0)  # unclaimed, worker never got the event

    real_restore = manager.restore_snapshot
    attempts = []

    def restore_with_concurrent_claim(sid):
        # Simulates the worker polling while the DB is being rewritten.
        with sessionmaker(bind=engine)() as ws:
            attempts.append(worker_control.claim_gate_open(ws))
        return real_restore(sid)

    with (
        patch.object(manager, "restore_snapshot", restore_with_concurrent_claim),
        patch.object(
            endpoints.worker_control,
            "publish_worker_control",
            AsyncMock(return_value=1),
        ),
    ):
        assert await endpoints.restore_snapshot(manager, snapshot_id) is True

    assert attempts == [False]
    assert _claim_of(session, TX_A) is None  # restored to the (empty) snapshot

    # Gate released afterwards
    with sessionmaker(bind=engine)() as ws:
        assert worker_control.claim_gate_open(ws) is True


@pytest.mark.asyncio
async def test_restore_aborts_and_leaves_db_untouched_when_claims_remain(
    engine: Engine, session: Session, monkeypatch, alive
):
    monkeypatch.setenv("SNAPSHOT_RESTORE_RELEASE_TIMEOUT_SECONDS", "0.3")
    alive("unresponsive-worker")
    manager = SnapshotManager(session)
    snapshot_id = manager.create_snapshot().snapshot_id
    _insert_tx(session, TX_A, nonce=0, worker_id="unresponsive-worker")
    _insert_tx(session, TX_B, nonce=1)

    with (
        patch.object(
            endpoints.worker_control,
            "worker_control_subscribers",
            AsyncMock(return_value=1),
        ),
        patch.object(
            endpoints.worker_control,
            "publish_worker_control",
            AsyncMock(return_value=1),
        ),
    ):
        with pytest.raises(JSONRPCError) as exc:
            await endpoints.restore_snapshot(manager, snapshot_id)

    assert TX_A in exc.value.message
    assert "told to cancel" in exc.value.message
    assert _claim_of(session, TX_A).worker_id == "unresponsive-worker"
    assert _claim_of(session, TX_B) is not None
    with sessionmaker(bind=engine)() as ws:
        assert worker_control.claim_gate_open(ws) is True


@pytest.mark.asyncio
async def test_claims_of_terminated_workers_do_not_block_restore(
    engine: Engine, session: Session
):
    """A worker died holding claims (fresh or old, any status): its liveness
    lock is gone, so its claims are ignored and the restore rewrites them."""
    manager = SnapshotManager(session)
    _insert_tx(session, TX_A, nonce=0)
    snapshot_id = manager.create_snapshot().snapshot_id
    _insert_tx(session, TX_B, nonce=1, worker_id="dead-worker")  # fresh claim
    _insert_tx(session, TX_C, nonce=2, worker_id="dead-worker", status="FINALIZED")

    assert worker_control.claimed_transactions(session) == []
    publish = AsyncMock(return_value=0)
    with patch.object(endpoints.worker_control, "publish_worker_control", publish):
        assert await endpoints.restore_snapshot(manager, snapshot_id) is True

    assert _claim_of(session, TX_A).worker_id is None
    assert _claim_of(session, TX_B) is None


@pytest.mark.asyncio
async def test_claims_of_running_workers_count_whatever_their_age_or_status(
    engine: Engine, session: Session, alive
):
    """An expired lease or a terminal status does not stop the owning task."""
    alive("slow-worker")
    _insert_tx(
        session,
        TX_A,
        nonce=0,
        worker_id="slow-worker",
        status="PROPOSING",
        blocked="NOW() - INTERVAL '2 hours'",
    )
    _insert_tx(session, TX_B, nonce=1, worker_id="slow-worker", status="FINALIZED")
    _insert_tx(session, TX_C, nonce=2, worker_id="dead-worker")

    assert sorted(worker_control.claimed_transactions(session)) == sorted(
        [(TX_A, "slow-worker"), (TX_B, "slow-worker")]
    )


def test_ownerless_claims_are_ignored(engine: Engine, session: Session):
    """Every claim query sets worker_id with blocked_at: no task holds these."""
    _insert_tx(session, TX_A, nonce=0, worker_id="w")
    session.execute(
        text("UPDATE transactions SET worker_id = NULL WHERE hash = :h"), {"h": TX_A}
    )
    session.commit()
    assert worker_control.claimed_transactions(session) == []


def test_liveness_lock_confirms_termination(engine: Engine, session: Session):
    liveness = worker_control.WorkerLiveness(engine, "w1")
    assert worker_control.worker_terminated(session, "w1") is True
    try:
        assert liveness.acquire()
        assert liveness.held()
        assert worker_control.worker_terminated(session, "w1") is False
        # The probe never leaves the lock with the RPC.
        assert worker_control.worker_terminated(session, "w1") is False
        assert worker_control.WorkerLiveness(engine, "w1").acquire() is False

        # The worker's connection dies (e.g. the process is killed).
        pid = liveness.conn.execute(text("SELECT pg_backend_pid()")).scalar()
        liveness.conn.commit()
        with sessionmaker(bind=engine)() as admin:
            admin.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
            admin.commit()
        assert liveness.held() is False
        assert worker_control.worker_terminated(session, "w1") is True
    finally:
        liveness.close()
    session.rollback()


@pytest.mark.asyncio
async def test_restore_waits_for_running_task_with_expired_lease_and_delayed_release(
    engine: Engine, session: Session, worker: ConsensusWorker, alive
):
    """Auditor regression: a still-running task holds a claim whose lease
    expired long ago and its worker processes the release late. The restore
    must wait for that task to finish (including a write in its cleanup), so
    the restored state is what remains afterwards."""
    alive(worker.worker_id)
    state = session.get(CurrentState, CONTRACT)
    state.data = {"value": "snapshot"}
    session.commit()
    manager = SnapshotManager(session)
    snapshot_id = manager.create_snapshot().snapshot_id
    state.data = {"value": "live"}
    session.commit()
    _insert_tx(
        session,
        TX_A,
        nonce=0,
        worker_id=worker.worker_id,
        status="COMMITTING",
        blocked="NOW() - INTERVAL '2 hours'",
    )

    Session_ = sessionmaker(bind=engine)
    running = asyncio.Event()

    async def still_running_task():
        async with worker._transaction_context(TX_A, {}, MagicMock()):
            running.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.sleep(0.2)  # slow cleanup that still writes
                with Session_() as ws:
                    ws.execute(
                        text(
                            "UPDATE current_state SET data = "
                            'CAST(\'{"value": "stale"}\' AS jsonb) WHERE id = :id'
                        ),
                        {"id": CONTRACT},
                    )
                    ws.commit()
                raise

    task = asyncio.create_task(still_running_task())
    worker._active_tasks.add(task)
    await running.wait()

    async def publish_with_delayed_delivery(*_args, **_kwargs):
        async def deliver_late():
            await asyncio.sleep(0.3)
            await worker.release_in_flight()

        asyncio.ensure_future(deliver_late())
        return 1

    with (
        patch.object(
            endpoints.worker_control,
            "worker_control_subscribers",
            AsyncMock(return_value=1),
        ),
        patch.object(
            endpoints.worker_control,
            "publish_worker_control",
            publish_with_delayed_delivery,
        ),
    ):
        assert await endpoints.restore_snapshot(manager, snapshot_id) is True

    assert task.done()
    session.rollback()
    session.expire_all()
    assert session.get(CurrentState, CONTRACT).data == {"value": "snapshot"}
    assert _claim_of(session, TX_A) is None  # not in the snapshot


@pytest.mark.asyncio
async def test_restore_aborts_before_release_event_when_a_running_owner_is_unreachable(
    engine: Engine, session: Session, alive
):
    """Two running claim owners, one control-channel subscriber: one of them
    can't be asked to release, so abort before disturbing anyone."""
    alive("live-worker", "unsubscribed-worker")
    manager = SnapshotManager(session)
    snapshot_id = manager.create_snapshot().snapshot_id
    _insert_tx(session, TX_A, nonce=0, worker_id="live-worker")
    _insert_tx(session, TX_B, nonce=1, worker_id="unsubscribed-worker")

    publish = AsyncMock(return_value=1)
    with (
        patch.object(
            endpoints.worker_control,
            "worker_control_subscribers",
            AsyncMock(return_value=1),
        ),
        patch.object(endpoints.worker_control, "publish_worker_control", publish),
    ):
        with pytest.raises(JSONRPCError) as exc:
            await endpoints.restore_snapshot(manager, snapshot_id)

    publish.assert_not_awaited()
    assert "told to cancel" not in exc.value.message
    assert _claim_of(session, TX_B).worker_id == "unsubscribed-worker"


@pytest.mark.asyncio
async def test_restore_aborts_immediately_when_release_event_cannot_be_sent(
    engine: Engine, session: Session, monkeypatch, alive
):
    monkeypatch.delenv("REDIS_URL", raising=False)
    alive("some-worker")
    manager = SnapshotManager(session)
    snapshot_id = manager.create_snapshot().snapshot_id
    _insert_tx(session, TX_A, nonce=0, worker_id="some-worker")

    with pytest.raises(JSONRPCError):
        await endpoints.restore_snapshot(manager, snapshot_id)

    assert _claim_of(session, TX_A).worker_id == "some-worker"


@pytest.mark.asyncio
async def test_worker_release_in_flight_clears_its_claims(
    engine: Engine, session: Session, worker: ConsensusWorker
):
    _insert_tx(session, TX_A, nonce=0, worker_id=worker.worker_id)
    _insert_tx(session, TX_B, nonce=1, worker_id="other-worker")

    await worker.release_in_flight()

    assert _claim_of(session, TX_A).worker_id is None
    assert _claim_of(session, TX_B).worker_id == "other-worker"
