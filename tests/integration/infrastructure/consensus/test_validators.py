import pytest
from tests.common.request import payload, post_request_localhost
from tests.common.response import has_success_status


@pytest.mark.parametrize("execution_number", range(5))
def test_validators(execution_number):
    delete_validators_result = post_request_localhost(
        payload("sim_deleteAllValidators")
    ).json()
    assert has_success_status(delete_validators_result)

    response = post_request_localhost(payload("sim_createRandomValidator", 5)).json()
    assert has_success_status(response)

    validator = response["result"]
    first_address = validator["address"]

    # Duplicate validator
    response = post_request_localhost(
        payload(
            "sim_createValidator",
            validator["stake"],
            validator["provider"],
            validator["model"],
            validator["config"],
            validator["plugin"],
            validator["plugin_config"],
        )
    ).json()
    assert has_success_status(response)

    second_address = response["result"]["address"]

    # Delete both validators
    response = post_request_localhost(
        payload("sim_deleteValidator", first_address)
    ).json()
    assert has_success_status(response)

    response = post_request_localhost(
        payload("sim_deleteValidator", second_address)
    ).json()
    assert has_success_status(response)

    # Check no validators are left

    response = post_request_localhost(payload("sim_getAllValidators")).json()
    assert has_success_status(response)
    assert response["result"] == []
