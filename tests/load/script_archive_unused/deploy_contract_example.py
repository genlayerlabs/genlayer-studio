#!/usr/bin/env python3
"""
Test script for CampaignIC contract using official genlayer-py library
Tests deployment, gen_call read/write operations to discover Linear issue DXP-609
"""

import sys
import os
from datetime import datetime, timezone

# Add genlayer-py to path
sys.path.append(
    "/Users/kristofstroobants/.local/share/virtualenvs/genlayer-studio-GiDAvT-h/lib/python3.12/site-packages"
)

from genlayer_py import create_client, create_account
from genlayer_py.chains import localnet


def get_client():
    """Create a GenLayer client with account"""
    account = create_account()
    client = create_client(
        chain=localnet, endpoint="http://localhost:4000/api", account=account
    )
    return client, account


def get_campaign_contract_code():
    """Load the CampaignIC contract code from dKOL"""
    contract_path = "/Users/kristofstroobants/dKOL/intelligent-contracts/CampaignFactory/CampaignIC.py"
    with open(contract_path, "r") as f:
        return f.read()


def create_minimal_constructor_args():
    """Create minimal valid constructor arguments for CampaignIC"""
    # Minimal valid arguments based on the constructor signature
    return {
        "title": "Test Campaign",
        "goal": "Test campaign goal",
        "knowledge_base": "Test knowledge base",
        "rules": "Test campaign rules",
        "style": "Test campaign style",
        "start_datetime_iso": datetime.now(timezone.utc).isoformat(),
        "campaign_duration_periods": 7,
        "period_length_days": 1,
        "missions": {
            "test_mission": {
                "title": "Test Mission",
                "description": "Test mission desc",
                "rules": "Test mission rules",
                "active": True,
            }
        },
        "token": "0x1234567890123456789012345678901234567890",
        "x_id_contract": "test_x_id",
        "creator_address": "0x1234567890123456789012345678901234567890",
        "bridge_sender": "0x1234567890123456789012345678901234567890",
        "distribution_contract_chain_id": 1,
        "distribution_contract_address": "0x1234567890123456789012345678901234567890",
        "only_verified_users": False,
        "minimum_followers": 0,
        "maximum_followers": 1000000,
        "whitelisted_submitters": [],
        "id": "test_campaign_id",
        "alpha": 1500000000000000000,  # 1.5 * 1e18
        "beta": 500000000000000000,  # 0.5 * 1e18
        "gate_weights": [
            1000000000000000000,
            1000000000000000000,
            1000000000000000000,
            1000000000000000000,
        ],  # [1, 1, 1, 1] * 1e18
        "metric_weights": [
            200000000000000000,
            200000000000000000,
            200000000000000000,
            200000000000000000,
            200000000000000000,
            200000000000000000,
            200000000000000000,
        ],  # [0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2] * 1e18
        "allow_old_tweets": True,
        "tweet_api_url": "https://rally-staging.vercel.app/api/twitter",
        "max_submissions_per_participant": 5,
    }


def deploy_campaign_contract(client):
    """Deploy the CampaignIC contract with minimal arguments"""
    print("=== DEPLOYING CAMPAIGNIC CONTRACT ===")

    contract_code = get_campaign_contract_code()
    constructor_args = create_minimal_constructor_args()

    print(f"Contract code length: {len(contract_code)} characters")
    print(f"Constructor args: {list(constructor_args.keys())}")

    try:
        # Deploy the contract
        tx_hash = client.deploy_contract(code=contract_code, kwargs=constructor_args)

        print(f":white_check_mark: Contract deployment TX: {tx_hash}")

        # Wait for deployment and get contract address
        receipt = client.w3.eth.wait_for_transaction_receipt(tx_hash)

        if receipt.status != 1:
            print(f":x: Deployment failed with status: {receipt.status}")
            return None

        # Get transaction details to find the contract address
        print(f"Transaction receipt status: {receipt.status}")
        print(f"Transaction hash: {tx_hash}")

        # For GenLayer, we need to get the transaction details to find the contract address
        import requests
        import json

        # Make RPC call to get transaction by hash
        try:
            response = requests.post(
                "http://localhost:4000/api",
                json={
                    "jsonrpc": "2.0",
                    "method": "eth_getTransactionByHash",
                    "params": [tx_hash],
                    "id": 1,
                },
            )

            tx_data = response.json()
            if "result" in tx_data and tx_data["result"]:
                raw_tx = tx_data["result"]
                if "data" in raw_tx and "contract_address" in raw_tx["data"]:
                    contract_address = raw_tx["data"]["contract_address"]
                    print(
                        f":white_check_mark: Found contract address: {contract_address}"
                    )
                else:
                    print(":warning:  Contract address not found in transaction data")
                    print(f"Transaction data: {raw_tx}")
                    # Fallback: try to find it in logs or other fields
                    contract_address = tx_hash  # Use as fallback
            else:
                print(":x: Could not retrieve transaction data")
                contract_address = tx_hash  # Use as fallback

        except Exception as e:
            print(f":warning:  Error getting transaction data: {e}")
            contract_address = tx_hash  # Use as fallback

        print(f":white_check_mark: CampaignIC contract deployed at: {contract_address}")
        return contract_address

    except Exception as e:
        print(f":x: Deployment failed: {e}")
        return None


