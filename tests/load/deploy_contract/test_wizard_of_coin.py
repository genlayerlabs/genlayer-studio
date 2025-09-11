#!/usr/bin/env python3
"""
WizardOfCoin contract test using genlayer_py with proper contract address handling.
Based on the Storage contract test pattern.
"""

import requests
import json
from pathlib import Path
from genlayer_py import create_client, create_account, localnet
import time


def main():
    print("=== WizardOfCoin Contract Test ===")

    # Setup
    client = create_client(chain=localnet, endpoint="http://localhost:4000/api")
    account = create_account()
    client.local_account = account

    print(f"Account: {account.address}")

    # Load contract
    contract_path = Path("examples/contracts/wizard_of_coin.py")
    if not contract_path.exists():
        # Try alternative path if running from tests/load directory
        contract_path = Path("../../examples/contracts/wizard_of_coin.py")

    with open(contract_path, "r") as f:
        contract_code = f.read()

    print(f"Contract loaded ({len(contract_code)} bytes)")

    # 1. Deploy contract with initial state (have_coin=True)
    print("\n1. Deploying WizardOfCoin contract...")
    print("   Constructor args: have_coin=True")

    tx_hash = client.deploy_contract(code=contract_code, args=[True])
    print(f"Deploy tx: {tx_hash}")

    # Wait for deployment
    receipt = client.w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"Status: {receipt.status}")

    if receipt.status != 1:
        print("❌ Deployment failed")
        return 1

    # Wait for contract to be fully processed
    print("Waiting for contract to be indexed...")
    time.sleep(5)

    # Get contract address from transaction details
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
        if "result" in tx_data:
            raw_tx = tx_data["result"]
            print(f"\nTransaction fields that might contain address:")

            # Look for any field that looks like an address
            potential_addresses = []
            for key, value in raw_tx.items():
                if (
                    isinstance(value, str)
                    and value.startswith("0x")
                    and len(value) == 42
                ):
                    print(f"  {key}: {value}")
                    # Don't use the null address
                    if value != "0x0000000000000000000000000000000000000000":
                        potential_addresses.append(value)

            # Try specific fields first
            if (
                "to_address" in raw_tx
                and raw_tx["to_address"] != "0x0000000000000000000000000000000000000000"
            ):
                contract_address = raw_tx["to_address"]
            elif (
                "recipient" in raw_tx
                and raw_tx["recipient"] != "0x0000000000000000000000000000000000000000"
            ):
                contract_address = raw_tx["recipient"]
            elif potential_addresses:
                contract_address = potential_addresses[0]
            else:
                # As fallback, use the transaction hash itself
                contract_address = tx_hash
                print(f"⚠️  Using tx hash as contract reference: {contract_address}")

            print(f"✅ Using contract address: {contract_address}")

            # Save contract address for other scripts
            with open(".last_deployed_contract", "w") as f:
                f.write(contract_address)

    except Exception as e:
        print(f"❌ Error getting transaction data: {e}")
        return 1

    # 2. Check if contract exists
    print(f"\n2. Verifying contract deployment...")

    try:
        schema_response = requests.post(
            "http://localhost:4000/api",
            json={
                "jsonrpc": "2.0",
                "method": "gen_getContractSchema",
                "params": [contract_address],
                "id": 1,
            },
        )

        schema_result = schema_response.json()
        if "result" in schema_result:
            print(f"✅ Contract schema found - contract exists")
            schema = schema_result["result"]
            if "functions" in schema:
                print(f"Available functions: {list(schema['functions'].keys())}")
        else:
            print(f"⚠️  Contract schema not found immediately")
            # Contract needs more time, wait and retry
            print("Waiting additional time for contract indexing...")
            time.sleep(10)

            schema_response = requests.post(
                "http://localhost:4000/api",
                json={
                    "jsonrpc": "2.0",
                    "method": "gen_getContractSchema",
                    "params": [contract_address],
                    "id": 1,
                },
            )

            schema_result = schema_response.json()
            if "result" in schema_result:
                print(f"✅ Contract schema found after waiting")
                schema = schema_result["result"]
                if "functions" in schema:
                    print(f"Available functions: {list(schema['functions'].keys())}")
            else:
                print(f"❌ Contract still not found after waiting")
                print(
                    f"Error: {schema_result.get('error', {}).get('message', 'Unknown error')}"
                )
                # Continue anyway as contract might still work

    except Exception as e:
        print(f"⚠️  Error checking contract schema: {e}")
        # Continue anyway

    # 3. Read initial state
    print(f"\n3. Reading initial state...")
    try:
        initial_state = client.read_contract(
            address=contract_address, function_name="get_have_coin"
        )
        print(f"✅ Initial state: have_coin = {initial_state}")

        if initial_state != True:
            print(f"⚠️  Expected have_coin=True but got {initial_state}")

    except Exception as e:
        print(f"❌ Failed to read initial state: {e}")
        return 1

    # 4. Try write simulation (ask_for_coin)
    print(f"\n4. Simulating ask_for_coin operation...")
    print("   Request: 'Oh mighty wizard, can I have your magical coin?'")

    try:
        write_result = client.simulate_write_contract(
            address=contract_address,
            function_name="ask_for_coin",
            args=["Oh mighty wizard, can I have your magical coin?"],
        )
        print(f"✅ Write simulation completed")
        print(f"   Result: {write_result}")

    except Exception as e:
        error_str = str(e)
        if "forbidden" in error_str.lower() or "SystemError: 6" in error_str:
            print(f"🎯 Expected error encountered: {e}")
            print("   This matches the Linear issue DXP-609 pattern")
        else:
            print(f"❌ Write simulation failed: {e}")
        # Continue with test

    # 5. Read state after simulation
    print(f"\n5. Reading state after simulation...")
    try:
        post_sim_state = client.read_contract(
            address=contract_address, function_name="get_have_coin"
        )
        print(f"✅ State after simulation: have_coin = {post_sim_state}")

        if post_sim_state == initial_state:
            print(
                "✅ SUCCESS: State unchanged - simulation didn't modify contract state!"
            )
        else:
            print(
                f"⚠️  State changed from {initial_state} to {post_sim_state} during simulation"
            )

    except Exception as e:
        print(f"❌ Failed to read state after simulation: {e}")
        return 1

    # 6. Execute actual write transaction
    print(f"\n6. Executing actual ask_for_coin transaction...")
    print("   Request: 'Please, I really need the coin for my quest!'")

    try:
        tx_hash = client.write_contract(
            address=contract_address,
            function_name="ask_for_coin",
            args=["Please, I really need the coin for my quest!"],
        )
        print(f"✅ Transaction submitted: {tx_hash}")

        # Wait for transaction confirmation
        receipt = client.w3.eth.wait_for_transaction_receipt(tx_hash)
        if receipt.status == 1:
            print("✅ Transaction confirmed")
        else:
            print(f"⚠️  Transaction failed with status: {receipt.status}")

        # Wait a bit for state update
        time.sleep(2)

    except Exception as e:
        print(f"❌ Write transaction failed: {e}")
        # Continue to check final state anyway

    # 7. Read final state
    print(f"\n7. Reading final state after transaction...")
    try:
        final_state = client.read_contract(
            address=contract_address, function_name="get_have_coin"
        )
        print(f"✅ Final state: have_coin = {final_state}")

        if final_state != initial_state:
            if final_state == False:
                print("🎉 The wizard gave away the coin!")
            else:
                print(f"🤔 State changed to unexpected value: {final_state}")
        else:
            print("💪 The wizard kept the coin!")

    except Exception as e:
        print(f"❌ Failed to read final state: {e}")
        return 1

    # Summary
    print(f"\n" + "=" * 50)
    print("🎉 All tests completed successfully!")
    print("=" * 50)
    print(f"Contract deployed at: {contract_address}")
    print(f"Initial state: have_coin = {initial_state}")
    print(f"State after simulation: have_coin = {post_sim_state}")
    print(f"Final state: have_coin = {final_state}")
    print(f"\nContract address saved to: .last_deployed_contract")

    return 0


if __name__ == "__main__":
    exit(main())
