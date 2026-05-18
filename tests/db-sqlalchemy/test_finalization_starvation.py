"""
Regression tests for finalization-starvation in ConsensusWorker scheduling.

Bug history (May 2026): on contracts with continuous PENDING inflow, ACCEPTED
txs sat 5+ days waiting for finalization. Root cause: claim_next_transaction
and claim_next_finalization shared per-contract serialization (NOT EXISTS +
advisory lock). With ~16 PENDINGs/hr arriving and consensus taking ~3 min,
PENDING-processing continuously held the slot — finalization windows of 30s
rarely opened. Compounded by recover_stuck_transactions wiping consensus_data
for ACCEPTED txs whose finalization worker died (treating them as stuck
consensus).

These tests cover:
1. claim_next_transaction defers when an eligible finalization exists.
2. claim_next_transaction proceeds when finalization for the contract is
   already in flight (preserves liveness — no infinite defer loop).
3. recover_stuck_transactions preserves consensus_data for finalization-
   eligible statuses (ACCEPTED/UNDETERMINED/*_TIMEOUT).
4. Safety-net log fires when an ACCEPTED tx's wait exceeds N×finality_window.
"""

import time
import os
import pytest
from unittest.mock import MagicMock
from sqlalchemy import Engine, text
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager
from typing import Iterable

# Ensure consensus env vars are set before ConsensusWorker imports
# ConsensusAlgorithm (which reads them at __init__).
os.environ.setdefault("VITE_FINALITY_WINDOW", "30")
os.environ.setdefault("VITE_FINALITY_WINDOW_APPEAL_FAILED_REDUCTION", "0.2")

from backend.database_handler.models import CurrentState, TransactionStatus
from backend.consensus.worker import ConsensusWorker


CONTRACT_ADDRESS = "0xfinalization_starvation_test_contract"
SENDER = "0x" + "ab" * 20


def _setup_contract(engine: Engine):
    Session_ = sessionmaker(bind=engine)
    with Session_() as s:
        s.add(
            CurrentState(
                id=CONTRACT_ADDRESS,
                data={
                    "state": {
                        "accepted": {"slot": "init"},
                        "finalized": {"slot": "init"},
                    }
                },
            )
        )
        s.commit()


def _insert_tx(
    session: Session,
    *,
    tx_hash: str,
    status: str,
    nonce: int,
    to_address: str = CONTRACT_ADDRESS,
    timestamp_awaiting_finalization: int | None = None,
    consensus_data: dict | None = None,
    consensus_history: dict | None = None,
    blocked_at_offset_seconds: int | None = None,  # negative = past
):
    """Insert a transaction directly, bypassing TransactionsProcessor's
    PENDING-default and other defaults — we want full control over the row
    state for these scheduling tests."""
    blocked_at_clause = "NULL"
    if blocked_at_offset_seconds is not None:
        blocked_at_clause = f"NOW() + INTERVAL '{blocked_at_offset_seconds} seconds'"
    session.execute(
        text(
            f"""
            INSERT INTO transactions (
                hash, status, from_address, to_address,
                data, value, type, nonce,
                leader_only, execution_mode,
                appealed, appeal_failed, appeal_undetermined,
                appeal_leader_timeout, appeal_validators_timeout,
                appeal_processing_time,
                timestamp_awaiting_finalization,
                consensus_data, consensus_history,
                blocked_at, recovery_count, value_credited
            ) VALUES (
                :hash, CAST(:status AS transaction_status), :from_addr, :to_addr,
                CAST(:data AS jsonb), 0, 2, :nonce,
                false, 'NORMAL',
                false, 0, false,
                false, false,
                0,
                :timestamp_awaiting_finalization,
                CAST(:consensus_data AS jsonb), CAST(:consensus_history AS jsonb),
                {blocked_at_clause}, 0, false
            )
            """
        ),
        {
            "hash": tx_hash,
            "status": status,
            "from_addr": SENDER,
            "to_addr": to_address,
            "data": '{"calldata": "test"}',
            "nonce": nonce,
            "timestamp_awaiting_finalization": timestamp_awaiting_finalization,
            "consensus_data": (
                None
                if consensus_data is None
                else __import__("json").dumps(consensus_data)
            ),
            "consensus_history": (
                None
                if consensus_history is None
                else __import__("json").dumps(consensus_history)
            ),
        },
    )
    session.commit()


