import pytest
import os
from dotenv import load_dotenv

from tests.common.request import payload, post_request_localhost
from tests.common.response import has_success_status


@pytest.fixture
def setup_validators():
    created_validator_addresses = []

    def _setup(mock_response=None):
        nonlocal created_validator_addresses
        if mock_llms():
            # Mock mode: create validators with specific mock_response for this test
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
                            "api_key_env_var": "OPENAIKEY",
                            "api_url": "https://api.openai.com",
                            "mock_response": mock_response if mock_response else {},
                        },
                    )
                ).json()
                assert has_success_status(result)
                created_validator_addresses.append(result["result"]["address"])
        else:
            # Non-mock mode: only create validators if not enough exist
            validators_result = post_request_localhost(
                payload("sim_getAllValidators")
            ).json()
            assert has_success_status(validators_result)
            existing_validators = validators_result.get("result", [])
            if len(existing_validators) < 5:
                result = post_request_localhost(
                    payload(
                        "sim_createRandomValidators", 5, 8, 12, ["openai"], ["gpt-5.1"]
                    )
                ).json()
                assert has_success_status(result)
                # Track created validators for cleanup
                for validator in result.get("result", []):
                    created_validator_addresses.append(validator["address"])

    yield _setup

    # Only delete validators that THIS test created (not all validators)
    for address in created_validator_addresses:
        delete_result = post_request_localhost(
            payload("sim_deleteValidator", address)
        ).json()
        # Don't assert - validator might already be deleted by test logic
        has_success_status(delete_result)


def mock_llms():
    env_var = os.getenv("TEST_WITH_MOCK_LLMS", "false")  # default no mocking
    if env_var == "true":
        return True
    return False


def pytest_configure(config):
    load_dotenv(override=True)
