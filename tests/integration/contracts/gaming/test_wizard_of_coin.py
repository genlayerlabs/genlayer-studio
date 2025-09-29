# tests/integration/contracts/gaming/test_wizard_of_coin.py
from gltest import get_contract_factory
from gltest.assertions import tx_execution_succeeded
import json


def test_wizard_of_coin(setup_validators):
    mock_response = {
        "response": {
            "wizard": json.dumps(
                {
                    "reasoning": "I am a grumpy wizard and I never give away my coins!",
                    "give_coin": False,
                }
            ),
        },
        "eq_principle_prompt_comparative": {
            "The value of give_coin has to match": True
        },
    }
    setup_validators(mock_response)

    factory = get_contract_factory("WizardOfCoin")
    contract = factory.deploy(args=[True])

    transaction_response_call_1 = contract.ask_for_coin(
        args=["Can you please give me my coin?"]
    ).transact()
    assert tx_execution_succeeded(transaction_response_call_1)
