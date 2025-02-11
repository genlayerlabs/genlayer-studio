# tests/e2e/test_storage.py

import eth_utils

from tests.common.request import (
    deploy_intelligent_contract,
    write_intelligent_contract,
    payload,
    post_request_localhost,
    call_contract_method,
)
from tests.integration.contract_examples.mocks.football_prediction_market_get_contract_schema_for_code import (
    football_prediction_market_contract_schema,
)
from tests.integration.contract_examples.mocks.call_contract_function import (
    call_contract_function_response,
)

from tests.common.response import (
    assert_dict_struct,
    assert_dict_exact,
    has_success_status,
)


def test_football_prediction_market(setup_validators, from_account):
    # Get contract schema
    contract_code = open("examples/contracts/football_prediction_market.py", "r").read()
    result_schema = post_request_localhost(
        payload(
            "gen_getContractSchemaForCode",
            eth_utils.hexadecimal.encode_hex(contract_code),
        )
    ).json()
    assert has_success_status(result_schema)
    assert_dict_exact(result_schema, football_prediction_market_contract_schema)

    # Deploy Contract
    contract_address, transaction_response_deploy = deploy_intelligent_contract(
        from_account,
        contract_code,
        ["2024-06-26", "Georgia", "Portugal"],
    )
    assert has_success_status(transaction_response_deploy)

    ########################################
    ############# RESOLVE match ############
    ########################################
    transaction_response_call_1 = write_intelligent_contract(
        from_account,
        contract_address,
        "resolve",
        [],
    )
    assert has_success_status(transaction_response_call_1)

    # Assert response format
    assert_dict_struct(transaction_response_call_1, call_contract_function_response)

    # Get Updated State
    contract_state_2 = call_contract_method(
        contract_address, from_account, "get_resolution_data", []
    )

    assert contract_state_2["winner"] == 1
    assert contract_state_2["score"] == "2:0"
    assert contract_state_2["has_resolved"] == True
