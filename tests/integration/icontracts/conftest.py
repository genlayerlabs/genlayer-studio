import pytest
import os
import json
from dotenv import load_dotenv

from tests.common.request import payload, post_request_localhost
from tests.common.response import has_success_status


@pytest.fixture
def setup_validators():
    def _setup(mock_response=None, mock_web_responses=None):
        # Load default mock web responses if mocking is enabled (uses same var as LLM mocking)
        if mock_web_responses is None and mock_llms():
            mock_web_responses = load_mock_web_responses()
        
        if mock_llms():
            for _ in range(5):
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


def mock_web_requests():
    # Using the same variable as LLM mocking for consistency
    return mock_llms()


def load_mock_web_responses():
    """Load mock web responses from JSON file"""
    if not mock_llms():
        return None
    
    mock_file = os.path.join(
        os.path.dirname(__file__), 
        "..", 
        "fixtures", 
        "mock_responses", 
        "web", 
        "example_responses.json"
    )
    
    try:
        with open(mock_file, 'r') as f:
            return json.loads(f.read())
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Warning: Could not load mock web responses: {e}")
        return {}


def pytest_configure(config):
    load_dotenv(override=True)
