import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.consensus.worker import ConsensusWorker


def _make_worker(session: Session, worker_id: str = "worker-a") -> ConsensusWorker:
    return ConsensusWorker(
        get_session=lambda: session,
        msg_handler=MagicMock(),
        consensus_service=MagicMock(),
        validators_manager=MagicMock(),
        genvm_manager=MagicMock(),
        worker_id=worker_id,
    )


def _insert_tx(
    session: Session,
    *,
    tx_hash: str,
    status: str,
    worker_id: str | None,
    blocked_at: datetime | None,
    recovery_count: int = 0,
    consensus_history: dict | None = None,
    consensus_data: dict | None = None,
):
    session.execute(
        text(
            """
            INSERT INTO transactions (
                hash, status, from_address, to_address, data, value, type,
                nonce, leader_only, execution_mode, appealed, appeal_failed,
                appeal_undetermined, appeal_leader_timeout,
                appeal_validators_timeout, appeal_processing_time,
                recovery_count, value_credited,
                consensus_history, consensus_data,
                created_at, blocked_at, worker_id
            ) VALUES (
                :hash, CAST(:status AS transaction_status),
                '0xfromaddress', '0x1111111111111111111111111111111111111111',
                CAST('{}' AS jsonb), 0, 2,
                1, false, 'NORMAL', false, 0,
                false, false, false, 0, :recovery_count, false,
                CAST(:consensus_history AS jsonb), CAST(:consensus_data AS jsonb),
                :created_at, :blocked_at, :worker_id
            )
            """
        ),
        {
            "hash": tx_hash,
            "status": status,
            "recovery_count": recovery_count,
            "consensus_history": (
                json.dumps(consensus_history) if consensus_history is not None else None
            ),
            "consensus_data": (
                json.dumps(consensus_data) if consensus_data is not None else None
            ),
            "created_at": datetime.now(timezone.utc) - timedelta(minutes=30),
            "blocked_at": blocked_at,
            "worker_id": worker_id,
        },
    )


def _get_tx(session: Session, tx_hash: str):
    return session.execute(
        text(
            """
            SELECT hash, status::text AS status, worker_id, blocked_at,
                   recovery_count, consensus_history, consensus_data
            FROM transactions
            WHERE hash = :hash
            """
        ),
        {"hash": tx_hash},
    ).one()


def test_shutdown_cleanup_resets_owned_consensus_claims(session: Session):
    now = datetime.now(timezone.utc)
    owned_hash = "0x" + "01" * 32
    tracked_hash = "0x" + "02" * 32
    other_worker_hash = "0x" + "03" * 32

    _insert_tx(
        session,
        tx_hash=owned_hash,
        status="COMMITTING",
        worker_id="worker-a",
        blocked_at=now,
        consensus_history={"current_monitoring": {"COMMITTING": 1}},
        consensus_data={"partial": True},
    )
    _insert_tx(
        session,
        tx_hash=tracked_hash,
        status="REVEALING",
        worker_id=None,
        blocked_at=None,
        recovery_count=1,
        consensus_history={"current_monitoring": {"REVEALING": 2}},
        consensus_data={"partial": True},
    )
    _insert_tx(
        session,
        tx_hash=other_worker_hash,
        status="COMMITTING",
        worker_id="worker-b",
        blocked_at=now,
        consensus_history={"current_monitoring": {"COMMITTING": 3}},
        consensus_data={"partial": True},
    )
    session.commit()

    worker = _make_worker(session)

    summary = worker.abandon_owned_claims_for_shutdown(
        session, tracked_hashes=[tracked_hash]
    )

    assert summary == {"released": 0, "reset": 2}

    owned = _get_tx(session, owned_hash)
    assert owned.status == "PENDING"
    assert owned.worker_id is None
    assert owned.blocked_at is None
    assert owned.recovery_count == 1
    assert owned.consensus_history is None
    assert owned.consensus_data is None

    tracked = _get_tx(session, tracked_hash)
    assert tracked.status == "PENDING"
    assert tracked.worker_id is None
    assert tracked.recovery_count == 2
    assert tracked.consensus_history is None
    assert tracked.consensus_data is None

    other = _get_tx(session, other_worker_hash)
    assert other.status == "COMMITTING"
    assert other.worker_id == "worker-b"
    assert other.blocked_at is not None
    assert other.recovery_count == 0


def test_shutdown_cleanup_preserves_finalization_state(session: Session):
    now = datetime.now(timezone.utc)
    owned_hash = "0x" + "11" * 32
    tracked_hash = "0x" + "12" * 32
    other_worker_hash = "0x" + "13" * 32

    _insert_tx(
        session,
        tx_hash=owned_hash,
        status="ACCEPTED",
        worker_id="worker-a",
        blocked_at=now,
        consensus_history={"current_monitoring": {"ACCEPTED": 1}},
        consensus_data={"receipt": "kept"},
    )
    _insert_tx(
        session,
        tx_hash=tracked_hash,
        status="UNDETERMINED",
        worker_id=None,
        blocked_at=None,
        consensus_history={"current_monitoring": {"UNDETERMINED": 2}},
        consensus_data={"receipt": "also-kept"},
    )
    _insert_tx(
        session,
        tx_hash=other_worker_hash,
        status="LEADER_TIMEOUT",
        worker_id="worker-b",
        blocked_at=now,
        consensus_history={"current_monitoring": {"LEADER_TIMEOUT": 3}},
        consensus_data={"receipt": "other"},
    )
    session.commit()

    worker = _make_worker(session)

    summary = worker.abandon_owned_claims_for_shutdown(
        session, tracked_hashes=[tracked_hash]
    )

    assert summary == {"released": 2, "reset": 0}

    owned = _get_tx(session, owned_hash)
    assert owned.status == "ACCEPTED"
    assert owned.worker_id is None
    assert owned.blocked_at is None
    assert owned.consensus_history == {"current_monitoring": {"ACCEPTED": 1}}
    assert owned.consensus_data == {"receipt": "kept"}

    tracked = _get_tx(session, tracked_hash)
    assert tracked.status == "UNDETERMINED"
    assert tracked.worker_id is None
    assert tracked.consensus_history == {"current_monitoring": {"UNDETERMINED": 2}}
    assert tracked.consensus_data == {"receipt": "also-kept"}

    other = _get_tx(session, other_worker_hash)
    assert other.status == "LEADER_TIMEOUT"
    assert other.worker_id == "worker-b"
    assert other.blocked_at is not None
    assert other.consensus_data == {"receipt": "other"}


def test_shutdown_cleanup_escalates_repeatedly_reset_claims(session: Session):
    tx_hash = "0x" + "21" * 32
    _insert_tx(
        session,
        tx_hash=tx_hash,
        status="COMMITTING",
        worker_id="worker-a",
        blocked_at=datetime.now(timezone.utc),
        recovery_count=2,
        consensus_history={"current_monitoring": {"COMMITTING": 1}},
        consensus_data={"partial": True},
    )
    session.commit()

    worker = _make_worker(session)

    summary = worker.abandon_owned_claims_for_shutdown(session)

    assert summary == {"released": 0, "reset": 1}
    row = _get_tx(session, tx_hash)
    assert row.status == "CANCELED"
    assert row.worker_id is None
    assert row.blocked_at is None
    assert row.recovery_count == 3
    assert row.consensus_history is None
    assert row.consensus_data["error"] == "max_recovery_cycles_exceeded"
