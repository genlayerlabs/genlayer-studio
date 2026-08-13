"""
Regression tests for concurrent contract-state updates.

Contract execution produces a complete replacement for ``accepted_state``.
Workers must therefore serialize the entire read/execute/write attempt for a
contract; locking only the final database write is too late to distinguish an
intentional deletion from a stale snapshot.

This is the root cause of 336+ lost submissions in Rally production (March 2026).
See: Rally2/docs/genvm-state-mismatch-bug.md

Production scenario:
  - Worker A accepts TX-A → writes accepted_state with TX-A's submission
  - Worker B accepts TX-B → reads the SAME pre-TX-A state → writes accepted_state
    with TX-B's submission → TX-A's submission is silently erased
"""

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from backend.consensus.worker import ConsensusWorker
from backend.database_handler.contract_processor import ContractProcessor
from backend.database_handler.models import CurrentState, Transactions
from backend.database_handler.transactions_processor import TransactionsProcessor


CONTRACT_ADDRESS = "0xrace_test_contract"

INITIAL_STATE = {
    "accepted": {"slot_a": "original_a"},
    "finalized": {"slot_f": "original_f"},
}


def _setup_contract(engine: Engine):
    """Insert a contract with initial state."""
    Session_ = sessionmaker(bind=engine)
    with Session_() as s:
        contract = CurrentState(
            id=CONTRACT_ADDRESS,
            data={"state": INITIAL_STATE},
        )
        s.add(contract)
        s.commit()


def _read_state(engine: Engine) -> dict:
    """Read the current contract state from a fresh session."""
    Session_ = sessionmaker(bind=engine)
    with Session_() as s:
        row = s.query(CurrentState).filter_by(id=CONTRACT_ADDRESS).one()
        return row.data["state"]


def _make_lock_worker(Session_, worker_id: str) -> ConsensusWorker:
    """Build the worker fields used by processing/claim lock tests."""
    worker = ConsensusWorker.__new__(ConsensusWorker)
    worker.worker_id = worker_id
    worker.get_session = Session_
    worker.current_transactions = {}
    worker.release_transaction = MagicMock()
    return worker


# ---------------------------------------------------------------------------
# Test 1: Worker processing locks serialize read + accepted-state replacement
# ---------------------------------------------------------------------------


def test_concurrent_accepted_updates_preserve_both(engine: Engine):
    """
    A second worker attempting the same contract must wait before reading
    state, even when it was already claimed (for example after claim expiry).

    Without the processing-lifetime advisory lock, both workers read the
    original state and one complete accepted-state replacement is lost.
    """
    _setup_contract(engine)

    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    worker_a = _make_lock_worker(Session_, "state-writer-a")
    worker_b = _make_lock_worker(Session_, "state-writer-b")
    # These synthetic transaction hashes do not have transaction rows to
    # release; the test is scoped to the processing lock and state write.

    a_has_read = threading.Event()
    b_is_attempting = threading.Event()
    b_has_read = threading.Event()
    errors = []

    def run_writer(worker, submission: str, tx_hash: str):
        try:

            async def process():
                if submission == "submission_B":
                    if not a_has_read.wait(timeout=5):
                        errors.append(
                            ("B", RuntimeError("worker A did not read state"))
                        )
                        return
                    b_is_attempting.set()

                with Session_() as session:
                    async with worker._transaction_context(
                        tx_hash,
                        {"to_address": CONTRACT_ADDRESS},
                        session,
                    ):
                        contract = (
                            session.query(CurrentState)
                            .filter_by(id=CONTRACT_ADDRESS)
                            .one()
                        )
                        accepted = dict(contract.data["state"]["accepted"])

                        if submission == "submission_A":
                            a_has_read.set()
                            if not b_is_attempting.wait(timeout=5):
                                errors.append(
                                    ("A", RuntimeError("worker B did not attempt lock"))
                                )
                                return
                            # If the processing lock is removed, B enters and
                            # reads the same stale state before A writes.
                            if b_has_read.wait(timeout=0.5):
                                errors.append(
                                    (
                                        "lock",
                                        RuntimeError(
                                            "worker B read state while worker A "
                                            "still held the contract lock"
                                        ),
                                    )
                                )
                                return
                        else:
                            b_has_read.set()

                        accepted[submission] = "scored"
                        ContractProcessor(session).update_contract_state(
                            CONTRACT_ADDRESS,
                            accepted_state=accepted,
                        )

            asyncio.run(process())
        except Exception as e:
            errors.append((submission, e))

    t_a = threading.Thread(
        target=run_writer,
        args=(worker_a, "submission_A", "0xstate-writer-a"),
    )
    t_b = threading.Thread(
        target=run_writer,
        args=(worker_b, "submission_B", "0xstate-writer-b"),
    )
    t_a.start()
    t_b.start()
    t_a.join(timeout=10)
    t_b.join(timeout=10)

    assert not t_a.is_alive() and not t_b.is_alive(), "worker threads did not finish"
    assert not errors, f"Worker errors: {errors}"

    state = _read_state(engine)

    has_a = "submission_A" in state["accepted"]
    has_b = "submission_B" in state["accepted"]

    assert has_a and has_b, (
        f"Lost update: concurrent accepted_state writes must both survive. "
        f"has_A={has_a}, has_B={has_b}, state={state['accepted']}"
    )


