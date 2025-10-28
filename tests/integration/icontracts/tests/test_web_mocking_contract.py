"""
Test file for validating web mocking with contracts that make web requests.
This test file specifically tests the ErrorWebContract which makes web requests.
"""

import pytest
import json
from gltest import get_contract_factory
from gltest.contracts.contract import Contract
from gltest.exceptions import DeploymentError
from tests.integration.fixtures.conftest import mock_web_requests, mock_llms

pytestmark = pytest.mark.web_mocking


def _deployment_error_to_tx_receipt(e: DeploymentError):
    """Helper function to extract transaction receipt from deployment error"""
    return e.transaction_result


def test_error_web_with_mocking(setup_validators):
    """Test ErrorWebContract with mock web responses"""
    if not mock_web_requests():
        pytest.skip("Mocking not enabled - TEST_WITH_MOCKS=false")

    # Setup mock responses for the specific URLs used in ErrorWebContract
    mock_web_responses = {
        "render": {
            "httpbin.org/status/404": {"text": "MOCK: Not Found - 404 Error"},
            "example.com": {
                "text": "MOCK: Example Domain - This domain is for use in illustrative examples."
            },
            "this-domain-definitely-does-not-exist": {
                "text": "MOCK: Simulated error for non-existent domain"
            },
        },
        "request": {
            "httpbin.org/status/404": {
                "status": 404,
                "body": "Not Found",
                "headers": {"content-type": "text/plain"},
            }
        },
    }

    # Mock LLM responses for the contract
    mock_llm_response = {
        "response": {
            "404": json.dumps({"error": "not found", "status": 404}),
            "example": json.dumps({"content": "example domain"}),
        }
    }

    setup_validators(
        mock_response=mock_llm_response, mock_web_responses=mock_web_responses
    )

    # Test 1: Deploy with a 404 URL (should succeed with mock)
    factory = get_contract_factory("ErrorWebContract")
    contract = factory.deploy(args=[2, "https://httpbin.org/status/404"])
    assert isinstance(contract, Contract)

    # Test 2: Deploy with example.com (should succeed with mock)
    contract2 = factory.deploy(args=[2, "https://example.com"])
    assert isinstance(contract2, Contract)

    # Test 3: Deploy with non-existent domain (should succeed with mock)
    contract3 = factory.deploy(
        args=[2, "https://this-domain-definitely-does-not-exist-12345.com"]
    )
    assert isinstance(contract3, Contract)


def test_web_mocking_comparison():
    """Test to verify behavior difference between mocked and non-mocked modes"""

    # This test documents the expected behavior:
    # - When TEST_WITH_MOCKS=true: Both LLMs and web requests return mock responses
    # - When TEST_WITH_MOCKS=false: Both LLMs and web requests hit real services

    is_mocking = mock_web_requests()
    is_mocking_llms = mock_llms()

    print(f"\nCurrent mocking configuration:")
    print(f"  - Mocking (LLMs & Web): {'ENABLED' if is_mocking else 'DISABLED'}")

    if is_mocking:
        print("  - Expected: Both LLMs and web requests will return mock responses")
    else:
        print("  - Expected: Both LLMs and web requests will hit real services")

    # This test always passes, it's informational
    assert True
