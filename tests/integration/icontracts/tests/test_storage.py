# tests/e2e/test_storage.py
from gltest import get_contract_factory
from gltest.assertions import tx_execution_succeeded

from tests.integration.icontracts.schemas.call_contract_function import (
    call_contract_function_response,
)
from tests.common.response import (
    assert_dict_struct,
)

INITIAL_STATE = "a"
UPDATED_STATE = "b"


def test_storage(setup_validators):
    setup_validators()
    factory = get_contract_factory(contract_file_path="examples/contracts/storage.py")
    contract = factory.deploy(args=[INITIAL_STATE])

    # Get initial state
    contract_state_1 = contract.get_storage(args=[]).call()
    assert contract_state_1 == INITIAL_STATE

    # Update State
    transaction_response_call_1 = contract.update_storage(
        args=[UPDATED_STATE]
    ).transact()
    assert tx_execution_succeeded(transaction_response_call_1)
    # Assert response format
    assert_dict_struct(transaction_response_call_1, call_contract_function_response)

    # Get Updated State
    contract_state_2 = contract.get_storage(args=[]).call()
    assert contract_state_2 == UPDATED_STATE