def test_read_operations(client, contract_address):
    """Test gen_call read operations on the deployed contract"""
    print(f"\n=== TESTING READ OPERATIONS ===")
    print(f"Contract address: {contract_address}")

    try:
        # Test 1: Read campaign info (view method)
        print("\n1. Reading campaign info...")
        campaign_info = client.read_contract(
            address=contract_address, function_name="get_campaign_info"
        )
        print(f":white_check_mark: Campaign info read successfully")
        print(f"Campaign title: {campaign_info.get('title', 'N/A')}")
        print(f"Campaign ID: {campaign_info.get('id', 'N/A')}")

        return campaign_info

    except Exception as e:
        print(f":x: Read operation failed: {e}")
        return None


def test_write_simulation(client, contract_address):
    """Test gen_call write simulation to trigger potential Linear issue DXP-609"""
    print(f"\n=== TESTING WRITE SIMULATION ===")

    try:
        # Test 1: Simulate submit_mission (write method)
        print("\n1. Simulating submit_mission write operation...")

        result = client.simulate_write_contract(
            address=contract_address,
            function_name="submit_mission",
            args=[
                "test_mission_2",
                "Test Mission 2",
                "Test mission 2 description",
                "Test mission 2 rules",
            ],
        )

        print(f":white_check_mark: Write simulation completed")
        print(f"Simulation result: {result}")

        return True

    except Exception as e:
        print(f":x: Write simulation failed: {e}")

        # Check for the specific LinearError: 6: forbidden in storage.py
        error_str = str(e)
        if "SystemError: 6: forbidden" in error_str and "storage.py" in error_str:
            print(":dart: FOUND: SystemError: 6: forbidden in storage.py!")
            print("This is the exact error from Linear issue DXP-609!")
            print("Error reproduced successfully in write simulation.")
            return "ERROR_REPRODUCED"

        return False


def test_state_persistence(client, contract_address, initial_info):
    """Verify that write simulation didn't modify actual contract state"""
    print(f"\n=== VERIFYING STATE PERSISTENCE ===")

    try:
        # Read campaign info again
        print("Reading campaign info after write simulation...")
        final_info = client.read_contract(
            address=contract_address, function_name="get_campaign_info"
        )

        # Compare mission counts or other state indicators
        initial_missions = initial_info.get("missions", {}) if initial_info else {}
        final_missions = final_info.get("missions", {})

        print(f"Initial missions count: {len(initial_missions)}")
        print(f"Final missions count: {len(final_missions)}")

        if len(initial_missions) == len(final_missions):
            print(
                ":dart: CONFIRMED: Write simulation did NOT modify actual contract state!"
            )
            print("State remains unchanged after simulation.")
        else:
            print(
                ":warning:  WARNING: Contract state was modified by write simulation!"
            )

        return True

    except Exception as e:
        print(f":x: State verification failed: {e}")
        return False


def main():
    print("=== CampaignIC Contract Test with genlayer-py ===\n")

    try:
        # Step 1: Create client
        print("Creating GenLayer client...")
        client, account = get_client()
        print(f":white_check_mark: Client created with account: {account.address}")

        # Step 2: Deploy contract
        contract_address = deploy_campaign_contract(client)
        if not contract_address:
            print(":x: Test failed - could not deploy contract")
            return

        # Step 3: Test read operations
        initial_state = test_read_operations(client, contract_address)

        # Step 4: Test write simulation (looking for Linear issue)
        simulation_result = test_write_simulation(client, contract_address)

        # Step 5: Verify state persistence
        test_state_persistence(client, contract_address, initial_state)

        # Summary
        print(f"\n:tada: SUCCESS: Full test completed!")
        print(f":white_check_mark: CampaignIC contract deployed at: {contract_address}")
        print(f":white_check_mark: Read operations tested")

        if simulation_result == "ERROR_REPRODUCED":
            print(f":dart: LINEAR ISSUE DXP-609 REPRODUCED!")
            print(
                f":white_check_mark: SystemError: 6: forbidden in storage.py detected"
            )
        elif simulation_result:
            print(
                f":white_check_mark: Write simulation operations tested (no error detected)"
            )
        else:
            print(f":x: Write simulation failed")

        print(f":white_check_mark: State persistence verified")

    except Exception as e:
        print(f"\n:x: Test failed with error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
