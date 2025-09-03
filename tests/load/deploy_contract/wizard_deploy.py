#!/usr/bin/env python3
"""
Deploy WizardOfCoin contract and save the address.
"""

import requests
import json
import sys
from pathlib import Path
from genlayer_py import create_client, create_account, localnet
import time


def main():
    # Accept optional output file argument
    output_file = sys.argv[1] if len(sys.argv) > 1 else ".last_deployed_contract"

    print(f"=== Deploying WizardOfCoin Contract ===")
    print(f"Output file: {output_file}")

    # Setup
    client = create_client(chain=localnet, endpoint="http://localhost:4000/api")
    account = create_account()
    client.local_account = account

    print(f"Account: {account.address}")

    # Load contract
    contract_path = Path("examples/contracts/wizard_of_coin.py")
    if not contract_path.exists():
        contract_path = Path("../../examples/contracts/wizard_of_coin.py")

    with open(contract_path, "r") as f:
        contract_code = f.read()

    print(f"Contract loaded ({len(contract_code)} bytes)")

    # Deploy contract with initial state (have_coin=True)
    print("Deploying with have_coin=True...")

    tx_hash = client.deploy_contract(code=contract_code, args=[True])
    print(f"Deploy tx: {tx_hash}")

    # Wait for deployment
    receipt = client.w3.eth.wait_for_transaction_receipt(tx_hash)

    if receipt.status != 1:
        print("❌ Deployment failed")
        return 1

    print(f"✅ Deployment successful (status: {receipt.status})")

    # Wait for contract to be indexed
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

            # Look for address fields
            potential_addresses = []
            for key, value in raw_tx.items():
                if (
                    isinstance(value, str)
                    and value.startswith("0x")
                    and len(value) == 42
                ):
                    if value != "0x0000000000000000000000000000000000000000":
                        potential_addresses.append(value)

            # Determine contract address
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
                contract_address = tx_hash

            print(f"✅ Contract address: {contract_address}")

            # Save contract address
            with open(output_file, "w") as f:
                f.write(contract_address)

            print(f"✅ Address saved to: {output_file}")

    except Exception as e:
        print(f"❌ Error getting contract address: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