@pytest.fixture
def worker(engine: Engine) -> Iterable[ConsensusWorker]:
    """Minimal ConsensusWorker wired to the test DB. Uses MagicMock for
    everything that's not exercised by claim/recover paths."""
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
    s.close()


@pytest.mark.asyncio
async def test_claim_next_transaction_defers_to_eligible_finalization(
    engine: Engine, worker: ConsensusWorker, session: Session
):
    """A PENDING tx is NOT claimed when an eligible finalization exists for
    the same contract — this is the fix for finalization starvation."""
    _setup_contract(engine)

    # Eligible finalization: ACCEPTED, NULL blocked_at, finality window past.
    _insert_tx(
        session,
        tx_hash="0x" + "11" * 32,
        status="ACCEPTED",
        nonce=0,
        timestamp_awaiting_finalization=int(time.time()) - 600,  # 10 min ago
        consensus_data={
            "votes": {},
            "leader_receipt": [{"vote": "agree"}],
            "validators": [],
        },
    )
    # Newer PENDING for same contract.
    _insert_tx(session, tx_hash="0x" + "22" * 32, status="PENDING", nonce=1)

    claimed = await worker.claim_next_transaction(session)

    assert claimed is None, (
        "claim_next_transaction must defer to eligible finalization on the "
        "same contract — got "
        f"{claimed}"
    )


@pytest.mark.asyncio
async def test_claim_next_transaction_proceeds_when_no_eligible_finalization(
    engine: Engine, worker: ConsensusWorker, session: Session
):
    """Without an eligible finalization, claim_next_transaction works
    normally. Sanity check that the new gate doesn't break the happy path."""
    _setup_contract(engine)
    _insert_tx(session, tx_hash="0x" + "33" * 32, status="PENDING", nonce=0)

    claimed = await worker.claim_next_transaction(session)

    assert claimed is not None
    assert claimed["hash"] == "0x" + "33" * 32


@pytest.mark.asyncio
async def test_claim_next_transaction_proceeds_when_finalization_window_not_yet_elapsed(
    engine: Engine, worker: ConsensusWorker, session: Session
):
    """If an ACCEPTED tx exists but its finality window hasn't elapsed yet,
    PENDING claims should still proceed — only ELIGIBLE finalizations gate."""
    _setup_contract(engine)
    # ACCEPTED tx that JUST became eligible-for-waiting; window not yet up
    # (default VITE_FINALITY_WINDOW=30s; set timestamp to 1s ago).
    _insert_tx(
        session,
        tx_hash="0x" + "44" * 32,
        status="ACCEPTED",
        nonce=0,
        timestamp_awaiting_finalization=int(time.time()) - 1,
        consensus_data={"leader_receipt": [{}], "votes": {}, "validators": []},
    )
    _insert_tx(session, tx_hash="0x" + "55" * 32, status="PENDING", nonce=1)

    claimed = await worker.claim_next_transaction(session)

    assert claimed is not None
    assert claimed["hash"] == "0x" + "55" * 32


