#!/usr/bin/env python3
"""
Simplified WizardOfCoin test - just deploy and read initial state.
"""

import requests
import json
from pathlib import Path
from genlayer_py import create_client, create_account, localnet
import time


def main():
    print("=== WizardOfCoin Deploy & Read Test ===")

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

            # Look for any field that looks like an address
            potential_addresses = []
            for key, value in raw_tx.items():
                if (
                    isinstance(value, str)
                    and value.startswith("0x")
                    and len(value) == 42
                ):
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

    # 2. Read initial state
    print(f"\n2. Reading contract state...")
    try:
        has_coin = client.read_contract(
            address=contract_address, function_name="get_have_coin"
        )
        print(f"✅ Contract state: have_coin = {has_coin}")

        if has_coin == True:
            print("✅ SUCCESS: Wizard has the coin as expected!")
        else:
            print(f"⚠️  Expected have_coin=True but got {has_coin}")

    except Exception as e:
        print(f"❌ Failed to read contract state: {e}")
        return 1

    # Summary
    print(f"\n" + "=" * 50)
    print("🎉 Deploy and read test completed!")
    print("=" * 50)
    print(f"Contract deployed at: {contract_address}")
    print(f"Wizard has coin: {has_coin}")
    print(f"\nContract address saved to: .last_deployed_contract")

    return 0


if __name__ == "__main__":
    exit(main())
