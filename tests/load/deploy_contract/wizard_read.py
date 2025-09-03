#!/usr/bin/env python3
"""
Read state from a deployed WizardOfCoin contract.
"""

import sys
from genlayer_py import create_client, create_account, localnet


def main():
    # Accept contract address file as first argument, BASE_URL as second
    address_file = sys.argv[1] if len(sys.argv) > 1 else ".last_deployed_contract"
    base_url = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:4000/api"

    print(f"=== Reading WizardOfCoin Contract State ===")
    print(f"Address file: {address_file}")
    print(f"Base URL: {base_url}")

    # Read contract address
    try:
        with open(address_file, "r") as f:
            contract_address = f.read().strip()
        print(f"Contract address: {contract_address}")
    except FileNotFoundError:
        print(f"❌ Contract address file not found: {address_file}")
        return 1

    # Setup
    client = create_client(chain=localnet, endpoint=base_url)
    account = create_account()
    client.local_account = account

    # Read contract state
    print("Reading have_coin state...")
    try:
        has_coin = client.read_contract(
            address=contract_address, function_name="get_have_coin"
        )
        print(f"✅ have_coin = {has_coin}")

        if has_coin:
            print("🪙 The wizard has the coin!")
        else:
            print("✋ The wizard gave away the coin!")

        return 0

    except Exception as e:
        print(f"❌ Failed to read contract: {e}")
        return 1


if __name__ == "__main__":
    exit(main())
