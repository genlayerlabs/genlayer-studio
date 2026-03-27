"""
Regression test for the lost update bug in ContractProcessor.update_contract_state().

The bug: update_contract_state() does a read-modify-write on the full JSONB blob.
When two sessions call it concurrently for the same contract, the second commit
silently overwrites the first's changes because it re-reads stale state.

This is the root cause of 336+ lost submissions in Rally production (March 2026).
See: Rally2/docs/genvm-state-mismatch-bug.md

Production scenario:
  - Worker A accepts TX-A → writes accepted_state with TX-A's submission
  - Worker B accepts TX-B → reads the SAME pre-TX-A state → writes accepted_state
    with TX-B's submission → TX-A's submission is silently erased
"""

import threading

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from backend.database_handler.contract_processor import ContractProcessor
from backend.database_handler.models import CurrentState


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


# ---------------------------------------------------------------------------
# Test 1: Two concurrent accepted_state updates — must both survive
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    reason="update_contract_state does full-field replacement by design. "
    "Same-field concurrent writes are prevented upstream by advisory locks "
    "in worker.py claim CTEs (pg_try_advisory_xact_lock). "
    "This test documents the limitation — will pass if state merging is added.",
    strict=True,
)
def test_concurrent_accepted_updates_preserve_both(engine: Engine):
    """
    Two workers both write accepted_state for the same contract concurrently.
    Worker A adds submission_A, Worker B adds submission_B.

    This scenario is prevented in production by advisory locks at the worker
    claim level. The update_contract_state API does full replacement, so if
    two callers pass different complete dicts, the second always wins.
    """
    _setup_contract(engine)

    barrier = threading.Barrier(2, timeout=5)
    errors = []

    def worker_a():
        try:
            Session_ = sessionmaker(bind=engine)
            with Session_() as s:
                cp = ContractProcessor(s)
                # Read current state (sees original)
                contract = s.query(CurrentState).filter_by(id=CONTRACT_ADDRESS).one()
                _ = contract.data  # force load
                barrier.wait()  # synchronize with worker B
                # Write accepted_state with submission_A
                cp.update_contract_state(
                    CONTRACT_ADDRESS,
                    accepted_state={"slot_a": "original_a", "submission_A": "scored"},
                )
        except Exception as e:
            errors.append(("A", e))

    def worker_b():
        try:
            Session_ = sessionmaker(bind=engine)
            with Session_() as s:
                cp = ContractProcessor(s)
                # Read current state (sees original — same as worker A)
                contract = s.query(CurrentState).filter_by(id=CONTRACT_ADDRESS).one()
                _ = contract.data  # force load
                barrier.wait()  # synchronize with worker A
                # Write accepted_state with submission_B
                cp.update_contract_state(
                    CONTRACT_ADDRESS,
                    accepted_state={"slot_a": "original_a", "submission_B": "scored"},
                )
        except Exception as e:
            errors.append(("B", e))

    t_a = threading.Thread(target=worker_a)
    t_b = threading.Thread(target=worker_b)
    t_a.start()
    t_b.start()
    t_a.join(timeout=10)
    t_b.join(timeout=10)

    assert not errors, f"Worker errors: {errors}"

    state = _read_state(engine)

    has_a = "submission_A" in state["accepted"]
    has_b = "submission_B" in state["accepted"]

    assert has_a and has_b, (
        f"Lost update: concurrent accepted_state writes must both survive. "
        f"has_A={has_a}, has_B={has_b}, state={state['accepted']}"
    )


# ---------------------------------------------------------------------------
# Test 2: accepted + finalized concurrent updates — must both survive
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
# Test 3: Sequential updates — sanity check (should always pass)
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
