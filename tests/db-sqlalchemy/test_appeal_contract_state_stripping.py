"""Regression test: contract_state stripping must not break appeal votes.

Bug: consensus_data strips contract_state to {} on persist (storage optimisation).
When an appeal loads the leader receipt from DB, the appeal validator compares
its real contract_state against the stripped {}, sees a mismatch, and votes
DETERMINISTIC_VIOLATION — even though the execution was correct.

This test exercises the full persist→load→compare round-trip:
  1. Leader executes → receipt with real contract_state
  2. ConsensusData.to_dict() strips contract_state
  3. Receipt.from_dict() loads it back (simulating DB round-trip)
  4. Appeal validator has real contract_state
  5. The comparison that _set_vote() performs is checked
  6. Assert: should match (AGREE), not mismatch (DETERMINISTIC_VIOLATION)
"""

import base64
import secrets

from backend.database_handler.types import ConsensusData
from backend.node.types import (
    Receipt,
    ExecutionMode,
    ExecutionResultStatus,
    Vote,
)


# Realistic contract state: code_slot + owner_slot + storage_slot
REAL_CONTRACT_STATE = {
    "4a4jQSeS32tqmPt8mDlwH7iwK2/H7QIoEPeDRklGhec=": "code_data_base64",
    "Ny1Gw62p+JfHTTSbv+DkUMeYFnyfWA+Nr4Xe9X6Ww+o=": "owner_data_base64",
    "ugBWMHRazzAUqvFi6ZMwQDAsoL7z9W/i1zwKCPgsYQs=": "storage_data_base64",
}


def _make_receipt(contract_state: dict, mode=ExecutionMode.LEADER) -> Receipt:
    return Receipt(
        result=b"\x00\x00",  # return code 0, empty payload
        calldata=b"\x06",
        gas_used=0,
        mode=mode,
        contract_state=contract_state,
        node_config={"address": "0xLeader", "stake": 100},
        execution_result=ExecutionResultStatus.SUCCESS,
        vote=None,
        eq_outputs={},
        pending_transactions=[],
        genvm_result={"stdout": "", "stderr": ""},
    )


def _simulate_set_vote(leader_receipt: Receipt, validator_receipt: Receipt) -> Vote:
    """Reproduce the comparison logic from Node._set_vote() (node/base.py:722-734).

    This avoids importing Node which pulls in yaml, aiohttp, and other deps
    not available in the db-sqlalchemy test container.
    """
    # Compare contract_state by hash when available (survives stripping)
    state_matches = (
        leader_receipt.contract_state_hash == validator_receipt.contract_state_hash
        if leader_receipt.contract_state_hash is not None
        else leader_receipt.contract_state == validator_receipt.contract_state
    )
    if (
        leader_receipt.execution_result != validator_receipt.execution_result
        or not state_matches
        or leader_receipt.pending_transactions != validator_receipt.pending_transactions
    ):
        return Vote.DETERMINISTIC_VIOLATION

    return Vote.AGREE


