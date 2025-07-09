from gltest import get_contract_factory
from gltest.assertions import tx_execution_succeeded
from tests.common.request import payload, post_request_localhost
from tests.common.response import has_success_status
from gltest.exceptions import DeploymentError
from tests.integration.icontracts.tests.test_error_execution import (
    _deployment_error_to_tx_receipt,
    _check_result,
)


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

    delete_validators_result = post_request_localhost(
        payload("sim_deleteAllValidators")
    ).json()
    assert has_success_status(delete_validators_result)

    # "error":{"causes":["ModuleError { causes: [\"STATUS_NOT_OK\"], fatal: true, ctx: {\"body\": Map({\"error\": Map({\"code\": Str(\"invalid_api_key\"), \"message\": Str(\"Incorrect API key provided: sk-ant
    # consensus is stuck
    # TODO: fix this


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

    delete_validators_result = post_request_localhost(
        payload("sim_deleteAllValidators")
    ).json()
    assert has_success_status(delete_validators_result)

    # lib.get_first_from_table(llm.providers[provider_id].models).key gives an error. Model not registered because unknown key.
    # consensus is stuck
    # TODO: fix this


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
    assert tx_execution_succeeded(transaction_response_call_1)

    delete_validators_result = post_request_localhost(
        payload("sim_deleteAllValidators")
    ).json()
    assert has_success_status(delete_validators_result)

    delete_provider_result = post_request_localhost(
        payload("sim_deleteProvider", result_provider["result"])
    ).json()
    assert has_success_status(delete_provider_result)

    # Genvm: "error":{"causes":["ModuleError { causes: [\"STATUS_NOT_OK\"], fatal: true, ctx: {\"body\": Map({\"error\": Map({\"code\": Null, \"message\": Str(\"Invalid parameter: 'response_format' of type 'json_object' is not supported with this model.\"), \"param\": Str(\"response_format\"), \"type\": Str(\"invalid_request_error\")})})
    # consensus is stuck
    # TODO: fix this


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
