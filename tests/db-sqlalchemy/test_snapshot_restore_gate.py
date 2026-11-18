"""
DXP-726: restoring a snapshot must not race with consensus workers.

Real-Postgres tests for the restore gate (advisory lock) that blocks worker
claims during a restore, and for aborting the restore when workers don't let
go of their claims.
"""

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
    engine: Engine, session: Session, monkeypatch
):
    monkeypatch.setenv("SNAPSHOT_RESTORE_RELEASE_TIMEOUT_SECONDS", "0.3")
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
async def test_claims_no_live_worker_can_act_on_do_not_block_restore(
    engine: Engine, session: Session, monkeypatch
):
    monkeypatch.setenv("TRANSACTION_TIMEOUT_MINUTES", "30")
    manager = SnapshotManager(session)
    _insert_tx(session, TX_A, nonce=0)
    snapshot_id = manager.create_snapshot().snapshot_id
    # Lease expired, no progress since: recovery treats it as abandoned.
    _insert_tx(
        session,
        TX_B,
        nonce=1,
        worker_id="dead-worker",
        blocked="NOW() - INTERVAL '2 hours'",
    )
    # Terminal: recovery never touches it.
    _insert_tx(session, TX_C, nonce=2, worker_id="dead-worker", status="FINALIZED")

    assert worker_control.claimed_transactions(session) == []
    publish = AsyncMock(return_value=0)
    with patch.object(endpoints.worker_control, "publish_worker_control", publish):
        assert await endpoints.restore_snapshot(manager, snapshot_id) is True

    assert _claim_of(session, TX_A).worker_id is None
    assert _claim_of(session, TX_B) is None


@pytest.mark.asyncio
async def test_expired_lease_with_recent_progress_still_counts_as_claimed(
    engine: Engine, session: Session, monkeypatch
):
    monkeypatch.setenv("TRANSACTION_TIMEOUT_MINUTES", "30")
    _insert_tx(
        session,
        TX_A,
        nonce=0,
        worker_id="slow-worker",
        status="PROPOSING",
        blocked="NOW() - INTERVAL '2 hours'",
        consensus_history=(
            "jsonb_build_object('current_monitoring', jsonb_build_object("
            "'PROPOSING', EXTRACT(EPOCH FROM NOW())::double precision))"
        ),
    )

    assert worker_control.claimed_transactions(session) == [(TX_A, "slow-worker")]


@pytest.mark.asyncio
async def test_restore_aborts_before_release_event_when_claim_owner_is_gone(
    engine: Engine, session: Session
):
    manager = SnapshotManager(session)
    snapshot_id = manager.create_snapshot().snapshot_id
    _insert_tx(session, TX_A, nonce=0, worker_id="live-worker")
    _insert_tx(session, TX_B, nonce=1, worker_id="dead-worker")

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
    assert _claim_of(session, TX_B).worker_id == "dead-worker"


@pytest.mark.asyncio
async def test_restore_aborts_immediately_when_release_event_cannot_be_sent(
    engine: Engine, session: Session, monkeypatch
):
    monkeypatch.delenv("REDIS_URL", raising=False)
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
