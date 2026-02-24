"""
Tests to verify that contract_state is stripped from database storage while
maintaining all consensus functionality (deploy, execute, appeals, rollbacks).

These tests verify the implementation from:
- backend/node/types.py: Receipt.to_dict(strip_contract_state=True)
- backend/database_handler/types.py: ConsensusData.to_dict(strip_contract_state=True)
- backend/consensus/base.py: All set_transaction_result() calls strip contract_state
- backend/database_handler/transactions_processor.py: update_consensus_history() strips contract_state
"""

import pytest
from backend.database_handler.models import TransactionStatus
from backend.node.types import Vote
from tests.unit.consensus.test_helpers import (
    TransactionsProcessorMock,
    ContractDB,
    transaction_to_dict,
    init_dummy_transaction,
    get_nodes_specs,
    consensus_algorithm,
    setup_test_environment,
    cleanup_threads,
    appeal,
    assert_transaction_status_match,
    check_contract_state_with_timeout,
)


@pytest.mark.asyncio
async def test_contract_state_stripped_from_consensus_data(consensus_algorithm):
    """
    Test that contract_state is stripped from consensus_data when stored in database.

    This verifies that:
    1. Transactions complete successfully (ACCEPTED -> FINALIZED)
    2. consensus_data.leader_receipt has empty contract_state in database
    3. consensus_data.validators have empty contract_state in database
    4. Contract functionality is preserved despite stripped state
    """
    transaction = init_dummy_transaction()
    nodes = get_nodes_specs(3)
    created_nodes = []
    transactions_processor = TransactionsProcessorMock(
        {transaction.hash: transaction_to_dict(transaction)}
    )

    def get_vote():
        return Vote.AGREE

    event, *threads = setup_test_environment(
        consensus_algorithm, transactions_processor, nodes, created_nodes, get_vote
    )

    try:
        # Wait for transaction to be accepted
        assert_transaction_status_match(
            transactions_processor, transaction, [TransactionStatus.ACCEPTED.value]
        )

        # Verify transaction completed successfully
        tx_data = transactions_processor.get_transaction_by_hash(transaction.hash)

        # Verify consensus_data exists
        assert tx_data["consensus_data"] is not None, "consensus_data should exist"
        assert (
            "leader_receipt" in tx_data["consensus_data"]
        ), "leader_receipt should exist"

        # Verify leader_receipt contract_state is stripped (empty dict)
        leader_receipt = tx_data["consensus_data"]["leader_receipt"]
        if isinstance(leader_receipt, list) and len(leader_receipt) > 0:
            for receipt in leader_receipt:
                assert (
                    receipt.get("contract_state") == {}
                ), f"Leader receipt contract_state should be empty dict, got: {receipt.get('contract_state')}"

        # Verify validators contract_state is stripped (empty dict)
        if "validators" in tx_data["consensus_data"]:
            validators_receipts = tx_data["consensus_data"]["validators"]
            if isinstance(validators_receipts, list):
                for validator_receipt in validators_receipts:
                    assert (
                        validator_receipt.get("contract_state") == {}
                    ), f"Validator receipt contract_state should be empty dict, got: {validator_receipt.get('contract_state')}"

        # Wait for finalization
        assert_transaction_status_match(
            transactions_processor, transaction, [TransactionStatus.FINALIZED.value]
        )

        # Verify transaction flow completed successfully
        assert dict(transactions_processor.updated_transaction_status_history) == {
            "transaction_hash": [
                TransactionStatus.ACTIVATED,
                TransactionStatus.PROPOSING,
                TransactionStatus.COMMITTING,
                TransactionStatus.REVEALING,
                TransactionStatus.ACCEPTED,
                TransactionStatus.FINALIZED,
            ]
        }
    finally:
        cleanup_threads(event, threads)


