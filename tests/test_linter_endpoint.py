#!/usr/bin/env python3
"""
Test script for the sim_lintContract JSON-RPC endpoint
Run this after starting the backend to verify the linter integration works
"""

import requests
import json

# Test contract with various issues
TEST_CONTRACT_WITH_ISSUES = """# Missing magic comment

from genlayer import *

class TestContract(gl.Contract):
    balance: int  # Should be u256

    # Missing __init__ method

    @gl.public.view
    def get_balance(self) -> u256:  # Should return int
        return self.balance
"""

# Valid contract
VALID_CONTRACT = """# { "Depends": "py-genlayer:test" }

from genlayer import *

class TestContract(gl.Contract):
    balance: u256

    def __init__(self):
        self.balance = 0

    @gl.public.view
    def get_balance(self) -> int:
        return self.balance
"""


def test_linter_endpoint(url="http://localhost:4000/api"):
    """Test the linter endpoint with various contracts"""

    print("Testing GenVM Linter Endpoint...")
    print("-" * 50)

    # Test 1: Contract with issues
    print("\n1. Testing contract with issues:")
    response = requests.post(
        url,
        json={
            "jsonrpc": "2.0",
            "method": "sim_lintContract",
            "params": {
                "source_code": TEST_CONTRACT_WITH_ISSUES,
                "filename": "test_with_issues.py",
            },
            "id": 1,
        },
        timeout=10,
    )

    response.raise_for_status()
    payload = response.json()
    assert "result" in payload, f"Unexpected payload: {payload}"

    contract_result = payload["result"]
    total_issues = contract_result["summary"]["total"]
    assert total_issues > 0, "Expected the linter to report issues for the bad contract"

    print(f"✅ Found {total_issues} issues:")
    for issue in contract_result["results"]:
        print(
            f"   - Line {issue['line']}: {issue['severity'].upper()} - {issue['message']}"
        )
        if issue["suggestion"]:
            print(f"     💡 {issue['suggestion']}")

    # Test 2: Valid contract
    print("\n2. Testing valid contract:")
    response = requests.post(
        url,
        json={
            "jsonrpc": "2.0",
            "method": "sim_lintContract",
            "params": {"source_code": VALID_CONTRACT, "filename": "valid_contract.py"},
            "id": 2,
        },
        timeout=10,
    )

    response.raise_for_status()
    payload = response.json()
    assert "result" in payload, f"Unexpected payload: {payload}"

    total_issues = payload["result"]["summary"]["total"]
    assert total_issues == 0, f"Expected no issues for valid contract, got {total_issues}"
    print("✅ No issues found - contract is valid!")

    print("\n" + "-" * 50)
    print("Test complete!")


if __name__ == "__main__":
    import sys

    # Allow custom URL as argument
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:4000/api"

    try:
        test_linter_endpoint(url)
    except requests.exceptions.ConnectionError:
        print(f"❌ Could not connect to {url}")
        print("Make sure the backend is running (docker compose up jsonrpc)")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
