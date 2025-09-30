# tests/e2e/test_company_naming.py
from gltest import get_contract_factory
from gltest.assertions import tx_execution_succeeded
import json


def test_company_naming(setup_validators):
    mock_response = {
        "response": {
            "expert business analyst": json.dumps(
                {
                    "analysis": "The company name 'GenLayer' aligns reasonably well with its description. For relevance, it scores 2 points because 'Gen' could imply generation or general, aligning with the creation and management role of the digital court and jurisdiction, while 'Layer' suggests a foundational or infrastructural role, relevant to the concept of a trust layer. In terms of memorability, it scores 2 points as 'GenLayer' is distinctive and easy to remember. For description match, it scores 3 points. The name captures the essence of a foundational layer but does not fully convey the complexity of the decentralized digital court or intelligent contracts. Lastly, for brand potential, it scores 2 points. The name and description together create a cohesive and futuristic brand identity, suggesting innovation in digital governance and contract enforcement.",
                    "score": 9,
                }
            ),
        },
    }

    setup_validators(mock_response)

    factory = get_contract_factory("CompanyNaming")
    contract = factory.deploy(args=[])

    company_name = "GenLayer"
    description = "Al-native trust layer and synthetic jurisdiction on-chain. Validators running diverse language models act as a decentralized digital court, resolving disputes and enforcing contracts. Intelligent Contracts interpret language, process unstructured data, and pull live web inputs, enabling autonomous systems to transact, govern, and settle decisions at machine speed."
    transaction_response_call_1 = contract.score_alignment(
        args=[company_name, description]
    ).transact()
    assert tx_execution_succeeded(transaction_response_call_1)

    score = contract.get_score(args=[company_name]).call()

    assert (
        score != 0
    )  # it means there was een agreement. The score is determined by the LLM so we cannot hardcode it to a specific value.
