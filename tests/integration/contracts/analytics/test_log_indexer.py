# tests/integration/contracts/analytics/test_log_indexer.py
from gltest import get_contract_factory
from gltest.assertions import tx_execution_succeeded

from fixtures.schemas.call_contract_function import (
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
    assert float(closest_vector_log_0["similarity"]) > 0.94
    assert float(closest_vector_log_0["similarity"]) < 0.95

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
    assert float(closest_vector_log_0_2["similarity"]) > 0.94
    assert float(closest_vector_log_0_2["similarity"]) < 0.95

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
    assert float(closest_vector_log_0_3["similarity"]) > 0.67
    assert float(closest_vector_log_0_3["similarity"]) < 0.68

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
