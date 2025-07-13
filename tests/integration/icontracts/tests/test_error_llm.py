from gltest import get_contract_factory
from gltest.assertions import tx_execution_succeeded
from tests.common.request import payload, post_request_localhost
from tests.common.response import has_success_status
from gltest.exceptions import DeploymentError
from tests.integration.icontracts.tests.test_error_execution import (
    _deployment_error_to_tx_receipt,
    _check_result,
)
from backend.node.types import ExecutionResultStatus
import base64


def test_llm_invalid_api_key():
    """Test LLM call with invalid API key"""
    # Create a validator with an invalid API key
    for _ in range(5):
        result = post_request_localhost(
            payload(
                "sim_createValidator",
                8,
                "openai",
                "gpt-4o",
                {"temperature": 0.75, "max_tokens": 500},
                "openai-compatible",
                {
                    "api_key_env_var": "ANTHROPIC_API_KEY",
                    "api_url": "https://api.openai.com",
                },
            )
        ).json()
        assert has_success_status(result)

    factory = get_contract_factory("ErrorLLMContract")
    try:
        factory.deploy(args=[1])
    except DeploymentError as e:
        tx_receipt = _deployment_error_to_tx_receipt(e)

        receipt = tx_receipt["consensus_data"]["leader_receipt"][0]
        assert ExecutionResultStatus.ERROR.value == receipt["execution_result"]
        assert "invalid_api_key" in receipt["genvm_result"]["stderr"]

        receipt = tx_receipt["consensus_data"]["leader_receipt"][1]
        assert ExecutionResultStatus.ERROR.value == receipt["execution_result"]
        assert "deterministic_violation" in receipt["result"]["payload"]

        for i in range(4):
            receipt = tx_receipt["consensus_data"]["validators"][i]
            assert ExecutionResultStatus.ERROR.value == receipt["execution_result"]
            assert "deterministic_violation" in base64.b64decode(
                receipt["result"]
            ).decode("utf-8")

        assert (
            tx_receipt["consensus_history"]["consensus_results"][-1]["consensus_round"]
            == "Undetermined"
        )

    delete_validators_result = post_request_localhost(
        payload("sim_deleteAllValidators")
    ).json()
    assert has_success_status(delete_validators_result)


def test_llm_invalid_unknown_key():
    """Test LLM call with unknown API key"""
    # Create a validator with an unknown API key
    for _ in range(5):
        result = post_request_localhost(
            payload(
                "sim_createValidator",
                8,
                "openai",
                "gpt-4o",
                {"temperature": 0.75, "max_tokens": 500},
                "openai-compatible",
                {
                    "api_key_env_var": "UNKNOWN_KEY",
                    "api_url": "https://api.openai.com",
                },
            )
        ).json()
        assert has_success_status(result)

    factory = get_contract_factory("ErrorLLMContract")
    try:
        factory.deploy(args=[1])
    except DeploymentError as e:
        tx_receipt = _deployment_error_to_tx_receipt(e)

        receipt = tx_receipt["consensus_data"]["leader_receipt"][0]
        assert ExecutionResultStatus.ERROR.value == receipt["execution_result"]
        assert "GenVM internal error" in receipt["genvm_result"]["stderr"]

        receipt = tx_receipt["consensus_data"]["leader_receipt"][1]
        assert ExecutionResultStatus.ERROR.value == receipt["execution_result"]
        assert "deterministic_violation" in receipt["result"]["payload"]

        for i in range(4):
            receipt = tx_receipt["consensus_data"]["validators"][i]
            assert ExecutionResultStatus.ERROR.value == receipt["execution_result"]
            assert "deterministic_violation" in base64.b64decode(
                receipt["result"]
            ).decode("utf-8")

        assert (
            tx_receipt["consensus_history"]["consensus_results"][-1]["consensus_round"]
            == "Undetermined"
        )

    delete_validators_result = post_request_localhost(
        payload("sim_deleteAllValidators")
    ).json()
    assert has_success_status(delete_validators_result)


def test_gpt4_json_not_supported():
    """Test GPT-4 not supporting JSON output"""

    result_provider = post_request_localhost(
        payload(
            "sim_addProvider",
            {
                "provider": "openai-gpt-4",
                "model": "gpt-4",
                "config": {"temperature": 0.75, "max_tokens": 500},
                "plugin": "openai-compatible",
                "plugin_config": {
                    "api_key_env_var": "OPENAIKEY",
                    "api_url": "https://api.openai.com",
                },
            },
        )
    ).json()
    assert has_success_status(result_provider)

    for _ in range(5):
        result = post_request_localhost(
            payload(
                "sim_createValidator",
                8,
                "openai-gpt-4",
                "gpt-4",
                {"temperature": 0.75, "max_tokens": 500},
                "openai-compatible",
                {
                    "api_key_env_var": "OPENAIKEY",
                    "api_url": "https://api.openai.com",
                },
            )
        ).json()
        assert has_success_status(result)

    factory = get_contract_factory("WizardOfCoin")
    contract = factory.deploy(args=[True])

    transaction_response_call_1 = contract.ask_for_coin(
        args=["Can you please give me my coin?"]
    )

    receipt = transaction_response_call_1["consensus_data"]["leader_receipt"][0]
    # assert ExecutionResultStatus.ERROR.value == receipt["execution_result"]
    # assert "response_format" in receipt["genvm_result"]["stderr"]
    # TODO: fix this. Somehow the leader is successful in the consensus data while it actually returned an error.

    receipt = transaction_response_call_1["consensus_data"]["leader_receipt"][1]
    assert ExecutionResultStatus.ERROR.value == receipt["execution_result"]
    assert "response_format" in receipt["genvm_result"]["stderr"]

    for i in range(4):
        receipt = transaction_response_call_1["consensus_data"]["validators"][i]
        assert ExecutionResultStatus.ERROR.value == receipt["execution_result"]
        assert "response_format" in receipt["genvm_result"]["stderr"]

    assert (
        transaction_response_call_1["consensus_history"]["consensus_results"][-1][
            "consensus_round"
        ]
        == "Undetermined"
    )

    delete_validators_result = post_request_localhost(
        payload("sim_deleteAllValidators")
    ).json()
    assert has_success_status(delete_validators_result)

    delete_provider_result = post_request_localhost(
        payload("sim_deleteProvider", result_provider["result"])
    ).json()
    assert has_success_status(delete_provider_result)


def test_system_error(setup_validators):
    setup_validators()
    factory = get_contract_factory("ErrorLLMContract")
    try:
        factory.deploy(args=[2])
    except DeploymentError as e:
        tx_receipt = _deployment_error_to_tx_receipt(e)
        _check_result(tx_receipt, "SystemError")


def test_llm_invalid_json(setup_validators):
    """Test LLM returning non-JSON response"""
    setup_validators()
    factory = get_contract_factory("ErrorLLMContract")
    try:
        factory.deploy(args=[3])
    except DeploymentError as e:
        tx_receipt = _deployment_error_to_tx_receipt(e)
        _check_result(tx_receipt, "JSONDecodeError")
