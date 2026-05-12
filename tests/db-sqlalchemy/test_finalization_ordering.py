"""
Regression tests for the finalization ordering invariant in
`ConsensusWorker.claim_next_finalization`.

Two related invariants pinned here:

1. **Ordering**: a tx can only be claimed for finalization once every
   OLDER tx on the same contract has reached a terminal state
   ({FINALIZED, CANCELED}). Without this, a younger tx N+1 ACCEPTED
   could finalize while older N is still stuck in COMMITTING, locking
   in state changes out of causal order.

2. **NULL-timestamp tolerance**: rows with `timestamp_awaiting_finalization
   IS NULL` past the stranded threshold are eligible. Pre-fix code paths
   (e.g. the May 2026 insufficient-balance SEND short-circuit) reached
   ACCEPTED-class statuses without stamping the timestamp; those rows
   would otherwise sit invisible to the claim query forever.
"""

import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterable
from unittest.mock import MagicMock

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

os.environ.setdefault("VITE_FINALITY_WINDOW", "30")
os.environ.setdefault("VITE_FINALITY_WINDOW_APPEAL_FAILED_REDUCTION", "0.2")

from backend.consensus.worker import ConsensusWorker


CONTRACT = "0x" + "ff" * 20
OTHER_CONTRACT = "0x" + "ee" * 20
SENDER = "0x" + "ab" * 20


def _insert_tx(
    session: Session,
    *,
    tx_hash: str,
    status: str,
    nonce: int,
    to_address: str = CONTRACT,
    created_at: datetime | None = None,
    timestamp_awaiting_finalization: int | None = None,
    blocked_at: datetime | None = None,
    worker_id: str | None = None,
):
    if created_at is None:
        created_at = datetime.now(timezone.utc)
    session.execute(
        text(
            """
            INSERT INTO transactions (
                hash, status, from_address, to_address,
                data, value, type, nonce,
                leader_only, execution_mode,
                appealed, appeal_failed, appeal_undetermined,
                appeal_leader_timeout, appeal_validators_timeout,
                appeal_processing_time,
                timestamp_awaiting_finalization, blocked_at, worker_id,
                created_at, recovery_count, value_credited
            ) VALUES (
                :hash, CAST(:status AS transaction_status), :from_addr, :to_addr,
                CAST('{}' AS jsonb), 0, 2, :nonce,
                false, 'NORMAL',
                false, 0, false,
                false, false,
                0,
                :timestamp_awaiting_finalization, :blocked_at, :worker_id,
                :created_at, 0, false
            )
            """
        ),
        {
            "hash": tx_hash,
            "status": status,
            "from_addr": SENDER,
            "to_addr": to_address,
            "nonce": nonce,
            "timestamp_awaiting_finalization": timestamp_awaiting_finalization,
            "blocked_at": blocked_at,
            "worker_id": worker_id,
            "created_at": created_at,
        },
    )
    session.commit()


@pytest.fixture
def worker(engine: Engine) -> Iterable[ConsensusWorker]:
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def get_session():
        s = Session_()
        try:
            yield s
        finally:
            s.close()

    w = ConsensusWorker(
        get_session=get_session,
        msg_handler=MagicMock(),
        consensus_service=MagicMock(),
        validators_manager=MagicMock(),
        genvm_manager=MagicMock(),
        worker_id="test-worker",
        transaction_timeout_minutes=20,
    )
    yield w


@pytest.fixture
def session(engine: Engine) -> Iterable[Session]:
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session_()
    yield s
    s.rollback()
    s.close()


@pytest.mark.asyncio
async def test_ordering_blocks_younger_when_older_still_in_consensus(
    worker: ConsensusWorker, session: Session
):
    """Older tx still in COMMITTING ⇒ younger ACCEPTED with elapsed
    finality window MUST NOT be claimed for finalization. This is the
    causal-ordering invariant."""
    now = datetime.now(timezone.utc)

    _insert_tx(
        session,
        tx_hash="0x" + "10" * 32,
        status="COMMITTING",
        nonce=0,
        created_at=now - timedelta(minutes=10),
    )
    _insert_tx(
        session,
        tx_hash="0x" + "11" * 32,
        status="ACCEPTED",
        nonce=1,
        created_at=now - timedelta(minutes=5),
        timestamp_awaiting_finalization=int(time.time()) - 600,  # window long elapsed
    )

    with worker.get_session() as s:
        result = await worker.claim_next_finalization(s)

    assert result is None, (
        "Younger ACCEPTED tx must NOT be finalized while older tx is "
        f"still in COMMITTING. Got: {result}"
    )


@pytest.mark.asyncio
async def test_ordering_allows_younger_after_older_finalized(
    worker: ConsensusWorker, session: Session
):
    """Older tx FINALIZED ⇒ younger ACCEPTED becomes eligible. The
    cascade behaviour: once the head is done, the next-oldest is up."""
    now = datetime.now(timezone.utc)

    _insert_tx(
        session,
        tx_hash="0x" + "20" * 32,
        status="FINALIZED",
        nonce=0,
        created_at=now - timedelta(minutes=10),
    )
    _insert_tx(
        session,
        tx_hash="0x" + "21" * 32,
        status="ACCEPTED",
        nonce=1,
        created_at=now - timedelta(minutes=5),
        timestamp_awaiting_finalization=int(time.time()) - 600,
    )

    with worker.get_session() as s:
        result = await worker.claim_next_finalization(s)

    assert result is not None and result["hash"] == "0x" + "21" * 32, (
        "Younger ACCEPTED tx must be eligible when the only older tx on "
        f"the contract is FINALIZED. Got: {result}"
    )


