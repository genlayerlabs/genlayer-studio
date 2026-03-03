import pytest
import os
from dotenv import load_dotenv
from typing import Any

from tests.common.request import payload, post_request_localhost
from tests.common.response import has_success_status


def get_provider_config() -> dict[str, str]:
    """
    Returns provider configuration for non-mock integration tests.

    Override via environment variables:
      TEST_PROVIDER        - provider name  (default: openai)
      TEST_PROVIDER_MODEL  - model name     (default: gpt-4o)

    Example (local Ollama):
      TEST_PROVIDER=ollama TEST_PROVIDER_MODEL=llama3 pytest ...
    """
    return {
        "provider": os.getenv("TEST_PROVIDER", "openai"),
        "model": os.getenv("TEST_PROVIDER_MODEL", "gpt-4o"),
    }


def get_mock_provider_config() -> dict[str, str]:
    """
    Returns provider configuration for mock (TEST_WITH_MOCK_LLMS=true) tests.

    Override via environment variables:
      TEST_MOCK_PROVIDER          - provider name      (default: openrouter)
      TEST_MOCK_MODEL             - model name         (default: @preset/rally-testnet-gpt-5-1)
      TEST_MOCK_API_KEY_ENV_VAR   - env var holding the API key (default: OPENROUTERAPIKEY)
      TEST_MOCK_API_URL           - base API URL       (default: https://openrouter.ai/api)
    """
    return {
        "provider": os.getenv("TEST_MOCK_PROVIDER", "openrouter"),
        "model": os.getenv("TEST_MOCK_MODEL", "@preset/rally-testnet-gpt-5-1"),
        "api_key_env_var": os.getenv("TEST_MOCK_API_KEY_ENV_VAR", "OPENROUTERAPIKEY"),
        "api_url": os.getenv("TEST_MOCK_API_URL", "https://openrouter.ai/api"),
    }


@pytest.fixture
def setup_validators():
    """
    Pytest fixture that ensures at least 5 validators exist before each test
    by topping up to 5, and cleans up only the validators it created after the test completes.

    Yields a callable ``_setup(mock_response)`` that the test invokes to
    trigger validator creation in either mock or non-mock mode.
    """
    created_validator_addresses = []

    def _setup(mock_response: Any = None) -> None:
        """
        Create validators appropriate for the current test mode.

        In mock mode (TEST_WITH_MOCK_LLMS=true) always creates 5 validators
        with the configured mock provider and the supplied mock_response.
        In non-mock mode creates only the validators needed to reach a total
        of 5, reusing any that already exist.
        """
        nonlocal created_validator_addresses
        if mock_llms():
            mock_cfg = get_mock_provider_config()
            # Mock mode: create validators with specific mock_response for this test
            for _ in range(5):
                result = post_request_localhost(
                    payload(
                        "sim_createValidator",
                        8,
                        mock_cfg["provider"],
                        mock_cfg["model"],
                        {"temperature": 0.75, "max_tokens": 500},
                        "openai-compatible",
                        {
                            "api_key_env_var": mock_cfg["api_key_env_var"],
                            "api_url": mock_cfg["api_url"],
                            "mock_response": mock_response if mock_response is not None else {},
                        },
                    )
                ).json()
                assert has_success_status(result)
                created_validator_addresses.append(result["result"]["address"])
        else:
            cfg = get_provider_config()
            # Non-mock mode: only create the validators that are still missing
            validators_result = post_request_localhost(
                payload("sim_getAllValidators")
            ).json()
            assert has_success_status(validators_result)
            existing_validators = validators_result.get("result", [])
            validators_to_create = 5 - len(existing_validators)
            if validators_to_create > 0:
                result = post_request_localhost(
                    payload(
                        "sim_createRandomValidators",
                        validators_to_create,
                        8,
                        12,
                        [cfg["provider"]],
                        [cfg["model"]],
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


def mock_llms() -> bool:
    """Return True when mock LLM mode is enabled via TEST_WITH_MOCK_LLMS=true."""
    env_var = os.getenv("TEST_WITH_MOCK_LLMS", "false")  # default no mocking
    return env_var == "true"


def pytest_configure(config: Any) -> None:
    """Load .env file at session start so environment variables are available to all tests."""
    load_dotenv(override=False)
