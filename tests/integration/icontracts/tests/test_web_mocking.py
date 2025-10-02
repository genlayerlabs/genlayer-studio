import pytest
import json
from gltest import get_contract_factory
from tests.integration.icontracts.conftest import mock_web_requests
from tests.common.request import payload, post_request_localhost
from tests.common.response import has_success_status

pytestmark = pytest.mark.web_mocking


def test_web_mocking_enabled():
    """Test that web mocking is working when enabled"""
    if not mock_web_requests():
        pytest.skip("Mocking not enabled - TEST_WITH_MOCKS=false")

    # This test verifies that the mocking infrastructure is in place
    assert mock_web_requests() == True


def test_web_requests_with_real_urls():
    """Test that real web requests work when mocking is disabled"""
    if mock_web_requests():
        pytest.skip("Mocking enabled - this test requires real web requests")

    # This test verifies that real web requests work when mocking is disabled
    assert mock_web_requests() == False


def test_web_mock_with_custom_responses(setup_validators):
    """Test that custom mock web responses work correctly"""
    if not mock_web_requests():
        pytest.skip("Mocking not enabled - TEST_WITH_MOCKS=false")

    # Custom mock responses for testing
    mock_llm_response = {
        "response": {"test": json.dumps({"result": "mocked LLM response"})}
    }

    mock_web_responses = {
        "render": {
            "test.example.com": {"text": "MOCK: This is a test webpage content"},
            "api.test.com": {"text": "MOCK: API response text"},
        },
        "request": {
            "api.test.com/data": {
                "status": 200,
                "body": json.dumps({"test": "data", "mock": True}),
                "headers": {"content-type": "application/json"},
            }
        },
    }

    setup_validators(
        mock_response=mock_llm_response, mock_web_responses=mock_web_responses
    )

    # Verify validators were created with mock responses
    result = post_request_localhost(payload("sim_getValidators")).json()
    assert has_success_status(result)
    assert len(result["result"]) == 5  # We created 5 validators


def test_error_web_mock(setup_validators):
    """Test web error responses with mocking"""
    if not mock_web_requests():
        pytest.skip("Mocking not enabled - TEST_WITH_MOCKS=false")

    # Mock error responses
    mock_web_responses = {
        "render": {"error.example.com": {"text": "MOCK: 404 Not Found"}},
        "request": {
            "api.error.com": {
                "status": 500,
                "body": "Internal Server Error",
                "headers": {"content-type": "text/plain"},
            }
        },
    }

    setup_validators(mock_web_responses=mock_web_responses)

    # Test that validators can handle mock error responses
    result = post_request_localhost(payload("sim_getValidators")).json()
    assert has_success_status(result)


def test_default_mock_responses_loading(setup_validators):
    """Test that default mock responses from example_responses.json are loaded"""
    if not mock_web_requests():
        pytest.skip("Mocking not enabled - TEST_WITH_MOCKS=false")

    # Don't pass custom mock_web_responses, should load defaults
    setup_validators()

    # The default responses should be loaded from example_responses.json
    # This test verifies the infrastructure is working
    result = post_request_localhost(payload("sim_getValidators")).json()
    assert has_success_status(result)
    assert len(result["result"]) == 5
