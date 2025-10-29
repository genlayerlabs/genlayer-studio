"""
Configuration for fixtures used in integration tests.

This conftest.py provides helper functions but no longer defines the setup_validators
fixture, which has been moved to tests/integration/conftest.py to properly override
the gltest fixture for all integration tests because gltest does not have mocking for web requests implemented yet.
"""

import sys
import os
import json
from pathlib import Path

# Add schemas to path
schemas_path = Path(__file__).parent / "schemas"
if str(schemas_path) not in sys.path:
    sys.path.insert(0, str(schemas_path))


def mock_llms():
    """Check if LLM mocking is enabled"""
    env_var = os.getenv("TEST_WITH_MOCKS", "false")  # default no mocking
    if env_var == "true":
        return True
    return False


def mock_web_requests():
    """Check if web request mocking is enabled"""
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
        "example_responses.json",
    )

    try:
        with open(mock_file, "r") as f:
            return json.loads(f.read())
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Warning: Could not load mock web responses: {e}")
        return {}
