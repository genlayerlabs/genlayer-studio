# tests/e2e/test_storage.py
from gltest import get_contract_factory
from gltest.assertions import tx_execution_succeeded

from tests.integration.icontracts.schemas.call_contract_function import (
    call_contract_function_response,
)
from tests.common.response import assert_dict_struct


def test_log_indexer(setup_validators):
    setup_validators()
    # Deploy Contract
    factory = get_contract_factory("LogIndexer")
    contract = factory.deploy(args=[])

    # ##########################################
    # ##### Get closest vector when empty ######
    # ##########################################
    closest_vector_log_0 = contract.get_closest_vector(args=["I like mango"]).call()
    assert closest_vector_log_0 is None

    # ########################################
    # ############## Add log 0 ###############
    # ########################################
    transaction_response_add_log_0 = contract.add_log(
        args=["I like to eat mango", 0]
    ).transact()
    assert tx_execution_succeeded(transaction_response_add_log_0)
    assert_dict_struct(transaction_response_add_log_0, call_contract_function_response)

    # ########################################
    # ##### Get closest vector to log 0 ######
    # ########################################
    closest_vector_log_0 = contract.get_closest_vector(args=["I like mango"]).call()
    closest_vector_log_0 = closest_vector_log_0
    assert closest_vector_log_0["id"] == 0
    assert closest_vector_log_0["text"] == "I like to eat mango"
    assert 0 < float(closest_vector_log_0["similarity"]) < 1

    # ########################################
    # ############## Add log 1 ###############
    # ########################################
    transaction_response_add_log_1 = contract.add_log(
        args=["I like carrots", 1]
    ).transact()
    assert tx_execution_succeeded(transaction_response_add_log_1)

    # ########################################
    # ##### Get closest vector to log 1 ######
    # ########################################
    closest_vector_log_1 = contract.get_closest_vector(args=["I like carrots"]).call()
    closest_vector_log_1 = closest_vector_log_1
    assert float(closest_vector_log_1["similarity"]) == 1

    # ########################################
    # ########### Update log 0 ##############
    # ########################################
    transaction_response_update_log_0 = contract.update_log(
        args=[0, "I like to eat a lot of mangoes"]
    ).transact()
    assert tx_execution_succeeded(transaction_response_update_log_0)

    # ########################################
    # ###### Get closest vector to log 0 #####
    # ########################################
    closest_vector_log_0_2 = contract.get_closest_vector(
        args=["I like mango a lot"]
    ).call()
    closest_vector_log_0_2 = closest_vector_log_0_2
    assert closest_vector_log_0_2["id"] == 0
    assert closest_vector_log_0_2["text"] == "I like to eat a lot of mangoes"
    assert 0 < float(closest_vector_log_0_2["similarity"]) < 1

    # ########################################
    # ########### Remove log 0 ##############
    # ########################################
    transaction_response_remove_log_0 = contract.remove_log(args=[0]).transact()
    assert tx_execution_succeeded(transaction_response_remove_log_0)

    # ########################################
    # ##### Get closest vector to log 0 ######
    # ########################################
    closest_vector_log_0_3 = contract.get_closest_vector(
        args=["I like to eat mango"]
    ).call()
    closest_vector_log_0_3 = closest_vector_log_0_3
    assert closest_vector_log_0_3["id"] == 1
    assert closest_vector_log_0_3["text"] == "I like carrots"
    assert float(closest_vector_log_0_3["similarity"]) < float(
        closest_vector_log_0["similarity"]
    )

    # ########################################
    # ##### Test id uniqueness after deletion #
    # ########################################

    # Add third log
    transaction_response_add_log_2 = contract.add_log(
        args=["This is the third log", 3]
    ).transact()
    assert tx_execution_succeeded(transaction_response_add_log_2)

    # Check if new item got id 2
    closest_vector_log_2 = contract.get_closest_vector(
        args=["This is the third log"]
    ).call()
    assert float(closest_vector_log_2["similarity"]) > 0.99
    assert closest_vector_log_2["id"] == 3
    assert closest_vector_log_2["text"] == "This is the third log"

    # ########################################
    # ### Removed log_id becomes visible again
    # ###         after being re-added
    # ########################################
    transaction_response_remove_log_2 = contract.remove_log(args=[3]).transact()
    assert tx_execution_succeeded(transaction_response_remove_log_2)

    # tombstoned: must not be found anymore
    closest_vector_after_remove = contract.get_closest_vector(
        args=["This is the third log"]
    ).call()
    assert (
        closest_vector_after_remove is None
        or closest_vector_after_remove["id"] != 3
    )

    # re-add the same log_id via add_log (takes the "already indexed"
    # in-place-update branch, since log_id 3 is still in log_vector_ids)
    transaction_response_readd_log_2 = contract.add_log(
        args=["This is the third log, again", 3]
    ).transact()
    assert tx_execution_succeeded(transaction_response_readd_log_2)

    # must be visible again — the tombstone from remove_log must have
    # been cleared, not left permanently set
    closest_vector_after_readd = contract.get_closest_vector(
        args=["This is the third log, again"]
    ).call()
    assert closest_vector_after_readd is not None
    assert closest_vector_after_readd["id"] == 3
    assert closest_vector_after_readd["text"] == "This is the third log, again"

    # ########################################
    # ### Updating an existing log_id must
    # ###   re-embed, not just re-label
    # ########################################
    # log_id 3 currently holds "This is the third log, again" — an
    # unrelated topic. If add_log's "already indexed" branch only updated
    # .value (the old bug: VecDBElement.key, the stored vector, is
    # read-only and can't be changed in place) instead of removing and
    # re-inserting with a fresh embedding, this log would still be found
    # by queries about its OLD topic and missed by queries about its new
    # one, despite .text correctly reporting the new content.
    transaction_response_retopic_log_2 = contract.add_log(
        args=["Quantum computers use superconducting qubits", 3]
    ).transact()
    assert tx_execution_succeeded(transaction_response_retopic_log_2)

    # found by a query about the NEW topic — this alone proves re-embedding
    # happened: the OLD embedding ("This is the third log, again") has
    # nothing in common with quantum computing, so this could only match
    # if a fresh embedding was actually computed and stored.
    closest_vector_new_topic = contract.get_closest_vector(
        args=["How do quantum computers work"]
    ).call()
    assert closest_vector_new_topic is not None
    assert closest_vector_new_topic["id"] == 3
    assert (
        closest_vector_new_topic["text"]
        == "Quantum computers use superconducting qubits"
    )
