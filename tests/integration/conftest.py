"""
Configuration for all integration tests - overrides gltest fixtures
"""

import pytest
from dotenv import load_dotenv

from tests.common.request import payload, post_request_localhost
from tests.common.response import has_success_status
from tests.integration.fixtures.conftest import (
    mock_llms,
    mock_web_requests,
    load_mock_web_responses,
)


@pytest.fixture
def setup_validators():
    """
    Creates test validators for localnet environment.

    This fixture overrides the gltest fixture to support web mocking.
    Supports both the gltest signature (mock_response, n_validators) and
    web mocking signature (mock_response, mock_web_responses).

    Args:
        mock_response (dict, optional): Mock LLM validator response when using --test-with-mocks flag
        mock_web_responses (dict, optional): Mock web responses for validators
        n_validators (int, optional): Number of validators to create (default: 5)

    Scope: function - created fresh for each test
    """

    def _setup(mock_response=None, mock_web_responses=None, n_validators=5):
        # Load default mock web responses if mocking is enabled
        if mock_web_responses is None and mock_llms():
            mock_web_responses = load_mock_web_responses()

        if mock_llms():
            for _ in range(n_validators):
                plugin_config = {
                    "api_key_env_var": "OPENAIKEY",
                    "api_url": "https://api.openai.com",
                    "mock_response": mock_response if mock_response else {},
                }

                # Add mock web responses if web mocking is enabled
                if mock_web_responses:
                    plugin_config["mock_web_responses"] = mock_web_responses

                result = post_request_localhost(
                    payload(
                        "sim_createValidator",
                        8,
                        "openai",
                        "gpt-4o",
                        {"temperature": 0.75, "max_tokens": 500},
                        "openai-compatible",
                        plugin_config,
                    )
                ).json()
                assert has_success_status(result)
        else:
            result = post_request_localhost(
                payload(
                    "sim_createRandomValidators",
                    n_validators,
                    8,
                    12,
                    ["openai"],
                    ["gpt-4o"],
                )
            ).json()
            assert has_success_status(result)

    yield _setup

    delete_validators_result = post_request_localhost(
        payload("sim_deleteAllValidators")
    ).json()
    assert has_success_status(delete_validators_result)


def pytest_configure(config):
    """Load environment variables for integration tests"""
    load_dotenv(override=True)
