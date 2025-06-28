# tests/e2e/test_storage.py
from gltest import get_contract_factory, default_account, create_account
from gltest.assertions import tx_execution_succeeded
import json

TOKEN_TOTAL_SUPPLY = 1000
TRANSFER_AMOUNT = 100


def test_llm_erc20(setup_validators):
    # Account Setup
    from_account_a = default_account
    from_account_b = create_account()

    mock_response = {
        "response": {
            "The balance of the sender": json.dumps(
                {
                    "transaction_success": True,
                    "transaction_error": "",
                    "updated_balances": {
                        from_account_a.address: TOKEN_TOTAL_SUPPLY - TRANSFER_AMOUNT,
                        from_account_b.address: TRANSFER_AMOUNT,
                    },
                }
            )
        },
        "eq_principle_prompt_non_comparative": {"The balance of the sender": True},
    }
    setup_validators(mock_response)

    # Deploy Contract
    factory = get_contract_factory("LlmErc20")
    contract = factory.deploy(args=[TOKEN_TOTAL_SUPPLY])

    ########################################
    ######### GET Initial State ############
    ########################################
    contract_state_1 = contract.get_balances(args=[])
    assert contract_state_1[from_account_a.address] == TOKEN_TOTAL_SUPPLY

    ########################################
    #### TRANSFER from User A to User B ####
    ########################################
    transaction_response_call_1 = contract.transfer(
        args=[TRANSFER_AMOUNT, from_account_b.address]
    )
    assert tx_execution_succeeded(transaction_response_call_1)

    # Assert response format
    # FIXME: error decoding https://linear.app/genlayer-labs/issue/DXP-233/error-in-decoding-function-genlayer-js-and-genlayer-py
    # assert_dict_struct(transaction_response_call_1, call_contract_function_response)

    # Get Updated State
    contract_state_2_1 = contract.get_balances(args=[])
    assert (
        contract_state_2_1[from_account_a.address]
        == TOKEN_TOTAL_SUPPLY - TRANSFER_AMOUNT
    )
    assert contract_state_2_1[from_account_b.address] == TRANSFER_AMOUNT

    # Get Updated State
    contract_state_2_2 = contract.get_balance_of(args=[from_account_a.address])
    assert contract_state_2_2 == TOKEN_TOTAL_SUPPLY - TRANSFER_AMOUNT

    # Get Updated State
    contract_state_2_3 = contract.get_balance_of(args=[from_account_b.address])
    assert contract_state_2_3 == TRANSFER_AMOUNT