@pytest.mark.asyncio
async def test_ordering_treats_canceled_as_terminal(
    worker: ConsensusWorker, session: Session
):
    """CANCELED counts as terminal too — the queue should drain past it."""
    now = datetime.now(timezone.utc)

    _insert_tx(
        session,
        tx_hash="0x" + "30" * 32,
        status="CANCELED",
        nonce=0,
        created_at=now - timedelta(minutes=10),
    )
    _insert_tx(
        session,
        tx_hash="0x" + "31" * 32,
        status="ACCEPTED",
        nonce=1,
        created_at=now - timedelta(minutes=5),
        timestamp_awaiting_finalization=int(time.time()) - 600,
    )

    with worker.get_session() as s:
        result = await worker.claim_next_finalization(s)

    assert result is not None and result["hash"] == "0x" + "31" * 32, (
        "CANCELED counts as terminal; younger tx should be eligible. " f"Got: {result}"
    )


@pytest.mark.asyncio
async def test_null_timestamp_stranded_row_is_claimed_when_head(
    worker: ConsensusWorker, session: Session
):
    """The defensive arm: a NULL-timestamp row past the stranded threshold
    IS eligible when it's the head of its contract. Drains the May 2026
    insufficient-balance SEND legacy rows without a backfill."""
    now = datetime.now(timezone.utc)

    _insert_tx(
        session,
        tx_hash="0x" + "40" * 32,
        status="UNDETERMINED",
        nonce=0,
        created_at=now - timedelta(hours=2),
        timestamp_awaiting_finalization=None,  # stranded
    )

    with worker.get_session() as s:
        result = await worker.claim_next_finalization(s)

    assert result is not None and result["hash"] == "0x" + "40" * 32, (
        "Stranded NULL-timestamp head must drain via defensive eligibility. "
        f"Got: {result}"
    )


@pytest.mark.asyncio
async def test_null_timestamp_stranded_row_blocked_by_older(
    worker: ConsensusWorker, session: Session
):
    """Stranded row that ISN'T the head waits behind the older non-final
    tx — same ordering invariant applies."""
    now = datetime.now(timezone.utc)

    _insert_tx(
        session,
        tx_hash="0x" + "50" * 32,
        status="COMMITTING",
        nonce=0,
        created_at=now - timedelta(hours=3),  # older, still in consensus
    )
    _insert_tx(
        session,
        tx_hash="0x" + "51" * 32,
        status="UNDETERMINED",
        nonce=1,
        created_at=now - timedelta(hours=2),
        timestamp_awaiting_finalization=None,
    )

    with worker.get_session() as s:
        result = await worker.claim_next_finalization(s)

    assert result is None, (
        "Stranded UNDETERMINED behind an older non-final tx must wait — "
        f"ordering invariant applies even on the defensive eligibility branch. Got: {result}"
    )


@pytest.mark.asyncio
async def test_null_timestamp_recent_row_not_yet_eligible(
    worker: ConsensusWorker, session: Session
):
    """NULL timestamp + row younger than threshold ⇒ NOT yet claimed.
    Prevents premature finalization of rows still in the active path."""
    now = datetime.now(timezone.utc)

    _insert_tx(
        session,
        tx_hash="0x" + "60" * 32,
        status="UNDETERMINED",
        nonce=0,
        created_at=now - timedelta(seconds=30),  # well under threshold
        timestamp_awaiting_finalization=None,
    )

    with worker.get_session() as s:
        result = await worker.claim_next_finalization(s)

    assert result is None, (
        "Recent NULL-timestamp row must not be claimed before the stranded "
        f"threshold elapses. Got: {result}"
    )


@pytest.mark.asyncio
async def test_ordering_is_per_contract(worker: ConsensusWorker, session: Session):
    """The ordering invariant is per-contract — a non-terminal tx on a
    different contract does NOT block this one."""
    now = datetime.now(timezone.utc)

    # Older non-terminal tx on a DIFFERENT contract
    _insert_tx(
        session,
        tx_hash="0x" + "70" * 32,
        status="COMMITTING",
        nonce=0,
        to_address=OTHER_CONTRACT,
        created_at=now - timedelta(minutes=10),
    )
    # Younger ACCEPTED on the target contract; no older tx on the same contract
    _insert_tx(
        session,
        tx_hash="0x" + "71" * 32,
        status="ACCEPTED",
        nonce=0,
        to_address=CONTRACT,
        created_at=now - timedelta(minutes=5),
        timestamp_awaiting_finalization=int(time.time()) - 600,
    )

    with worker.get_session() as s:
        result = await worker.claim_next_finalization(s)

    assert result is not None and result["hash"] == "0x" + "71" * 32, (
        "Ordering must be per-contract; other contracts' state shouldn't matter. "
        f"Got: {result}"
    )