@pytest.mark.asyncio
async def test_contract_state_stripped_from_consensus_history(consensus_algorithm):
    """
    Test that contract_state is stripped from consensus_history for all rounds.

    This verifies that:
    1. Transaction goes through multiple rounds (rotation)
    2. Each round in consensus_history has stripped contract_state
    3. Leader and validator receipts all have empty contract_state
    """
    transaction = init_dummy_transaction()
    rotation_rounds = 2
    transaction.config_rotation_rounds = rotation_rounds
    nodes = get_nodes_specs(transaction.num_of_initial_validators + rotation_rounds)
    created_nodes = []
    transactions_processor = TransactionsProcessorMock(
        {transaction.hash: transaction_to_dict(transaction)}
    )

    def get_vote():
        # First round disagrees, second round agrees
        if len(created_nodes) < transaction.num_of_initial_validators + 1:
            return Vote.DISAGREE
        else:
            return Vote.AGREE

    event, *threads = setup_test_environment(
        consensus_algorithm, transactions_processor, nodes, created_nodes, get_vote
    )

    try:
        # Wait for transaction to be accepted (after rotation)
        assert_transaction_status_match(
            transactions_processor, transaction, [TransactionStatus.ACCEPTED.value]
        )

        tx_data = transactions_processor.get_transaction_by_hash(transaction.hash)

        # Verify consensus_history exists and has rounds
        assert "consensus_history" in tx_data, "consensus_history should exist"
        assert (
            "consensus_results" in tx_data["consensus_history"]
        ), "consensus_results should exist"

        consensus_results = tx_data["consensus_history"]["consensus_results"]
        assert (
            len(consensus_results) >= 2
        ), f"Should have at least 2 rounds, got {len(consensus_results)}"

        # Check each round
        for round_idx, round_data in enumerate(consensus_results):
            # Check leader_result
            if round_data.get("leader_result"):
                leader_results = round_data["leader_result"]
                if isinstance(leader_results, list):
                    for leader_receipt in leader_results:
                        assert (
                            leader_receipt.get("contract_state") == {}
                        ), f"Round {round_idx} leader contract_state should be empty, got: {leader_receipt.get('contract_state')}"

            # Check validator_results
            if round_data.get("validator_results"):
                validator_results = round_data["validator_results"]
                if isinstance(validator_results, list):
                    for validator_receipt in validator_results:
                        assert (
                            validator_receipt.get("contract_state") == {}
                        ), f"Round {round_idx} validator contract_state should be empty, got: {validator_receipt.get('contract_state')}"

        # Wait for finalization
        assert_transaction_status_match(
            transactions_processor, transaction, [TransactionStatus.FINALIZED.value]
        )
    finally:
        cleanup_threads(event, threads)


