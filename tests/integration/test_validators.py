from tests.common.request import payload, post_request_localhost
from tests.common.response import has_success_status


def test_validator_create_delete_smoke():
    """Smoke test: one create-delete cycle to verify GenVM LLM module restart works.

    Full CRUD coverage lives in tests/db-sqlalchemy/validators_registry_test.py
    which runs in <1s without GenVM overhead.
    """
    # Clean slate
    result = post_request_localhost(payload("sim_deleteAllValidators")).json()
    assert has_success_status(result)

    # Create one validator (triggers GenVM LLM module restart)
    result = post_request_localhost(payload("sim_createRandomValidator", 5)).json()
    assert has_success_status(result)
    address = result["result"]["address"]

    # Delete it (triggers another restart)
    result = post_request_localhost(payload("sim_deleteValidator", address)).json()
    assert has_success_status(result)

    # Verify clean
    result = post_request_localhost(payload("sim_getAllValidators")).json()
    assert has_success_status(result)
    assert result["result"] == []
