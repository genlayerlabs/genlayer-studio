import pytest
import os
from dotenv import load_dotenv

from tests.common.request import payload, post_request_localhost
from tests.common.response import has_success_status


@pytest.fixture
def setup_validators():
    def _setup(mock_response=None):
        if mock_llms():
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
        else:
            result = post_request_localhost(
                payload("sim_createRandomValidators", 5, 8, 12, ["openai"], ["gpt-4o"])
            ).json()
            assert has_success_status(result)

    yield _setup

    delete_validators_result = post_request_localhost(
        payload("sim_deleteAllValidators")
    ).json()
    assert has_success_status(delete_validators_result)


def mock_llms():
    env_var = os.getenv("TEST_WITH_MOCKS", "false")  # default no mocking
    if env_var == "true":
        return True
    return False


def pytest_configure(config):
    load_dotenv(override=True)