@pytest.mark.asyncio
async def test_contract_state_stripped_after_appeal(consensus_algorithm):
    """
    Test that contract_state is stripped from consensus_data after a successful appeal.

    This verifies that:
    1. Transaction is accepted, then appealed successfully
    2. Transaction goes back to PENDING and re-executes
    3. All consensus rounds have stripped contract_state
    4. Appeal functionality works correctly without contract_state
    """
    transaction = init_dummy_transaction("transaction_hash_appeal_test")
    nodes = get_nodes_specs(2 * transaction.num_of_initial_validators + 2)
    created_nodes = []
    transactions_processor = TransactionsProcessorMock(
        {transaction.hash: transaction_to_dict(transaction)}
    )
    contract_db = ContractDB(
        {
            "to_address": {
                "id": "to_address",
                "data": {
                    "state": {"accepted": {}, "finalized": {}},
                    "code": "contract_code",
                },
            }
        }
    )

    def get_vote():
        # First round: all agree
        # Appeal round: all disagree (appeal succeeds)
        # Re-execution: all agree
        if len(created_nodes) < transaction.num_of_initial_validators + 1:
            return Vote.AGREE
        if (len(created_nodes) >= transaction.num_of_initial_validators + 1) and (
            len(created_nodes) < 2 * transaction.num_of_initial_validators + 2
        ):
            return Vote.DISAGREE
        return Vote.AGREE

    event, *threads = setup_test_environment(
        consensus_algorithm,
        transactions_processor,
        nodes,
        created_nodes,
        get_vote,
        None,
        contract_db,
    )

    try:
        # Wait for initial acceptance
        assert_transaction_status_match(
            transactions_processor, transaction, [TransactionStatus.ACCEPTED.value]
        )

        # Verify contract_state stripped in first round
        tx_data = transactions_processor.get_transaction_by_hash(transaction.hash)
        if tx_data["consensus_data"] and tx_data["consensus_data"].get(
            "leader_receipt"
        ):
            leader_receipts = tx_data["consensus_data"]["leader_receipt"]
            if isinstance(leader_receipts, list):
                for receipt in leader_receipts:
                    assert (
                        receipt.get("contract_state") == {}
                    ), "Initial round leader contract_state should be stripped"

        # Trigger appeal
        appeal(transaction, transactions_processor)

        # Wait for re-execution to complete
        assert_transaction_status_match(
            transactions_processor,
            transaction,
            [TransactionStatus.ACCEPTED.value],
            timeout=30,
        )

        # Verify contract_state stripped after appeal
        tx_data = transactions_processor.get_transaction_by_hash(transaction.hash)

        # Check consensus_history has multiple rounds
        if (
            "consensus_history" in tx_data
            and "consensus_results" in tx_data["consensus_history"]
        ):
            consensus_results = tx_data["consensus_history"]["consensus_results"]

            for round_idx, round_data in enumerate(consensus_results):
                # Verify leader_result
                if round_data.get("leader_result"):
                    for leader_receipt in round_data["leader_result"]:
                        assert (
                            leader_receipt.get("contract_state") == {}
                        ), f"Appeal round {round_idx} leader contract_state should be stripped"

                # Verify validator_results
                if round_data.get("validator_results"):
                    for validator_receipt in round_data["validator_results"]:
                        assert (
                            validator_receipt.get("contract_state") == {}
                        ), f"Appeal round {round_idx} validator contract_state should be stripped"

        # Verify finalization completes
        assert_transaction_status_match(
            transactions_processor, transaction, [TransactionStatus.FINALIZED.value]
        )

        # Verify contract state was correctly managed through appeal
        # (contract_db should have correct state despite stripped storage)
        check_contract_state_with_timeout(
            contract_db, transaction.to_address, {"state_var": "1"}, {"state_var": "1"}
        )
    finally:
        cleanup_threads(event, threads)