# ---------------------------------------------------------------------------
# Test 2: Claim lease expiry cannot bypass the processing lock
# ---------------------------------------------------------------------------


def test_processing_lock_prevents_reclaim_after_lease_expiry(engine: Engine):
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    transaction_hash = "0x" + "ab" * 32

    with Session_() as session:
        TransactionsProcessor(session).insert_transaction(
            from_address="0x" + "11" * 20,
            to_address=CONTRACT_ADDRESS,
            data={},
            value=0,
            type=2,
            nonce=0,
            leader_only=True,
            config_rotation_rounds=0,
            transaction_hash=transaction_hash,
        )
        transaction = session.query(Transactions).filter_by(hash=transaction_hash).one()
        transaction.worker_id = "expired-worker"
        transaction.blocked_at = datetime.now(timezone.utc) - timedelta(hours=1)
        session.commit()

    lock_owner = _make_lock_worker(Session_, "lock-owner")
    claimer = _make_lock_worker(Session_, "new-worker")
    claimer.transaction_timeout_minutes = 20
    claimer.consensus_algorithm = MagicMock(
        finality_window_time=10,
        finality_window_appeal_failed_reduction=0,
    )
    claimer._log_query_result = MagicMock()

    with Session_() as owner_session:
        lock_connection = asyncio.run(
            lock_owner._acquire_contract_processing_lock(
                owner_session, CONTRACT_ADDRESS
            )
        )
        try:
            with Session_() as claim_session:
                assert (
                    asyncio.run(claimer.claim_next_transaction(claim_session)) is None
                )
        finally:
            lock_owner._release_contract_processing_lock(
                lock_connection, CONTRACT_ADDRESS
            )

    with Session_() as claim_session:
        claimed = asyncio.run(claimer.claim_next_transaction(claim_session))

    assert claimed is not None
    assert claimed["hash"] == transaction_hash


# ---------------------------------------------------------------------------
# Test 3: accepted + finalized concurrent updates — must both survive
# ---------------------------------------------------------------------------


def test_concurrent_accepted_and_finalized_preserve_both(engine: Engine):
    """
    Worker A writes accepted_state, Worker B writes finalized_state concurrently.

    CORRECT behavior: both fields must reflect their respective updates.
    This test FAILS until the cross-field clobber bug is fixed.
    """
    _setup_contract(engine)

    barrier = threading.Barrier(2, timeout=5)
    errors = []

    def writer_accepted():
        try:
            Session_ = sessionmaker(bind=engine)
            with Session_() as s:
                cp = ContractProcessor(s)
                contract = s.query(CurrentState).filter_by(id=CONTRACT_ADDRESS).one()
                _ = contract.data
                barrier.wait()
                cp.update_contract_state(
                    CONTRACT_ADDRESS,
                    accepted_state={"slot_a": "updated_by_accepted_writer"},
                )
        except Exception as e:
            errors.append(("accepted", e))

    def writer_finalized():
        try:
            Session_ = sessionmaker(bind=engine)
            with Session_() as s:
                cp = ContractProcessor(s)
                contract = s.query(CurrentState).filter_by(id=CONTRACT_ADDRESS).one()
                _ = contract.data
                barrier.wait()
                cp.update_contract_state(
                    CONTRACT_ADDRESS,
                    finalized_state={"slot_f": "updated_by_finalized_writer"},
                )
        except Exception as e:
            errors.append(("finalized", e))

    t1 = threading.Thread(target=writer_accepted)
    t2 = threading.Thread(target=writer_finalized)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not errors, f"Worker errors: {errors}"

    state = _read_state(engine)

    accepted_updated = state["accepted"].get("slot_a") == "updated_by_accepted_writer"
    finalized_updated = (
        state["finalized"].get("slot_f") == "updated_by_finalized_writer"
    )

    assert accepted_updated and finalized_updated, (
        f"Cross-field clobber: concurrent accepted + finalized writes must both survive. "
        f"accepted={state['accepted']}, finalized={state['finalized']}"
    )


# ---------------------------------------------------------------------------
# Test 4: Sequential updates — sanity check (should always pass)
# ---------------------------------------------------------------------------


def test_sequential_updates_preserve_all_state(engine: Engine):
    """
    Baseline: sequential updates don't lose data.
    This should always pass regardless of the bug.
    """
    _setup_contract(engine)

    Session_ = sessionmaker(bind=engine)

    with Session_() as s:
        cp = ContractProcessor(s)
        cp.update_contract_state(
            CONTRACT_ADDRESS,
            accepted_state={"slot_a": "original_a", "submission_A": "scored"},
        )

    with Session_() as s:
        cp = ContractProcessor(s)
        cp.update_contract_state(
            CONTRACT_ADDRESS,
            accepted_state={
                "slot_a": "original_a",
                "submission_A": "scored",
                "submission_B": "scored",
            },
        )

    state = _read_state(engine)
    assert state["accepted"]["submission_A"] == "scored"
    assert state["accepted"]["submission_B"] == "scored"
    assert state["finalized"] == {"slot_f": "original_f"}
