# tests/e2e/test_storage.py
from gltest import get_contract_factory, create_account
from gltest.assertions import tx_execution_succeeded
import json

TOKEN_TOTAL_SUPPLY = 1000
TRANSFER_AMOUNT = 100

# Substring guaranteed to appear in the LlmErc20 contract's exec_prompt
# call (see examples/contracts/llm_erc20.py — the prompt opens with
# "You keep track of transactions between users..."). The Lua mock
# plugin in backend/node/llm.lua matches mock_response keys against the
# prompt via string.find; a non-matching key falls through to the real
# LLM provider, which is non-deterministic. The previous key
# ("The balance of the sender") came from the eq_principle criteria
# that USED to be concatenated to the LLM prompt — the contract has
# since moved to gl.eq_principle.strict_eq, so that string is no longer
# in the prompt and the mock never matched.
_PROMPT_MATCH_KEY = "transactions between users"


def test_llm_erc20(setup_validators, default_account):
    # Account Setup
    from_account_a = default_account
    from_account_b = create_account()

    mock_response = {
        "response": {
            _PROMPT_MATCH_KEY: json.dumps(
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
        "eq_principle_prompt_non_comparative": {_PROMPT_MATCH_KEY: True},
    }
    setup_validators(mock_response)

    # Deploy Contract
    factory = get_contract_factory("LlmErc20")
    contract = factory.deploy(args=[TOKEN_TOTAL_SUPPLY])

    ########################################
    ######### GET Initial State ############
    ########################################
    contract_state_1 = contract.get_balances(args=[]).call()
    assert contract_state_1[from_account_a.address] == TOKEN_TOTAL_SUPPLY

    ########################################
    #### TRANSFER from User A to User B ####
    ########################################
    transaction_response_call_1 = contract.transfer(
        args=[TRANSFER_AMOUNT, from_account_b.address]
    ).transact()
    assert tx_execution_succeeded(transaction_response_call_1)

    # Get Updated State
    contract_state_2_1 = contract.get_balances(args=[]).call()
    assert (
        contract_state_2_1[from_account_a.address]
        == TOKEN_TOTAL_SUPPLY - TRANSFER_AMOUNT
    )
    assert contract_state_2_1[from_account_b.address] == TRANSFER_AMOUNT

    # Get Updated State
    contract_state_2_2 = contract.get_balance_of(args=[from_account_a.address]).call()
    assert contract_state_2_2 == TOKEN_TOTAL_SUPPLY - TRANSFER_AMOUNT

    # Get Updated State
    contract_state_2_3 = contract.get_balance_of(args=[from_account_b.address]).call()
    assert contract_state_2_3 == TRANSFER_AMOUNT
