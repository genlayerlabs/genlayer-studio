import pytest
import os
import time
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


def _wait_for_validator_count(min_count: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    last_count = 0
    while time.monotonic() < deadline:
        validators_result = post_request_localhost(
            payload("sim_getAllValidators")
        ).json()
        assert has_success_status(validators_result)
        last_count = len(validators_result.get("result", []))
        if last_count >= min_count:
            # Validator changes are delivered to workers via reload events.
            # Give the consensus worker a short window to consume the update
            # before the test submits a transaction.
            time.sleep(2)
            return
        time.sleep(0.5)
    raise AssertionError(
        f"Expected at least {min_count} validators, found {last_count} after {timeout}s"
    )


@pytest.fixture
def setup_validators():
    created_validator_addresses = []

    def _setup(mock_response: Any = None) -> None:
        nonlocal created_validator_addresses
        if mock_llms():
            # Wipe ALL existing validators before seeding this test's mocks.
            # Without this, a prior test's validator (which has different
            # mock_response, possibly even none) can be picked into this
            # test's consensus round via VRF, disagree with the leader's
            # mocked output, and produce an UNDETERMINED result — the
            # leader's receipt still says SUCCESS so tx_execution_succeeded
            # passes, but contract state stays unchanged and the balance
            # assertion fails. The xdist_group marker serializes these
            # tests onto a single worker, so this wipe is safe for the
            # parallel CI run.
            delete_all = post_request_localhost(
                payload("sim_deleteAllValidators")
            ).json()
            assert has_success_status(delete_all)

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
                            "mock_response": (
                                mock_response if mock_response is not None else {}
                            ),
                        },
                    )
                ).json()
                assert has_success_status(result)
                created_validator_addresses.append(result["result"]["address"])
            _wait_for_validator_count(5)
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
            _wait_for_validator_count(5)

    yield _setup

    # Only delete validators that THIS test created (not all validators)
    for address in created_validator_addresses:
        delete_result = post_request_localhost(
            payload("sim_deleteValidator", address)
        ).json()
        # Don't assert - validator might already be deleted by test logic
        has_success_status(delete_result)


def mock_llms() -> bool:
    env_var = os.getenv("TEST_WITH_MOCK_LLMS", "false")  # default no mocking
    return env_var.lower() == "true"


def pytest_configure(config: Any) -> None:
    load_dotenv(override=True)


def pytest_collection_modifyitems(config: Any, items: list) -> None:
    """Force tests using `setup_validators` onto a single xdist worker.

    Validators are global across the whole studio process — there's no
    per-test pool. With pytest-xdist `-n 8`, two parallel tests can each
    create 5 validators with different mock_response payloads, and a
    third test's consensus will randomly pick from the merged pool.
    Validators with the wrong mock return wrong outputs, validators
    disagree, the tx errors, the test fails non-deterministically.

    Grouping all tests that exercise validators onto one xdist worker
    serializes them and removes the cross-test interference.

    Requires `--dist loadgroup` to take effect (see CI test command).
    """
    import pytest as _pytest

    for item in items:
        if "setup_validators" in getattr(item, "fixturenames", ()):
            item.add_marker(_pytest.mark.xdist_group(name="mock_validators"))