@pytest.mark.asyncio
async def test_recover_does_not_wipe_consensus_data_for_accepted(
    engine: Engine, worker: ConsensusWorker, session: Session
):
    """When an ACCEPTED tx's blocked_at expires (worker died mid-
    finalization), recovery must release blocked_at WITHOUT wiping
    consensus_data — that state is what finalization promotes."""
    _setup_contract(engine)
    consensus_data = {
        "votes": {"0xabc": "agree"},
        "leader_receipt": [{"vote": "agree", "execution_result": "SUCCESS"}],
        "validators": [],
    }
    consensus_history = {"consensus_results": [{"monitoring": {"PENDING": 123}}]}
    tx_hash = "0x" + "66" * 32

    # blocked_at 25 minutes ago (worker.transaction_timeout_minutes=20)
    _insert_tx(
        session,
        tx_hash=tx_hash,
        status="ACCEPTED",
        nonce=0,
        timestamp_awaiting_finalization=int(time.time()) - 600,
        consensus_data=consensus_data,
        consensus_history=consensus_history,
        blocked_at_offset_seconds=-25 * 60,
    )

    await worker.recover_stuck_transactions(session)

    row = session.execute(
        text(
            "SELECT status, blocked_at, worker_id, consensus_data, consensus_history "
            "FROM transactions WHERE hash = :h"
        ),
        {"h": tx_hash},
    ).one()
    assert row.status == TransactionStatus.ACCEPTED.value
    assert row.blocked_at is None
    assert row.worker_id is None
    assert row.consensus_data == consensus_data, (
        "Recovery must NOT wipe consensus_data for ACCEPTED-class txs — that "
        "state is what FinalizingState promotes to FINALIZED."
    )
    assert row.consensus_history == consensus_history


@pytest.mark.asyncio
async def test_recover_still_resets_stuck_consensus_txs(
    engine: Engine, worker: ConsensusWorker, session: Session
):
    """A tx genuinely stuck in PROPOSING/COMMITTING/REVEALING with expired
    blocked_at must still be reset to PENDING. The new restriction must
    not break consensus-recovery."""
    _setup_contract(engine)
    tx_hash = "0x" + "77" * 32

    _insert_tx(
        session,
        tx_hash=tx_hash,
        status="COMMITTING",
        nonce=0,
        consensus_data={"leader_receipt": [{}]},
        consensus_history={"consensus_results": [{}]},
        blocked_at_offset_seconds=-25 * 60,
    )

    await worker.recover_stuck_transactions(session)

    row = session.execute(
        text(
            "SELECT status, recovery_count, consensus_data, consensus_history "
            "FROM transactions WHERE hash = :h"
        ),
        {"h": tx_hash},
    ).one()
    assert row.status == TransactionStatus.PENDING.value
    assert row.recovery_count == 1
    assert row.consensus_data is None
    assert row.consensus_history is None


@pytest.mark.asyncio
async def test_safety_net_warns_on_long_stuck_finalization(
    engine: Engine, worker: ConsensusWorker, session: Session, caplog
):
    """An ACCEPTED tx with NULL blocked_at and a timestamp older than
    N×finality_window must emit a warning log so we catch starvation
    early instead of going days unnoticed."""
    import logging

    _setup_contract(engine)
    tx_hash = "0x" + "88" * 32
    # finality_window=30s, multiplier=10 → threshold 300s. Use 600s.
    _insert_tx(
        session,
        tx_hash=tx_hash,
        status="ACCEPTED",
        nonce=0,
        timestamp_awaiting_finalization=int(time.time()) - 600,
        consensus_data={"leader_receipt": [{}]},
    )

    # loguru → standard logging bridge for caplog
    from loguru import logger as loguru_logger

    handler_id = loguru_logger.add(
        lambda msg: logging.getLogger("loguru").warning(msg.record["message"]),
        level="WARNING",
    )
    try:
        with caplog.at_level(logging.WARNING, logger="loguru"):
            await worker.recover_stuck_transactions(session)
    finally:
        loguru_logger.remove(handler_id)

    assert any(
        tx_hash in record.getMessage() and "starvation" in record.getMessage().lower()
        for record in caplog.records
    ), f"Expected starvation warning for {tx_hash}; got {[r.getMessage() for r in caplog.records]}"