@pytest.mark.asyncio
async def test_contract_state_stripping_preserves_rollback_functionality(
    consensus_algorithm,
):
    """
    Test that rollback functionality works correctly with stripped contract_state.

    This verifies that:
    1. First transaction is accepted and contract state updated
    2. Second transaction is accepted
    3. First transaction is appealed successfully, rolling back second transaction
    4. Contract state is correctly restored from contract_snapshot (not consensus_data)
    5. All stored receipts have stripped contract_state
    """
    transaction_1 = init_dummy_transaction("transaction_hash_1")
    transaction_2 = init_dummy_transaction("transaction_hash_2")
    nodes = get_nodes_specs(2 * transaction_1.num_of_initial_validators + 2)
    created_nodes = []
    transactions_processor = TransactionsProcessorMock(
        {
            transaction_1.hash: transaction_to_dict(transaction_1),
            transaction_2.hash: transaction_to_dict(transaction_2),
        }
    )
    contract_db = ContractDB(
        {
            "to_address": {
                "id": "to_address",
                "data": {
                    "state": {"accepted": {}, "finalized": {}},
                    "code": "contract_code",
                },
            }
        }
    )

    consensus_algorithm.finality_window_time = 60

    def get_vote():
        """
        Transaction 1: Leader agrees + 4 validators agree
        Transaction 2: Leader agrees + 4 validators agree
        Transaction 1 Appeal: 7 disagree (appeal succeeds)
        Transaction 1 re-execution: Leader agrees + 10 validators agree
        Transaction 2 re-execution: Leader agrees + 4 validators agree
        """
        if len(created_nodes) < (2 * (transaction_1.num_of_initial_validators + 1)):
            return Vote.AGREE
        if (
            len(created_nodes) >= (2 * (transaction_1.num_of_initial_validators + 1))
        ) and (
            len(created_nodes)
            < (2 * (transaction_1.num_of_initial_validators + 1))
            + (transaction_1.num_of_initial_validators + 2)
        ):
            return Vote.DISAGREE
        return Vote.AGREE

    event, *threads = setup_test_environment(
        consensus_algorithm,
        transactions_processor,
        nodes,
        created_nodes,
        get_vote,
        None,
        contract_db,
    )

    try:
        contract_address = transaction_1.to_address

        # Transaction 1 accepted
        assert_transaction_status_match(
            transactions_processor, transaction_1, [TransactionStatus.ACCEPTED.value]
        )

        # Verify contract_state stripped in transaction 1
        tx1_data = transactions_processor.get_transaction_by_hash(transaction_1.hash)
        if tx1_data["consensus_data"] and tx1_data["consensus_data"].get(
            "leader_receipt"
        ):
            for receipt in tx1_data["consensus_data"]["leader_receipt"]:
                assert (
                    receipt.get("contract_state") == {}
                ), "Transaction 1 leader contract_state should be stripped"

        check_contract_state_with_timeout(
            contract_db, contract_address, {"state_var": "1"}, {}
        )

        # Transaction 2 accepted
        assert_transaction_status_match(
            transactions_processor, transaction_2, [TransactionStatus.ACCEPTED.value]
        )

        # Verify contract_state stripped in transaction 2
        tx2_data = transactions_processor.get_transaction_by_hash(transaction_2.hash)
        if tx2_data["consensus_data"] and tx2_data["consensus_data"].get(
            "leader_receipt"
        ):
            for receipt in tx2_data["consensus_data"]["leader_receipt"]:
                assert (
                    receipt.get("contract_state") == {}
                ), "Transaction 2 leader contract_state should be stripped"

        check_contract_state_with_timeout(
            contract_db, contract_address, {"state_var": "12"}, {}
        )

        # Appeal transaction 1
        appeal(transaction_1, transactions_processor)

        # Verify contract state rolled back (from contract_snapshot, not consensus_data)
        check_contract_state_with_timeout(contract_db, contract_address, {}, {})

        # Wait for transaction 1 to be re-executed
        assert_transaction_status_match(
            transactions_processor, transaction_1, [TransactionStatus.ACCEPTED.value]
        )

        check_contract_state_with_timeout(
            contract_db, contract_address, {"state_var": "1"}, {}
        )

        # Verify transaction 2 re-executed
        assert_transaction_status_match(
            transactions_processor, transaction_2, [TransactionStatus.ACCEPTED.value]
        )

        check_contract_state_with_timeout(
            contract_db, contract_address, {"state_var": "12"}, {}
        )

        # Verify all consensus_history rounds have stripped contract_state
        tx1_final = transactions_processor.get_transaction_by_hash(transaction_1.hash)
        if (
            "consensus_history" in tx1_final
            and "consensus_results" in tx1_final["consensus_history"]
        ):
            for round_data in tx1_final["consensus_history"]["consensus_results"]:
                if round_data.get("leader_result"):
                    for receipt in round_data["leader_result"]:
                        assert (
                            receipt.get("contract_state") == {}
                        ), "All historical rounds should have stripped contract_state"

        # Verify rollback succeeded despite stripped contract_state
        assert dict(transactions_processor.updated_transaction_status_history) == {
            "transaction_hash_1": [
                TransactionStatus.ACTIVATED,
                TransactionStatus.PROPOSING,
                TransactionStatus.COMMITTING,
                TransactionStatus.REVEALING,
                TransactionStatus.ACCEPTED,
                TransactionStatus.COMMITTING,
                TransactionStatus.REVEALING,
                TransactionStatus.PENDING,
                TransactionStatus.ACTIVATED,
                TransactionStatus.PROPOSING,
                TransactionStatus.COMMITTING,
                TransactionStatus.REVEALING,
                TransactionStatus.ACCEPTED,
            ],
            "transaction_hash_2": [
                TransactionStatus.ACTIVATED,
                TransactionStatus.PROPOSING,
                TransactionStatus.COMMITTING,
                TransactionStatus.REVEALING,
                TransactionStatus.ACCEPTED,
                TransactionStatus.PENDING,
                TransactionStatus.ACTIVATED,
                TransactionStatus.PROPOSING,
                TransactionStatus.COMMITTING,
                TransactionStatus.REVEALING,
                TransactionStatus.ACCEPTED,
            ],
        }

    finally:
        cleanup_threads(event, threads)
