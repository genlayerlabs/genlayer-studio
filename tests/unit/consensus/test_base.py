import pytest
from backend.database_handler.models import TransactionStatus
from backend.node.types import Vote
from tests.unit.consensus.test_helpers import (
    TransactionsProcessorMock,
    ContractDB,
    transaction_to_dict,
    init_dummy_transaction,
    get_nodes_specs,
    setup_test_environment,
    consensus_algorithm,
    cleanup_threads,
    appeal,
    check_validator_count,
    get_validator_addresses,
    get_leader_address,
    get_leader_timeout_validators_addresses,
    get_consensus_rounds_names,
    assert_transaction_status_match,
    assert_transaction_status_change_and_match,
    check_contract_state,
    check_contract_state_with_timeout,
)


@pytest.mark.asyncio
async def test_exec_transaction(consensus_algorithm):
    """
    Minor smoke checks for the happy path of a transaction execution
    """
    # Initialize transaction, nodes, and get_vote function
    transaction = init_dummy_transaction()
    nodes = get_nodes_specs(3)
    created_nodes = []
    transactions_processor = TransactionsProcessorMock(
        [transaction_to_dict(transaction)]
    )

    def get_vote():
        return Vote.AGREE

    # Use the helper function to set up the test environment
    event, *threads = setup_test_environment(
        consensus_algorithm, transactions_processor, nodes, created_nodes, get_vote
    )

    try:
        assert_transaction_status_match(
            transactions_processor, transaction, [TransactionStatus.ACCEPTED.value]
        )
        assert len(created_nodes) == len(nodes) + 1

        assert_transaction_status_match(
            transactions_processor, transaction, [TransactionStatus.FINALIZED.value]
        )
        assert transactions_processor.updated_transaction_status_history == {
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
async def test_exec_transaction_no_consensus(consensus_algorithm):
    """
    Scenario: all nodes disagree on the transaction execution, leaving the transaction in UNDETERMINED state
    Tests that consensus algorithm correctly rotates the leader when majority of nodes disagree
    """
    transaction = init_dummy_transaction()
    rotation_rounds = 2
    transaction.config_rotation_rounds = rotation_rounds
    nodes = get_nodes_specs(transaction.num_of_initial_validators + rotation_rounds)
    created_nodes = []
    transactions_processor = TransactionsProcessorMock(
        [transaction_to_dict(transaction)]
    )

    def get_vote():
        return Vote.DISAGREE

    event, *threads = setup_test_environment(
        consensus_algorithm, transactions_processor, nodes, created_nodes, get_vote
    )

    try:
        assert_transaction_status_match(
            transactions_processor, transaction, [TransactionStatus.UNDETERMINED.value]
        )
        assert len(created_nodes) == (transaction.num_of_initial_validators + 1) * (
            rotation_rounds + 1
        )

        assert_transaction_status_match(
            transactions_processor, transaction, [TransactionStatus.FINALIZED.value]
        )

        assert transactions_processor.updated_transaction_status_history == {
            "transaction_hash": [
                TransactionStatus.ACTIVATED,
                TransactionStatus.PROPOSING,  # leader 1
                TransactionStatus.COMMITTING,
                TransactionStatus.REVEALING,
                TransactionStatus.PROPOSING,  # rotation, leader 2
                TransactionStatus.COMMITTING,
                TransactionStatus.REVEALING,
                TransactionStatus.PROPOSING,  # rotation, leader 3
                TransactionStatus.COMMITTING,
                TransactionStatus.REVEALING,
                TransactionStatus.UNDETERMINED,  # all disagree
                TransactionStatus.FINALIZED,
            ]
        }
    finally:
        cleanup_threads(event, threads)


@pytest.mark.asyncio
async def test_exec_transaction_one_disagreement(consensus_algorithm):
    """
    Scenario: first round is disagreement, second round is agreement
    Tests that consensus algorithm correctly rotates the leader when majority of nodes disagree
    """
    transaction = init_dummy_transaction()
    nodes = get_nodes_specs(transaction.num_of_initial_validators + 1)
    created_nodes = []
    transactions_processor = TransactionsProcessorMock(
        [transaction_to_dict(transaction)]
    )

    def get_vote():
        if len(created_nodes) < transaction.num_of_initial_validators + 1:
            return Vote.DISAGREE
        else:
            return Vote.AGREE

    event, *threads = setup_test_environment(
        consensus_algorithm, transactions_processor, nodes, created_nodes, get_vote
    )

    try:
        assert_transaction_status_match(
            transactions_processor, transaction, [TransactionStatus.FINALIZED.value]
        )

        assert transactions_processor.updated_transaction_status_history == {
            "transaction_hash": [
                TransactionStatus.ACTIVATED,
                TransactionStatus.PROPOSING,  # leader 1
                TransactionStatus.COMMITTING,
                TransactionStatus.REVEALING,
                TransactionStatus.PROPOSING,  # rotation, leader 2
                TransactionStatus.COMMITTING,
                TransactionStatus.REVEALING,
                TransactionStatus.ACCEPTED,
                TransactionStatus.FINALIZED,
            ]
        }
        assert len(created_nodes) == (transaction.num_of_initial_validators + 1) * 2
    finally:
        cleanup_threads(event, threads)


@pytest.mark.asyncio
async def test_exec_accepted_appeal_fail(consensus_algorithm):
    """
    Test that a transaction can be appealed after being accepted where the appeal fails. This verifies that:
    1. The transaction can enter appeal state
    2. New validators are selected to process the appeal
    3. The appeal is processed but fails
    4. The transaction goes back to the active state
    5. The appeal window is not reset
    6. The transaction is finalized after the appeal window
    The states the transaction goes through are:
        PROPOSING -> COMMITTING -> REVEALING -> ACCEPTED -appeal-> COMMITTING -> REVEALING -appeal-fail-> ACCEPTED -no-appeal-> FINALIZED
    """
    transaction = init_dummy_transaction()
    nodes = get_nodes_specs(2 * transaction.num_of_initial_validators + 2)
    created_nodes = []
    transactions_processor = TransactionsProcessorMock(
        [transaction_to_dict(transaction)]
    )

    def get_vote():
        return Vote.AGREE

    event, *threads = setup_test_environment(
        consensus_algorithm, transactions_processor, nodes, created_nodes, get_vote
    )

    try:
        assert_transaction_status_match(
            transactions_processor, transaction, [TransactionStatus.ACCEPTED.value]
        )
        assert len(created_nodes) == transaction.num_of_initial_validators + 1

        timestamp_awaiting_finalization_1 = (
            transactions_processor.get_transaction_by_hash(transaction.hash)[
                "timestamp_awaiting_finalization"
            ]
        )

        appeal(transaction, transactions_processor)
        assert_transaction_status_match(
            transactions_processor, transaction, [TransactionStatus.FINALIZED.value]
        )

        check_validator_count(
            transaction,
            transactions_processor,
            2 * transaction.num_of_initial_validators + 2,
        )

        assert transactions_processor.updated_transaction_status_history == {
            "transaction_hash": [
                TransactionStatus.ACTIVATED,
                TransactionStatus.PROPOSING,
                TransactionStatus.COMMITTING,
                TransactionStatus.REVEALING,
                TransactionStatus.ACCEPTED,
                TransactionStatus.COMMITTING,
                TransactionStatus.REVEALING,
                TransactionStatus.ACCEPTED,
                TransactionStatus.FINALIZED,
            ]
        }
        assert len(created_nodes) == 2 * transaction.num_of_initial_validators + 1 + 2

        assert (
            transactions_processor.get_transaction_by_hash(transaction.hash)[
                "timestamp_awaiting_finalization"
            ]
            == timestamp_awaiting_finalization_1
        )

        assert (
            transactions_processor.get_transaction_by_hash(transaction.hash)[
                "appeal_processing_time"
            ]
            > 0
        )
    finally:
        cleanup_threads(event, threads)


@pytest.mark.asyncio
async def test_exec_accepted_appeal_no_extra_validators(consensus_algorithm):
    """
    Test that a transaction goes to finalized state when there are no extra validators to process the appeal. This verifies that:
    1. The transaction can enter appeal state
    2. New validators are selected to process the appeal but there are no extra validators anymore
    3. The appeal is not processed and fails
    4. The transaction stays in the active state and appeal window is not reset
    5. The transaction is finalized after the appeal window
    The states the transaction goes through are:
        PROPOSING -> COMMITTING -> REVEALING -> ACCEPTED -appeal-> -appeal-fail-> -no-new-appeal-> FINALIZED
    """
    transaction = init_dummy_transaction()
    nodes = get_nodes_specs(transaction.num_of_initial_validators)
    created_nodes = []
    transactions_processor = TransactionsProcessorMock(
        [transaction_to_dict(transaction)]
    )

    def get_vote():
        return Vote.AGREE

    event, *threads = setup_test_environment(
        consensus_algorithm, transactions_processor, nodes, created_nodes, get_vote
    )

    try:
        assert_transaction_status_match(
            transactions_processor, transaction, [TransactionStatus.ACCEPTED.value]
        )
        assert len(created_nodes) == transaction.num_of_initial_validators + 1

        timestamp_awaiting_finalization_1 = (
            transactions_processor.get_transaction_by_hash(transaction.hash)[
                "timestamp_awaiting_finalization"
            ]
        )

        appeal(transaction, transactions_processor)

        assert_transaction_status_match(
            transactions_processor, transaction, [TransactionStatus.FINALIZED.value]
        )

        assert transactions_processor.updated_transaction_status_history == {
            "transaction_hash": [
                TransactionStatus.ACTIVATED,
                TransactionStatus.PROPOSING,
                TransactionStatus.COMMITTING,
                TransactionStatus.REVEALING,
                TransactionStatus.ACCEPTED,
                TransactionStatus.FINALIZED,
            ]
        }

        assert (
            transactions_processor.get_transaction_by_hash(transaction.hash)[
                "timestamp_awaiting_finalization"
            ]
            == timestamp_awaiting_finalization_1
        )
        assert (
            transactions_processor.get_transaction_by_hash(transaction.hash)[
                "appeal_processing_time"
            ]
            > 0
        )
    finally:
        cleanup_threads(event, threads)


@pytest.mark.asyncio
async def test_exec_accepted_appeal_successful(consensus_algorithm):
    """
    Test that a transaction can be appealed successfully after being accepted. This verifies that:
    1. The transaction can enter appeal state
    2. New validators are selected to process the appeal
    3. The appeal is processed successfully
    4. The transaction goes back to the pending state
    5. The consensus algorithm removed the old leader
    6. The consensus algorithm goes through committing and revealing states with an increased number of validators
    7. The transaction is in the accepted state with an updated appeal window
    8. The transaction is finalized after the appeal window
    The states the transaction goes through are:
        PROPOSING -> COMMITTING -> REVEALING -> ACCEPTED -appeal-> COMMITTING -> REVEALING -appeal-success->
        PENDING -> PROPOSING -> COMMITTING -> REVEALING -> ACCEPTED -no-appeal-> FINALIZED
    """
    transaction = init_dummy_transaction("transaction_hash_1")
    nodes = get_nodes_specs(2 * transaction.num_of_initial_validators + 2)
    created_nodes = []
    transactions_processor = TransactionsProcessorMock(
        [transaction_to_dict(transaction)]
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
        """
        Leader agrees + 4 validators agree.
        Appeal: 4 validators disagree + 3 validators agree. So appeal succeeds.
        """
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
        check_contract_state(contract_db, transaction.to_address, {}, {})
        assert_transaction_status_match(
            transactions_processor, transaction, [TransactionStatus.ACCEPTED.value]
        )

        expected_nb_created_nodes = transaction.num_of_initial_validators + 1
        assert len(created_nodes) == expected_nb_created_nodes

        timestamp_awaiting_finalization_1 = (
            transactions_processor.get_transaction_by_hash(transaction.hash)[
                "timestamp_awaiting_finalization"
            ]
        )

        check_contract_state_with_timeout(
            contract_db, transaction.to_address, {"state_var": "1"}, {}
        )

        appeal(transaction, transactions_processor)

        current_status = assert_transaction_status_match(
            transactions_processor,
            transaction,
            [TransactionStatus.PENDING.value, TransactionStatus.ACTIVATED.value],
        )

        transaction_status_history = [
            TransactionStatus.ACTIVATED,
            TransactionStatus.PROPOSING,
            TransactionStatus.COMMITTING,
            TransactionStatus.REVEALING,
            TransactionStatus.ACCEPTED,
            TransactionStatus.COMMITTING,
            TransactionStatus.REVEALING,
            TransactionStatus.PENDING,
        ]
        if current_status == TransactionStatus.ACTIVATED.value:
            transaction_status_history.append(TransactionStatus.ACTIVATED)

        assert transactions_processor.updated_transaction_status_history == {
            "transaction_hash_1": transaction_status_history
        }

        expected_nb_created_nodes += transaction.num_of_initial_validators + 2
        assert len(created_nodes) == expected_nb_created_nodes

        validator_set_addresses = get_validator_addresses(
            transaction, transactions_processor
        )
        old_leader_address = get_leader_address(transaction, transactions_processor)

        check_contract_state_with_timeout(contract_db, transaction.to_address, {}, {})

        assert_transaction_status_match(
            transactions_processor, transaction, [TransactionStatus.FINALIZED.value]
        )

        expected_nb_created_nodes += (
            (2 * transaction.num_of_initial_validators + 2) - 1 + 1
        )
        assert len(created_nodes) == expected_nb_created_nodes

        if current_status == TransactionStatus.PENDING.value:
            transaction_status_history.append(TransactionStatus.ACTIVATED)
        transaction_status_history += [
            TransactionStatus.PROPOSING,
            TransactionStatus.COMMITTING,
            TransactionStatus.REVEALING,
            TransactionStatus.ACCEPTED,
            TransactionStatus.FINALIZED,
        ]
        assert transactions_processor.updated_transaction_status_history == {
            "transaction_hash_1": transaction_status_history
        }

        assert (
            transactions_processor.get_transaction_by_hash(transaction.hash)[
                "timestamp_awaiting_finalization"
            ]
            > timestamp_awaiting_finalization_1
        )

        check_validator_count(
            transaction,
            transactions_processor,
            2 * transaction.num_of_initial_validators + 1,
        )

        new_leader_address = get_leader_address(transaction, transactions_processor)

        assert new_leader_address != old_leader_address
        assert new_leader_address in validator_set_addresses

        assert (
            transactions_processor.get_transaction_by_hash(transaction.hash)[
                "appeal_processing_time"
            ]
            == 0
        )

        check_contract_state(
            contract_db, transaction.to_address, {"state_var": "1"}, {"state_var": "1"}
        )
        assert created_nodes[0].contract_snapshot.states == {
            "accepted": {},
            "finalized": {},
        }  # appeal nodes use original contract snapshot
    finally:
        cleanup_threads(event, threads)


@pytest.mark.asyncio
async def test_exec_accepted_appeal_successful_rotations_undetermined(
    consensus_algorithm,
):
    """
    Test that a transaction can do the rotations when going back to pending after being successful in its appeal. This verifies that:
    1. The transaction can enter appeal state
    2. New validators are selected to process the appeal
    3. The appeal is processed successfully and the transaction goes back to the pending state
    4. Perform all rotation until transaction is undetermined
    The states the transaction goes through are:
        PROPOSING -> COMMITTING -> REVEALING -> ACCEPTED -appeal-> COMMITTING -> REVEALING -appeal-success->
        PENDING -> (PROPOSING -> COMMITTING -> REVEALING) * 4 -> UNDERTERMINED
    """
    transaction = init_dummy_transaction()
    nodes = get_nodes_specs(
        2 * transaction.num_of_initial_validators
        + 2
        + transaction.config_rotation_rounds
    )
    created_nodes = []
    transactions_processor = TransactionsProcessorMock(
        [transaction_to_dict(transaction)]
    )

    def get_vote():
        """
        Leader agrees + 4 validators agree.
        Appeal: 7 validators disagree. So appeal succeeds.
        Rotations: 11 validator disagree.
        """
        if len(created_nodes) < transaction.num_of_initial_validators + 1:
            return Vote.AGREE
        return Vote.DISAGREE

    event, *threads = setup_test_environment(
        consensus_algorithm, transactions_processor, nodes, created_nodes, get_vote
    )

    try:
        assert_transaction_status_match(
            transactions_processor, transaction, [TransactionStatus.ACCEPTED.value]
        )
        expected_nb_created_nodes = transaction.num_of_initial_validators + 1
        assert len(created_nodes) == expected_nb_created_nodes

        appeal(transaction, transactions_processor)

        current_status = assert_transaction_status_match(
            transactions_processor,
            transaction,
            [TransactionStatus.PENDING.value, TransactionStatus.ACTIVATED.value],
        )

        transaction_status_history = [
            TransactionStatus.ACTIVATED,
            TransactionStatus.PROPOSING,
            TransactionStatus.COMMITTING,
            TransactionStatus.REVEALING,
            TransactionStatus.ACCEPTED,
            TransactionStatus.COMMITTING,
            TransactionStatus.REVEALING,
            TransactionStatus.PENDING,
        ]
        if current_status == TransactionStatus.ACTIVATED.value:
            transaction_status_history.append(TransactionStatus.ACTIVATED)

        assert transactions_processor.updated_transaction_status_history == {
            "transaction_hash": transaction_status_history
        }

        expected_nb_created_nodes += transaction.num_of_initial_validators + 2
        assert len(created_nodes) == expected_nb_created_nodes

        assert_transaction_status_match(
            transactions_processor, transaction, [TransactionStatus.UNDETERMINED.value]
        )

        if current_status == TransactionStatus.PENDING.value:
            transaction_status_history.append(TransactionStatus.ACTIVATED)
        transaction_status_history += [
            *(
                [
                    TransactionStatus.PROPOSING,
                    TransactionStatus.COMMITTING,
                    TransactionStatus.REVEALING,
                ]
                * (transaction.config_rotation_rounds + 1)
            ),
            TransactionStatus.UNDETERMINED,
        ]
        assert transactions_processor.updated_transaction_status_history == {
            "transaction_hash": transaction_status_history
        }

        check_validator_count(
            transaction,
            transactions_processor,
            2 * transaction.num_of_initial_validators + 1,
        )

        expected_nb_created_nodes += (2 * transaction.num_of_initial_validators + 2) * (
            transaction.config_rotation_rounds + 1
        )
        assert len(created_nodes) == expected_nb_created_nodes
    finally:
        cleanup_threads(event, threads)


@pytest.mark.asyncio
async def test_exec_accepted_appeal_successful_twice(consensus_algorithm):
    """
    Test that a transaction can be appealed successfully twice after being accepted. This verifies that:
    1. The transaction can enter appeal state
    2. New validators are selected to process the appeal
    3. The appeal is processed successfully
    4. The transaction goes back to the pending state
    5. The consensus algorithm removed the old leader
    6. The consensus algorithm goes through committing and revealing states with an increased number of validators
    7. The transaction is in the accepted state with an updated appeal window
    8. Do 1-7 again
    9. The transaction is finalized after the appeal window
    The states the transaction goes through are:
        PROPOSING -> COMMITTING -> REVEALING -> ACCEPTED -appeal-> COMMITTING -> REVEALING -appeal-success->
        PENDING -> PROPOSING -> COMMITTING -> REVEALING -> ACCEPTED -appeal-> COMMITTING -> REVEALING -appeal-success->
        PENDING -> PROPOSING -> COMMITTING -> REVEALING -> ACCEPTED -no-appeal-> FINALIZED
    """
    transaction = init_dummy_transaction()
    nodes = get_nodes_specs(2 * (2 * transaction.num_of_initial_validators + 1) + 1 + 2)
    created_nodes = []
    transactions_processor = TransactionsProcessorMock(
        [transaction_to_dict(transaction)]
    )

    def get_vote():
        """
        Normal: Leader agrees + 4 validators agree.
        Appeal: 7 validators disagree. So appeal succeeds.
        Normal: Leader agrees + 10 validators agree.
        Appeal: 13 validators disagree. So appeal succeeds.
        Normal: Leader agrees + 22 validators agree.
        """
        if len(created_nodes) < transaction.num_of_initial_validators + 1:
            return Vote.AGREE
        elif (len(created_nodes) >= transaction.num_of_initial_validators + 1) and (
            len(created_nodes) < 2 * transaction.num_of_initial_validators + 2 + 1
        ):
            return Vote.DISAGREE
        elif (
            len(created_nodes) >= 2 * transaction.num_of_initial_validators + 2 + 1
        ) and (
            len(created_nodes)
            < 2 * (2 * transaction.num_of_initial_validators + 2) - 1 + 2
        ):
            return Vote.AGREE
        elif (
            len(created_nodes)
            >= 2 * (2 * transaction.num_of_initial_validators + 2) - 1 + 2
        ) and (
            len(created_nodes) < 3 * (2 * transaction.num_of_initial_validators + 2) + 2
        ):
            return Vote.DISAGREE
        else:
            return Vote.AGREE

    event, *threads = setup_test_environment(
        consensus_algorithm, transactions_processor, nodes, created_nodes, get_vote
    )

    try:
        assert_transaction_status_match(
            transactions_processor, transaction, [TransactionStatus.ACCEPTED.value]
        )

        expected_nb_created_nodes = transaction.num_of_initial_validators + 1  # 5 + 1
        assert len(created_nodes) == expected_nb_created_nodes

        transaction_status_history = [
            TransactionStatus.ACTIVATED,
            TransactionStatus.PROPOSING,
            TransactionStatus.COMMITTING,
            TransactionStatus.REVEALING,
            TransactionStatus.ACCEPTED,
        ]
        assert transactions_processor.updated_transaction_status_history == {
            "transaction_hash": transaction_status_history
        }

        timestamp_awaiting_finalization_1 = (
            transactions_processor.get_transaction_by_hash(transaction.hash)[
                "timestamp_awaiting_finalization"
            ]
        )

        appeal(transaction, transactions_processor)

        current_status = assert_transaction_status_match(
            transactions_processor,
            transaction,
            [TransactionStatus.PENDING.value, TransactionStatus.ACTIVATED.value],
        )

        transaction_status_history += [
            TransactionStatus.COMMITTING,
            TransactionStatus.REVEALING,
            TransactionStatus.PENDING,
        ]
        if current_status == TransactionStatus.ACTIVATED.value:
            transaction_status_history.append(TransactionStatus.ACTIVATED)

        assert transactions_processor.updated_transaction_status_history == {
            "transaction_hash": transaction_status_history
        }

        expected_nb_created_nodes += (
            transaction.num_of_initial_validators + 2
        )  # 5 + 1 + 7 = 13
        assert len(created_nodes) == expected_nb_created_nodes

        validator_set_addresses = get_validator_addresses(
            transaction, transactions_processor
        )
        old_leader_address = get_leader_address(transaction, transactions_processor)

        assert_transaction_status_match(
            transactions_processor, transaction, [TransactionStatus.ACCEPTED.value]
        )

        if current_status == TransactionStatus.PENDING.value:
            transaction_status_history.append(TransactionStatus.ACTIVATED)
        transaction_status_history += [
            TransactionStatus.PROPOSING,
            TransactionStatus.COMMITTING,
            TransactionStatus.REVEALING,
            TransactionStatus.ACCEPTED,
        ]
        assert transactions_processor.updated_transaction_status_history == {
            "transaction_hash": transaction_status_history
        }

        expected_nb_created_nodes += (
            2 * transaction.num_of_initial_validators + 1 + 1
        )  # 13 + 11 + 1 = 25
        assert len(created_nodes) == expected_nb_created_nodes

        timestamp_awaiting_finalization_2 = (
            transactions_processor.get_transaction_by_hash(transaction.hash)[
                "timestamp_awaiting_finalization"
            ]
        )
        assert timestamp_awaiting_finalization_2 > timestamp_awaiting_finalization_1

        check_validator_count(
            transaction,
            transactions_processor,
            2 * transaction.num_of_initial_validators + 1,
        )

        new_leader_address = get_leader_address(transaction, transactions_processor)

        assert new_leader_address != old_leader_address
        assert new_leader_address in validator_set_addresses

        appeal(transaction, transactions_processor)

        current_status = assert_transaction_status_match(
            transactions_processor,
            transaction,
            [TransactionStatus.PENDING.value, TransactionStatus.ACTIVATED.value],
        )

        transaction_status_history += [
            TransactionStatus.COMMITTING,
            TransactionStatus.REVEALING,
            TransactionStatus.PENDING,
        ]
        if current_status == TransactionStatus.ACTIVATED.value:
            transaction_status_history.append(TransactionStatus.ACTIVATED)

        assert transactions_processor.updated_transaction_status_history == {
            "transaction_hash": transaction_status_history
        }

        expected_nb_created_nodes += (
            2 * transaction.num_of_initial_validators + 1
        ) + 2  # 25 + 13 = 38
        assert len(created_nodes) == expected_nb_created_nodes

        validator_set_addresses = get_validator_addresses(
            transaction, transactions_processor
        )
        old_leader_address = get_leader_address(transaction, transactions_processor)

        assert_transaction_status_match(
            transactions_processor, transaction, [TransactionStatus.FINALIZED.value]
        )

        if current_status == TransactionStatus.PENDING.value:
            transaction_status_history.append(TransactionStatus.ACTIVATED)
        transaction_status_history += [
            TransactionStatus.PROPOSING,
            TransactionStatus.COMMITTING,
            TransactionStatus.REVEALING,
            TransactionStatus.ACCEPTED,
            TransactionStatus.FINALIZED,
        ]
        assert transactions_processor.updated_transaction_status_history == {
            "transaction_hash": transaction_status_history
        }

        expected_nb_created_nodes += (
            (2 * (2 * transaction.num_of_initial_validators + 1) + 2) - 1 + 1
        )  # 38 + 24 = 62
        assert len(created_nodes) == expected_nb_created_nodes

        assert (
            transactions_processor.get_transaction_by_hash(transaction.hash)[
                "timestamp_awaiting_finalization"
            ]
            > timestamp_awaiting_finalization_2
        )

        check_validator_count(
            transaction,
            transactions_processor,
            2 * (2 * transaction.num_of_initial_validators + 1) + 1,
        )

        new_leader_address = get_leader_address(transaction, transactions_processor)

        assert new_leader_address != old_leader_address
        assert new_leader_address in validator_set_addresses

    finally:
        cleanup_threads(event, threads)


@pytest.mark.asyncio
async def test_exec_accepted_appeal_fail_three_times(consensus_algorithm):
    """
    Test that a transaction can be appealed after being accepted where the appeal fails three times. This verifies that:
    1. The transaction can enter appeal state after being accepted
    2. New validators are selected to process the appeal:
        2.1 N+2 new validators where appeal_failed = 0
        2.2 N+2 old validators from 2.1 + N+1 new validators = 2N+3 validators where appeal_failed = 1
        2.3 2N+3 old validators from 2.2 + 2N new validators = 4N+3 validators where appeal_failed = 2
        2.4 No need to continue testing more validators as it follows the same pattern as 2.3 calculation
    3. The appeal is processed but fails
    4. The transaction goes back to the active state
    5. The appeal window is not reset
    6. Redo 1-5 two more times to check if the correct amount of validators are selected. First time takes 2.2 validators, second time takes 2.3 validators.
    7. The transaction is finalized after the appeal window
    The states the transaction goes through are:
        PROPOSING -> COMMITTING -> REVEALING -> ACCEPTED (-appeal-> COMMITTING -> REVEALING -appeal-fail-> ACCEPTED)x3 -no-appeal-> FINALIZED
    """
    transaction = init_dummy_transaction()
    nodes = get_nodes_specs(5 * transaction.num_of_initial_validators + 3)
    created_nodes = []
    transactions_processor = TransactionsProcessorMock(
        [transaction_to_dict(transaction)]
    )
    consensus_algorithm.consensus_sleep_time = 5
    consensus_algorithm.finality_window_time = 15

    def get_vote():
        return Vote.AGREE

    event, *threads = setup_test_environment(
        consensus_algorithm, transactions_processor, nodes, created_nodes, get_vote
    )

    try:
        assert_transaction_status_match(
            transactions_processor, transaction, [TransactionStatus.ACCEPTED.value]
        )

        timestamp_awaiting_finalization_1 = (
            transactions_processor.get_transaction_by_hash(transaction.hash)[
                "timestamp_awaiting_finalization"
            ]
        )

        n = transaction.num_of_initial_validators
        nb_validators_processing_appeal = n
        nb_created_nodes = n + 1

        check_validator_count(
            transaction, transactions_processor, nb_validators_processing_appeal
        )

        assert len(created_nodes) == nb_created_nodes

        validator_set_addresses = get_validator_addresses(
            transaction, transactions_processor
        )
        leader_address = get_leader_address(transaction, transactions_processor)

        appeal_processing_time_temp = transactions_processor.get_transaction_by_hash(
            transaction.hash
        )["appeal_processing_time"]
        assert appeal_processing_time_temp == 0
        timestamp_appeal_temp = 0

        for appeal_failed in range(3):
            assert (
                transactions_processor.get_transaction_by_hash(transaction.hash)[
                    "status"
                ]
                == TransactionStatus.ACCEPTED.value
            )

            appeal(transaction, transactions_processor)

            assert_transaction_status_change_and_match(
                transactions_processor, transaction, [TransactionStatus.ACCEPTED.value]
            )

            appeal_processing_time_new = transactions_processor.get_transaction_by_hash(
                transaction.hash
            )["appeal_processing_time"]
            assert appeal_processing_time_new > appeal_processing_time_temp
            appeal_processing_time_temp = appeal_processing_time_new

            timestamp_appeal_new = transactions_processor.get_transaction_by_hash(
                transaction.hash
            )["timestamp_appeal"]
            assert timestamp_appeal_new > timestamp_appeal_temp
            timestamp_appeal_temp = timestamp_appeal_new

            assert (
                transactions_processor.get_transaction_by_hash(transaction.hash)[
                    "timestamp_awaiting_finalization"
                ]
                == timestamp_awaiting_finalization_1
            )

            assert (
                transactions_processor.get_transaction_by_hash(transaction.hash)[
                    "appeal_failed"
                ]
                == appeal_failed + 1
            )

            if appeal_failed == 0:
                nb_validators_processing_appeal += n + 2
            elif appeal_failed == 1:
                nb_validators_processing_appeal += n + 1
            else:
                nb_validators_processing_appeal += 2 * n  # 5, 12, 18, 28

            nb_created_nodes += (
                nb_validators_processing_appeal - n
            )  # 5, 7, 13, 23 -> 5, 12, 25, 48

            check_validator_count(
                transaction, transactions_processor, nb_validators_processing_appeal
            )

            assert len(created_nodes) == nb_created_nodes

            validator_set_addresses_old = validator_set_addresses
            validator_set_addresses = get_validator_addresses(
                transaction, transactions_processor
            )
            assert validator_set_addresses_old != validator_set_addresses
            assert validator_set_addresses_old.issubset(validator_set_addresses)
            assert leader_address == get_leader_address(
                transaction, transactions_processor
            )
            assert leader_address not in validator_set_addresses

        assert_transaction_status_match(
            transactions_processor, transaction, [TransactionStatus.FINALIZED.value]
        )

        assert transactions_processor.updated_transaction_status_history == {
            "transaction_hash": [
                TransactionStatus.ACTIVATED,
                TransactionStatus.PROPOSING,
                *(
                    [
                        TransactionStatus.COMMITTING,
                        TransactionStatus.REVEALING,
                        TransactionStatus.ACCEPTED,
                    ]
                    * 4
                ),
                TransactionStatus.FINALIZED,
            ]
        }
    finally:
        cleanup_threads(event, threads)


@pytest.mark.asyncio
async def test_exec_accepted_appeal_successful_fail_successful(consensus_algorithm):
    """
    Test that a transaction can be appealed successfully, then appeal fails, then be successfully appealed again after being accepted. This verifies that:
    1. The transaction can enter appeal state
    2. New validators are selected to process the appeal
    3. The appeal is processed successfully
    4. The transaction goes back to the pending state
    5. The consensus algorithm removes the old leader
    6. The consensus algorithm goes through committing and revealing states with an increased number of validators
    7. The transaction is in the accepted state with an updated appeal window
    8. The transaction can enter appeal state
    9. New validators are selected to process the appeal
    10. The appeal is processed but fails
    11. The transaction goes back to the active state
    12. The appeal window is not reset
    13. Redo 1-7
    14. The transaction is finalized after the appeal window
    The states the transaction goes through are:
        PROPOSING -> COMMITTING -> REVEALING -> ACCEPTED
        -appeal-> COMMITTING -> REVEALING -appeal-success->
        PENDING -> PROPOSING -> COMMITTING -> REVEALING -> ACCEPTED ->
        -appeal-> COMMITTING -> REVEALING -appeal-fail-> ACCEPTED
        -appeal-> COMMITTING -> REVEALING -appeal-success->
        PENDING -> PROPOSING -> COMMITTING -> REVEALING -> ACCEPTED -> -no-appeal-> FINALIZED
    """
    transaction = init_dummy_transaction()
    nodes = get_nodes_specs(37)
    created_nodes = []
    transactions_processor = TransactionsProcessorMock(
        [transaction_to_dict(transaction)]
    )

    def get_vote():
        """
        Leader agrees + 4 validators agree.
        Appeal: 7 validators disagree. So appeal succeeds.
        Leader agrees + 10 validators agree.
        Appeal: 13 validators agree. So appeal fails.
        Appeal: 25 validators disagree. So appeal succeeds.
        Leader agrees + 34 validators agree.
        """
        if len(created_nodes) < transaction.num_of_initial_validators + 1:
            return Vote.AGREE
        if (len(created_nodes) >= transaction.num_of_initial_validators + 1) and (
            len(created_nodes) < 2 * transaction.num_of_initial_validators + 2 + 1
        ):
            return Vote.DISAGREE
        if (
            len(created_nodes) >= 2 * transaction.num_of_initial_validators + 2 + 1
        ) and (
            len(created_nodes) < 3 * (2 * transaction.num_of_initial_validators + 2) + 2
        ):
            return Vote.AGREE
        if (
            len(created_nodes)
            >= 3 * (2 * transaction.num_of_initial_validators + 2) + 2
        ) and (
            len(created_nodes)
            < 5 * (2 * transaction.num_of_initial_validators + 2) + 1 + 2
        ):
            return Vote.DISAGREE
        return Vote.AGREE

    event, *threads = setup_test_environment(
        consensus_algorithm, transactions_processor, nodes, created_nodes, get_vote
    )

    try:
        assert_transaction_status_match(
            transactions_processor, transaction, [TransactionStatus.ACCEPTED.value]
        )

        expected_nb_created_nodes = transaction.num_of_initial_validators + 1
        assert len(created_nodes) == expected_nb_created_nodes

        transaction_status_history = [
            TransactionStatus.ACTIVATED,
            TransactionStatus.PROPOSING,
            TransactionStatus.COMMITTING,
            TransactionStatus.REVEALING,
            TransactionStatus.ACCEPTED,
        ]
        assert transactions_processor.updated_transaction_status_history == {
            "transaction_hash": transaction_status_history
        }

        # Appeal successful
        timestamp_awaiting_finalization_1 = (
            transactions_processor.get_transaction_by_hash(transaction.hash)[
                "timestamp_awaiting_finalization"
            ]
        )

        appeal(transaction, transactions_processor)
        current_status = assert_transaction_status_match(
            transactions_processor,
            transaction,
            [TransactionStatus.PENDING.value, TransactionStatus.ACTIVATED.value],
        )

        transaction_status_history += [
            TransactionStatus.COMMITTING,
            TransactionStatus.REVEALING,
            TransactionStatus.PENDING,
        ]
        if current_status == TransactionStatus.ACTIVATED.value:
            transaction_status_history.append(TransactionStatus.ACTIVATED)

        assert transactions_processor.updated_transaction_status_history == {
            "transaction_hash": transaction_status_history
        }

        expected_nb_created_nodes += transaction.num_of_initial_validators + 2
        assert len(created_nodes) == expected_nb_created_nodes

        validator_set_addresses = get_validator_addresses(
            transaction, transactions_processor
        )
        old_leader_address = get_leader_address(transaction, transactions_processor)

        assert_transaction_status_match(
            transactions_processor, transaction, [TransactionStatus.ACCEPTED.value]
        )

        if current_status == TransactionStatus.PENDING.value:
            transaction_status_history.append(TransactionStatus.ACTIVATED)
        transaction_status_history += [
            TransactionStatus.PROPOSING,
            TransactionStatus.COMMITTING,
            TransactionStatus.REVEALING,
            TransactionStatus.ACCEPTED,
        ]
        assert transactions_processor.updated_transaction_status_history == {
            "transaction_hash": transaction_status_history
        }

        n_new = (2 * transaction.num_of_initial_validators + 2) - 1
        expected_nb_created_nodes += n_new + 1
        assert len(created_nodes) == expected_nb_created_nodes

        timestamp_awaiting_finalization_2 = (
            transactions_processor.get_transaction_by_hash(transaction.hash)[
                "timestamp_awaiting_finalization"
            ]
        )

        assert timestamp_awaiting_finalization_2 > timestamp_awaiting_finalization_1

        check_validator_count(transaction, transactions_processor, n_new)

        new_leader_address = get_leader_address(transaction, transactions_processor)

        assert new_leader_address != old_leader_address
        assert new_leader_address in validator_set_addresses

        # Appeal fails
        appeal(transaction, transactions_processor)
        assert_transaction_status_change_and_match(
            transactions_processor, transaction, [TransactionStatus.ACCEPTED.value]
        )

        transaction_status_history += [
            TransactionStatus.COMMITTING,
            TransactionStatus.REVEALING,
            TransactionStatus.ACCEPTED,
        ]
        assert transactions_processor.updated_transaction_status_history == {
            "transaction_hash": transaction_status_history
        }

        expected_nb_created_nodes += n_new + 2
        assert len(created_nodes) == expected_nb_created_nodes

        check_validator_count(transaction, transactions_processor, 2 * n_new + 2)

        timestamp_awaiting_finalization_3 = (
            transactions_processor.get_transaction_by_hash(transaction.hash)[
                "timestamp_awaiting_finalization"
            ]
        )

        assert timestamp_awaiting_finalization_3 == timestamp_awaiting_finalization_2

        validator_set_addresses_after_appeal_fail = get_validator_addresses(
            transaction, transactions_processor
        )

        # Appeal successful
        appeal(transaction, transactions_processor)
        current_status = assert_transaction_status_match(
            transactions_processor,
            transaction,
            [TransactionStatus.PENDING.value, TransactionStatus.ACTIVATED.value],
        )

        transaction_status_history += [
            TransactionStatus.COMMITTING,
            TransactionStatus.REVEALING,
            TransactionStatus.PENDING,
        ]
        if current_status == TransactionStatus.ACTIVATED.value:
            transaction_status_history.append(TransactionStatus.ACTIVATED)

        assert transactions_processor.updated_transaction_status_history == {
            "transaction_hash": transaction_status_history
        }

        expected_nb_created_nodes += 2 * n_new + 3
        assert len(created_nodes) == expected_nb_created_nodes

        validator_set_addresses = get_validator_addresses(
            transaction, transactions_processor
        )
        old_leader_address = get_leader_address(transaction, transactions_processor)

        assert validator_set_addresses_after_appeal_fail.issubset(
            validator_set_addresses
        )

        assert_transaction_status_match(
            transactions_processor, transaction, [TransactionStatus.FINALIZED.value]
        )

        if current_status == TransactionStatus.PENDING.value:
            transaction_status_history.append(TransactionStatus.ACTIVATED)
        transaction_status_history += [
            TransactionStatus.PROPOSING,
            TransactionStatus.COMMITTING,
            TransactionStatus.REVEALING,
            TransactionStatus.ACCEPTED,
            TransactionStatus.FINALIZED,
        ]
        assert transactions_processor.updated_transaction_status_history == {
            "transaction_hash": transaction_status_history
        }

        expected_nb_created_nodes += 3 * n_new + 2 + 1
        assert len(created_nodes) == expected_nb_created_nodes

        timestamp_awaiting_finalization_4 = (
            transactions_processor.get_transaction_by_hash(transaction.hash)[
                "timestamp_awaiting_finalization"
            ]
        )

        assert timestamp_awaiting_finalization_4 > timestamp_awaiting_finalization_3

        check_validator_count(transaction, transactions_processor, 3 * n_new + 2)

        new_leader_address = get_leader_address(transaction, transactions_processor)

        assert new_leader_address != old_leader_address
        assert new_leader_address in validator_set_addresses
    finally:
        cleanup_threads(event, threads)


@pytest.mark.asyncio
async def test_exec_undetermined_appeal(consensus_algorithm):
    """
    Test that a transaction can be appealed when it is in the undetermined state. This verifies that:
    1. The transaction can enter appeal state after being in the undetermined state
    2. New validators are selected to process the appeal and the old leader is removed
    3. All possible path regarding undetermined appeals are correctly handled.
    4. The transaction is finalized after the appeal window
    The transaction flow:
        UNDETERMINED -appeal-fail-> UNDETERMINED
        -appeal-success-after-3-rounds-> ACCEPTED
        -successful-appeal-> PENDING -> UNDETERMINED -appeal-fail-> FINALIZED
    """
    transaction = init_dummy_transaction("transaction_hash_1")
    transaction.config_rotation_rounds = 4
    nodes = get_nodes_specs(
        2 * (2 * (2 * (2 * transaction.num_of_initial_validators + 2) + 2) + 2)
        + 2
        + (4 * (transaction.config_rotation_rounds))
        + 2
    )
    created_nodes = []
    transactions_processor = TransactionsProcessorMock(
        [transaction_to_dict(transaction)]
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
        """
        Leader disagrees + 4 validators disagree for 5 rounds
        Appeal leader fails: leader disagrees + 10 validators disagree for 5 rounds
        Appeal leader succeeds: leader disagrees + 22 validators disagree for 2 rounds then agree for 1 round

        Appeal validator succeeds: 25 validators disagree
        Leader disagrees + 46 validators disagree for 5 rounds
        Appeal leader fails: leader disagrees + 94 validators disagree for 5 rounds
        """
        exec_rounds = transaction.config_rotation_rounds + 1
        n_first = transaction.num_of_initial_validators
        n_second = 2 * n_first + 1
        n_third = 2 * n_second + 1
        nb_first_agree = (
            ((n_first + 1) * exec_rounds)
            + ((n_second + 1) * exec_rounds)
            + ((n_third + 1) * 2)
        )
        if (len(created_nodes) >= nb_first_agree) and (
            len(created_nodes) < nb_first_agree + n_third + 1
        ):
            return Vote.AGREE
        else:
            return Vote.DISAGREE

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
        check_contract_state(contract_db, transaction.to_address, {}, {})
        assert_transaction_status_match(
            transactions_processor, transaction, [TransactionStatus.UNDETERMINED.value]
        )

        transaction_status_history = [
            TransactionStatus.ACTIVATED,
            *[
                TransactionStatus.PROPOSING,
                TransactionStatus.COMMITTING,
                TransactionStatus.REVEALING,
            ]
            * (transaction.config_rotation_rounds + 1),
            TransactionStatus.UNDETERMINED,
        ]
        assert transactions_processor.updated_transaction_status_history == {
            "transaction_hash_1": transaction_status_history
        }

        nb_validators = transaction.num_of_initial_validators
        nb_created_nodes = (transaction.num_of_initial_validators + 1) * (
            transaction.config_rotation_rounds + 1
        )
        check_validator_count(transaction, transactions_processor, nb_validators)
        assert len(created_nodes) == nb_created_nodes

        check_contract_state(contract_db, transaction.to_address, {}, {})

        appeal(transaction, transactions_processor)
        assert_transaction_status_change_and_match(
            transactions_processor, transaction, [TransactionStatus.UNDETERMINED.value]
        )

        transaction_status_history += [
            *[
                TransactionStatus.PROPOSING,
                TransactionStatus.COMMITTING,
                TransactionStatus.REVEALING,
            ]
            * (transaction.config_rotation_rounds + 1),
            TransactionStatus.UNDETERMINED,
        ]
        assert transactions_processor.updated_transaction_status_history == {
            "transaction_hash_1": transaction_status_history
        }

        nb_validators += nb_validators + 1
        nb_created_nodes += (nb_validators + 1) * (
            transaction.config_rotation_rounds + 1
        )
        check_validator_count(transaction, transactions_processor, nb_validators)
        assert len(created_nodes) == nb_created_nodes

        assert (
            transactions_processor.get_transaction_by_hash(transaction.hash)[
                "appeal_processing_time"
            ]
            > 0
        )
        assert (
            transactions_processor.get_transaction_by_hash(transaction.hash)[
                "timestamp_appeal"
            ]
            is not None
        )

        check_contract_state(contract_db, transaction.to_address, {}, {})

        appeal(transaction, transactions_processor)
        assert_transaction_status_match(
            transactions_processor, transaction, [TransactionStatus.ACCEPTED.value]
        )

        transaction_status_history += [
            *[
                TransactionStatus.PROPOSING,
                TransactionStatus.COMMITTING,
                TransactionStatus.REVEALING,
            ]
            * 3,
            TransactionStatus.ACCEPTED,
        ]
        assert transactions_processor.updated_transaction_status_history == {
            "transaction_hash_1": transaction_status_history
        }

        check_contract_state_with_timeout(
            contract_db, transaction.to_address, {"state_var": "1"}, {}
        )

        nb_validators += nb_validators + 1
        nb_created_nodes += (nb_validators + 1) * 3
        check_validator_count(transaction, transactions_processor, nb_validators)
        assert len(created_nodes) == nb_created_nodes

        assert (
            transactions_processor.get_transaction_by_hash(transaction.hash)[
                "appeal_processing_time"
            ]
            == 0
        )
        assert (
            transactions_processor.get_transaction_by_hash(transaction.hash)[
                "timestamp_appeal"
            ]
            is None
        )

        appeal(transaction, transactions_processor)
        current_status = assert_transaction_status_match(
            transactions_processor,
            transaction,
            [TransactionStatus.PENDING.value, TransactionStatus.ACTIVATED.value],
        )

        transaction_status_history += [
            TransactionStatus.COMMITTING,
            TransactionStatus.REVEALING,
            TransactionStatus.PENDING,
        ]
        if current_status == TransactionStatus.ACTIVATED.value:
            transaction_status_history.append(TransactionStatus.ACTIVATED)

        assert transactions_processor.updated_transaction_status_history == {
            "transaction_hash_1": transaction_status_history
        }

        nb_created_nodes += nb_validators + 2
        nb_validators += nb_validators + 2
        check_validator_count(transaction, transactions_processor, nb_validators)
        assert len(created_nodes) == nb_created_nodes

        assert_transaction_status_match(
            transactions_processor, transaction, [TransactionStatus.UNDETERMINED.value]
        )

        if current_status == TransactionStatus.PENDING.value:
            transaction_status_history.append(TransactionStatus.ACTIVATED)
        transaction_status_history += [
            *[
                TransactionStatus.PROPOSING,
                TransactionStatus.COMMITTING,
                TransactionStatus.REVEALING,
            ]
            * (transaction.config_rotation_rounds + 1),
            TransactionStatus.UNDETERMINED,
        ]
        assert transactions_processor.updated_transaction_status_history == {
            "transaction_hash_1": transaction_status_history
        }

        nb_validators -= 1
        nb_created_nodes += (nb_validators + 1) * (
            transaction.config_rotation_rounds + 1
        )
        check_validator_count(transaction, transactions_processor, nb_validators)
        assert len(created_nodes) == nb_created_nodes

        check_contract_state_with_timeout(contract_db, transaction.to_address, {}, {})

        appeal(transaction, transactions_processor)
        assert_transaction_status_match(
            transactions_processor, transaction, [TransactionStatus.FINALIZED.value]
        )

        transaction_status_history += [
            *[
                TransactionStatus.PROPOSING,
                TransactionStatus.COMMITTING,
                TransactionStatus.REVEALING,
            ]
            * (transaction.config_rotation_rounds + 1),
            TransactionStatus.UNDETERMINED,
            TransactionStatus.FINALIZED,
        ]
        assert transactions_processor.updated_transaction_status_history == {
            "transaction_hash_1": transaction_status_history
        }

        nb_validators += nb_validators + 1
        nb_created_nodes += (nb_validators + 1) * (
            transaction.config_rotation_rounds + 1
        )
        check_validator_count(transaction, transactions_processor, nb_validators)
        assert len(created_nodes) == nb_created_nodes

        assert (
            transactions_processor.get_transaction_by_hash(transaction.hash)[
                "appeal_processing_time"
            ]
            > 0
        )
        assert (
            transactions_processor.get_transaction_by_hash(transaction.hash)[
                "timestamp_appeal"
            ]
            is not None
        )

        check_contract_state(contract_db, transaction.to_address, {}, {})
    finally:
        cleanup_threads(event, threads)


@pytest.mark.asyncio
async def test_exec_validator_appeal_success_with_rollback_second_tx(
    consensus_algorithm,
):
    """
    Test that a validator appeal is successful and the second transaction (future transaction) is rolled back to pending state.
    Also check the contract state is correctly updated and restored during these changes.
    """
    transaction_1 = init_dummy_transaction("transaction_hash_1")
    transaction_2 = init_dummy_transaction("transaction_hash_2")
    nodes = get_nodes_specs(2 * transaction_1.num_of_initial_validators + 2)
    created_nodes = []
    transactions_processor = TransactionsProcessorMock(
        [transaction_to_dict(transaction_1), transaction_to_dict(transaction_2)]
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
        Transaction 1: Leader agrees + 4 validators agree.
        Transaction 2: Leader agrees + 4 validators agree.
        Transaction 1 Appeal: 7 disagree. So appeal succeeds.
        Transaction 1: Leader agrees + 10 validators agree.
        Transaction 2: Leader agrees + 4 validators agree. Recalculation because of rollback.
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
        check_contract_state(contract_db, contract_address, {}, {})

        assert_transaction_status_match(
            transactions_processor, transaction_1, [TransactionStatus.ACCEPTED.value]
        )
        assert len(created_nodes) == transaction_1.num_of_initial_validators + 1

        check_contract_state_with_timeout(
            contract_db, contract_address, {"state_var": "1"}, {}
        )

        assert_transaction_status_match(
            transactions_processor, transaction_2, [TransactionStatus.ACCEPTED.value]
        )
        assert (
            len(created_nodes)
            == transaction_1.num_of_initial_validators
            + 1
            + transaction_2.num_of_initial_validators
            + 1
        )

        check_contract_state_with_timeout(
            contract_db, contract_address, {"state_var": "12"}, {}
        )

        appeal(transaction_1, transactions_processor)

        assert_transaction_status_match(
            transactions_processor,
            transaction_1,
            [TransactionStatus.PENDING.value, TransactionStatus.ACTIVATED.value],
        )

        check_contract_state_with_timeout(contract_db, contract_address, {}, {})

        assert_transaction_status_match(
            transactions_processor, transaction_1, [TransactionStatus.ACCEPTED.value]
        )

        check_contract_state_with_timeout(
            contract_db, contract_address, {"state_var": "1"}, {}
        )

        assert_transaction_status_match(
            transactions_processor, transaction_2, [TransactionStatus.ACCEPTED.value]
        )

        check_contract_state_with_timeout(
            contract_db, contract_address, {"state_var": "12"}, {}
        )

        assert transactions_processor.updated_transaction_status_history == {
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


@pytest.mark.asyncio
async def test_exec_leader_appeal_succes_with_rollback_second_tx(consensus_algorithm):
    """
    Test that a leader appeal is successful and the second transaction (future transaction) is rolled back to pending state.
    Also check the contract state is correctly updated these changes.
    """
    transaction_1 = init_dummy_transaction("transaction_hash_1")
    transaction_2 = init_dummy_transaction("transaction_hash_2")
    transaction_1.config_rotation_rounds = 3
    nodes = get_nodes_specs(5 * transaction_1.num_of_initial_validators + 1)
    created_nodes = []
    transactions_processor = TransactionsProcessorMock(
        [transaction_to_dict(transaction_1), transaction_to_dict(transaction_2)]
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
        Transaction 1: Leader disagrees + 4 validators disagree for 4 rounds.
        Transaction 2: Leader agrees + 4 validators agree.

        Transaction 1 Appeal: new leader agrees + 10 validators agree.
        Transaction 2: Leader agrees + 4 validators agree.
        """
        exec_rounds = transaction_1.config_rotation_rounds + 1
        if (
            len(created_nodes)
            < (transaction_1.num_of_initial_validators + 1) * exec_rounds
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
        check_contract_state(contract_db, contract_address, {}, {})

        assert_transaction_status_match(
            transactions_processor,
            transaction_1,
            [TransactionStatus.UNDETERMINED.value],
        )
        check_contract_state(contract_db, contract_address, {}, {})

        assert_transaction_status_match(
            transactions_processor, transaction_2, [TransactionStatus.ACCEPTED.value]
        )
        check_contract_state_with_timeout(
            contract_db, contract_address, {"state_var": "2"}, {}
        )

        appeal(transaction_1, transactions_processor)

        assert_transaction_status_match(
            transactions_processor, transaction_1, [TransactionStatus.ACCEPTED.value]
        )
        check_contract_state_with_timeout(
            contract_db, contract_address, {"state_var": "1"}, {}
        )

        assert_transaction_status_match(
            transactions_processor, transaction_2, [TransactionStatus.ACCEPTED.value]
        )
        check_contract_state_with_timeout(
            contract_db, contract_address, {"state_var": "12"}, {}
        )

        assert transactions_processor.updated_transaction_status_history == {
            "transaction_hash_1": [
                TransactionStatus.ACTIVATED,
                *[
                    TransactionStatus.PROPOSING,
                    TransactionStatus.COMMITTING,
                    TransactionStatus.REVEALING,
                ]
                * (transaction_1.config_rotation_rounds + 1),
                TransactionStatus.UNDETERMINED,
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


@pytest.mark.asyncio
async def test_exec_leader_timeout_appeal_failed(consensus_algorithm):
    """
    Test a leader timeout appeal that fails and that the transaction is finalized after awaiting finalization.
    """
    # Initialize transaction, nodes, and get_vote function
    transaction = init_dummy_transaction()
    nodes = get_nodes_specs(DEFAULT_VALIDATORS_COUNT + 1)
    created_nodes = []
    transactions_processor = TransactionsProcessorMock(
        [transaction_to_dict(transaction)]
    )

    def get_vote():
        return Vote.AGREE

    def get_timeout():
        """
        First leader returns timeout
        """
        if len(created_nodes) < 2:
            return True
        else:
            return False

    # Use the helper function to set up the test environment
    event, *threads = setup_test_environment(
        consensus_algorithm,
        transactions_processor,
        nodes,
        created_nodes,
        get_vote,
        get_timeout,
    )

    try:
        assert_transaction_status_match(
            transactions_processor,
            transaction,
            [TransactionStatus.LEADER_TIMEOUT.value],
        )
        assert len(created_nodes) == 1

        address_first_leader_timeout = get_leader_address(
            transaction, transactions_processor
        )

        timestamp_awaiting_finalization_1 = (
            transactions_processor.get_transaction_by_hash(transaction.hash)[
                "timestamp_awaiting_finalization"
            ]
        )

        appeal(transaction, transactions_processor)

        assert_transaction_status_change_and_match(
            transactions_processor, transaction, [TransactionStatus.FINALIZED.value]
        )

        assert len(created_nodes) == 2

        assert address_first_leader_timeout != get_leader_address(
            transaction, transactions_processor
        )

        assert timestamp_awaiting_finalization_1 == (
            transactions_processor.get_transaction_by_hash(transaction.hash)[
                "timestamp_awaiting_finalization"
            ]
        )

        assert transactions_processor.updated_transaction_status_history == {
            "transaction_hash": [
                TransactionStatus.ACTIVATED,
                TransactionStatus.PROPOSING,
                TransactionStatus.LEADER_TIMEOUT,
                TransactionStatus.PROPOSING,
                TransactionStatus.LEADER_TIMEOUT,
                TransactionStatus.FINALIZED,
            ]
        }

        assert [
            "Leader Timeout",
            "Leader Timeout Appeal Failed",
        ] == get_consensus_rounds_names(transaction, transactions_processor)

    finally:
        cleanup_threads(event, threads)


@pytest.mark.asyncio
async def test_exec_leader_timeout_appeal_success(consensus_algorithm):
    """
    Test a leader timeout appeal that succeeds. The new leader receipt is validated and agreed on by the validator set. The transaction is accepted.
    The second transaction is put back to pending.
    """
    transaction_1 = init_dummy_transaction("transaction_hash_1")
    transaction_2 = init_dummy_transaction("transaction_hash_2")
    nodes = get_nodes_specs(DEFAULT_VALIDATORS_COUNT + 1)
    created_nodes = []
    transactions_processor = TransactionsProcessorMock(
        [transaction_to_dict(transaction_1), transaction_to_dict(transaction_2)]
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
        return Vote.AGREE

    def get_timeout():
        """
        First leader returns timeout
        """
        if len(created_nodes) < 1:
            return True
        else:
            return False

    # Use the helper function to set up the test environment
    event, *threads = setup_test_environment(
        consensus_algorithm,
        transactions_processor,
        nodes,
        created_nodes,
        get_vote,
        get_timeout,
        contract_db,
    )

    try:
        contract_address = transaction_1.to_address
        check_contract_state(contract_db, contract_address, {}, {})

        assert_transaction_status_match(
            transactions_processor,
            transaction_1,
            [TransactionStatus.LEADER_TIMEOUT.value],
        )

        check_contract_state(contract_db, contract_address, {}, {})

        assert len(created_nodes) == 1

        leader_timeout_validators_addresses = get_leader_timeout_validators_addresses(
            transaction_1, transactions_processor
        )

        timestamp_awaiting_finalization_1 = (
            transactions_processor.get_transaction_by_hash(transaction_1.hash)[
                "timestamp_awaiting_finalization"
            ]
        )

        assert_transaction_status_match(
            transactions_processor, transaction_2, [TransactionStatus.ACCEPTED.value]
        )
        check_contract_state_with_timeout(
            contract_db, contract_address, {"state_var": "2"}, {}
        )

        assert len(created_nodes) == 1 + DEFAULT_VALIDATORS_COUNT + 1

        appeal(transaction_1, transactions_processor)

        assert_transaction_status_change_and_match(
            transactions_processor, transaction_1, [TransactionStatus.ACCEPTED.value]
        )

        check_contract_state_with_timeout(
            contract_db, contract_address, {"state_var": "1"}, {}
        )

        assert len(created_nodes) == 1 + 2 * (DEFAULT_VALIDATORS_COUNT + 1)

        address_second_leader_timeout = get_leader_address(
            transaction_1, transactions_processor
        )

        committed_validator_addresses = get_validator_addresses(
            transaction_1, transactions_processor
        )

        assert leader_timeout_validators_addresses.issubset(
            {address_second_leader_timeout} | committed_validator_addresses
        )

        assert timestamp_awaiting_finalization_1 != (
            transactions_processor.get_transaction_by_hash(transaction_1.hash)[
                "timestamp_awaiting_finalization"
            ]
        )

        assert_transaction_status_change_and_match(
            transactions_processor, transaction_2, [TransactionStatus.ACCEPTED.value]
        )

        assert transactions_processor.updated_transaction_status_history == {
            "transaction_hash_1": [
                TransactionStatus.ACTIVATED,
                TransactionStatus.PROPOSING,
                TransactionStatus.LEADER_TIMEOUT,
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

        assert ["Leader Timeout", "Accepted"] == get_consensus_rounds_names(
            transaction_1, transactions_processor
        )
        assert ["Accepted", "Accepted"] == get_consensus_rounds_names(
            transaction_2, transactions_processor
        )

        check_contract_state_with_timeout(
            contract_db, contract_address, {"state_var": "12"}, {}
        )

        assert len(created_nodes) == 1 + 3 * (DEFAULT_VALIDATORS_COUNT + 1)

    finally:
        cleanup_threads(event, threads)


@pytest.mark.asyncio
async def test_exec_leader_timeout_during_leader_appeal(consensus_algorithm):
    """
    Test a leader timeout appeal that succeeds during the leader appeal. Second transaction is in accepted state.
    Let first transaction never go to accepted state, hence the second transaction is not put back to pending.
    """
    transaction_1 = init_dummy_transaction("transaction_hash_1")
    transaction_2 = init_dummy_transaction("transaction_hash_2")
    transaction_1.config_rotation_rounds = 0
    nodes = get_nodes_specs(3 * DEFAULT_VALIDATORS_COUNT + 2)
    created_nodes = []
    transactions_processor = TransactionsProcessorMock(
        [transaction_to_dict(transaction_1), transaction_to_dict(transaction_2)]
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
        if len(created_nodes) < DEFAULT_VALIDATORS_COUNT + 1:
            return Vote.DISAGREE
        elif (len(created_nodes) >= DEFAULT_VALIDATORS_COUNT + 1) and (
            len(created_nodes) < 2 * (DEFAULT_VALIDATORS_COUNT + 1)
        ):
            return Vote.AGREE
        else:
            return Vote.DISAGREE

    def get_timeout():
        """
        First leader returns timeout
        """
        if len(created_nodes) == 2 * (DEFAULT_VALIDATORS_COUNT + 1):
            return True
        else:
            return False

    # Use the helper function to set up the test environment
    event, *threads = setup_test_environment(
        consensus_algorithm,
        transactions_processor,
        nodes,
        created_nodes,
        get_vote,
        get_timeout,
        contract_db,
    )

    try:
        contract_address = transaction_1.to_address
        check_contract_state(contract_db, contract_address, {}, {})

        assert_transaction_status_match(
            transactions_processor, transaction_2, [TransactionStatus.ACCEPTED.value]
        )

        check_contract_state_with_timeout(
            contract_db, contract_address, {"state_var": "2"}, {}
        )

        nb_created_nodes = 2 * (DEFAULT_VALIDATORS_COUNT + 1)
        assert len(created_nodes) == nb_created_nodes

        appeal(transaction_1, transactions_processor)

        assert_transaction_status_match(
            transactions_processor,
            transaction_1,
            [TransactionStatus.LEADER_TIMEOUT.value],
        )

        nb_created_nodes += 1
        assert len(created_nodes) == nb_created_nodes

        leader_timeout_validators_addresses = get_leader_timeout_validators_addresses(
            transaction_1, transactions_processor
        )

        appeal(transaction_1, transactions_processor)

        assert_transaction_status_match(
            transactions_processor,
            transaction_1,
            [TransactionStatus.UNDETERMINED.value],
        )

        nb_created_nodes += 2 * DEFAULT_VALIDATORS_COUNT + 1 + 1
        assert len(created_nodes) == nb_created_nodes

        address_leader_after_timeout = get_leader_address(
            transaction_1, transactions_processor
        )

        committed_validator_addresses = get_validator_addresses(
            transaction_1, transactions_processor
        )

        assert leader_timeout_validators_addresses.issubset(
            {address_leader_after_timeout} | committed_validator_addresses
        )

        assert transactions_processor.updated_transaction_status_history == {
            "transaction_hash_1": [
                TransactionStatus.ACTIVATED,
                TransactionStatus.PROPOSING,
                TransactionStatus.COMMITTING,
                TransactionStatus.REVEALING,
                TransactionStatus.UNDETERMINED,
                TransactionStatus.PROPOSING,
                TransactionStatus.LEADER_TIMEOUT,
                TransactionStatus.PROPOSING,
                TransactionStatus.COMMITTING,
                TransactionStatus.REVEALING,
                TransactionStatus.UNDETERMINED,
            ],
            "transaction_hash_2": [
                TransactionStatus.ACTIVATED,
                TransactionStatus.PROPOSING,
                TransactionStatus.COMMITTING,
                TransactionStatus.REVEALING,
                TransactionStatus.ACCEPTED,
            ],
        }

        assert [
            "Undetermined",
            "Leader Appeal Successful",
            "Undetermined",
        ] == get_consensus_rounds_names(transaction_1, transactions_processor)
        assert ["Accepted"] == get_consensus_rounds_names(
            transaction_2, transactions_processor
        )

        check_contract_state(contract_db, contract_address, {"state_var": "2"}, {})

    finally:
        cleanup_threads(event, threads)