class TestContractStateStrippingDoesNotBreakAppeals:
    """Verify that the contract_state stripping round-trip doesn't produce
    false DETERMINISTIC_VIOLATION votes."""

    def test_stripped_leader_state_persist_load_roundtrip(self):
        """The core bug: persist strips contract_state, load sees {},
        validator has real state → false DETERMINISTIC_VIOLATION."""

        # 1. Leader executes with real state
        leader_receipt = _make_receipt(REAL_CONTRACT_STATE)

        # 2. Build ConsensusData as the consensus algorithm does
        consensus_data = ConsensusData(
            votes={"0xValidator": "agree"},
            leader_receipt=[leader_receipt],
            validators=[],
        )

        # 3. Persist: to_dict strips contract_state (default behaviour)
        persisted = consensus_data.to_dict(strip_contract_state=True)

        # Verify stripping happened
        assert persisted["leader_receipt"][0]["contract_state"] == {}

        # 4. Load back from persisted dict (simulates appeal round loading from DB)
        loaded_leader = Receipt.from_dict(persisted["leader_receipt"][0])
        assert loaded_leader.contract_state == {}, "Sanity: loaded state is stripped"

        # 5. Appeal validator executes and produces real state
        validator_receipt = _make_receipt(
            REAL_CONTRACT_STATE, mode=ExecutionMode.VALIDATOR
        )

        # 6. Simulate _set_vote comparison
        vote = _simulate_set_vote(loaded_leader, validator_receipt)

        # 7. Assert: should be AGREE, not DETERMINISTIC_VIOLATION
        assert vote == Vote.AGREE, (
            f"Appeal validator should AGREE when execution is correct, "
            f"but got {vote}. This is caused by contract_state stripping: "
            f"leader has {{}} (stripped) vs validator has {len(REAL_CONTRACT_STATE)} keys."
        )

    def test_unstripped_leader_state_agrees(self):
        """Control: without stripping, matching state → AGREE."""

        leader_receipt = _make_receipt(REAL_CONTRACT_STATE)
        validator_receipt = _make_receipt(
            REAL_CONTRACT_STATE, mode=ExecutionMode.VALIDATOR
        )

        vote = _simulate_set_vote(leader_receipt, validator_receipt)
        assert vote == Vote.AGREE

    def test_real_state_divergence_still_detected(self):
        """Genuine divergence (different state) should still be DETERMINISTIC_VIOLATION."""

        leader_receipt = _make_receipt(REAL_CONTRACT_STATE)

        different_state = dict(REAL_CONTRACT_STATE)
        different_state["new_slot"] = "tampered"
        validator_receipt = _make_receipt(different_state, mode=ExecutionMode.VALIDATOR)

        vote = _simulate_set_vote(leader_receipt, validator_receipt)
        assert vote == Vote.DETERMINISTIC_VIOLATION


class TestContractStateStrippingDBRoundTrip:
    """Test with actual DB persistence (requires PostgreSQL via conftest)."""

    def test_appeal_vote_after_db_persist(self, session, transactions_processor):
        """Full DB round-trip: insert tx with consensus_data, reload, check vote."""
        from backend.database_handler.accounts_manager import AccountsManager
        from backend.domain.types import Transaction

        # 1. Insert a transaction
        tx_hash = "0x" + secrets.token_hex(32)
        am = AccountsManager(session)
        sender = "0xAA00000000000000000000000000000000000099"
        target = "0xBB00000000000000000000000000000000000099"
        am.create_new_account_with_address(sender)
        am.create_new_account_with_address(target)

        transactions_processor.insert_transaction(
            from_address=sender,
            to_address=target,
            data={"calldata": base64.b64encode(b"\x06").decode()},
            value=0,
            type=2,
            nonce=0,
            leader_only=False,
            config_rotation_rounds=3,
            transaction_hash=tx_hash,
        )
        session.commit()

        # 2. Build consensus data with real contract_state
        leader_receipt = _make_receipt(REAL_CONTRACT_STATE)
        consensus_data = ConsensusData(
            votes={"0xValidator": "agree"},
            leader_receipt=[leader_receipt],
            validators=[],
        )

        # 3. Persist with stripping (as AcceptedState does)
        transactions_processor.set_transaction_result(
            tx_hash, consensus_data.to_dict(strip_contract_state=True)
        )
        session.commit()

        # 4. Reload from DB (as appeal worker does)
        tx_data = transactions_processor.get_transaction_by_hash(tx_hash)
        loaded_tx = Transaction.from_dict(tx_data)
        loaded_leader = loaded_tx.consensus_data.leader_receipt[0]

        # Verify contract_state was stripped (may be {} or None depending on path)
        assert (
            not loaded_leader.contract_state
        ), f"Expected stripped contract_state, got {loaded_leader.contract_state}"

        # 5. Validator executes with real state
        validator_receipt = _make_receipt(
            REAL_CONTRACT_STATE, mode=ExecutionMode.VALIDATOR
        )

        # 6. Simulate _set_vote comparison
        vote = _simulate_set_vote(loaded_leader, validator_receipt)

        # 7. Should be AGREE, not DETERMINISTIC_VIOLATION
        assert vote == Vote.AGREE, (
            f"After DB round-trip, appeal validator should AGREE, got {vote}. "
            f"Bug: contract_state stripped to {{}} on persist breaks comparison."
        )
