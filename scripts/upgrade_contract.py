#!/usr/bin/env python3
"""
Upgrade a deployed contract's code.

Usage:
    # Local studio (no auth needed)
    ./scripts/upgrade_contract.py 0x00c6125b5535a9e33e8E0662eb0336e06aaA19C4 path/to/contract.py

    # Hosted studio with deployer signature (upgrade your own contracts)
    ./scripts/upgrade_contract.py 0x123... contract.py --rpc-url https://studio.example.com/api --private-key 0xabc...

    # Hosted studio with admin key (upgrade any contract)
    ./scripts/upgrade_contract.py 0x123... contract.py --rpc-url https://studio.example.com/api --admin-key SECRET

    # With custom timeout
    ./scripts/upgrade_contract.py 0x123... contract.py --timeout 120
"""

import argparse
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("Error: requests library required. Install with: pip install requests")
    sys.exit(1)


def sign_upgrade_message(
    contract_address: str, new_code: str, private_key: str, nonce: int
) -> str:
    """Sign the upgrade message with the deployer's private key."""
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
        from web3 import Web3
    except ImportError:
        print(
            "Error: eth_account and web3 required for signing. Install with: pip install eth-account web3"
        )
        sys.exit(1)

    # Message: keccak256(address + nonce_bytes32 + keccak256(code))
    # Including nonce prevents replay attacks
    nonce_bytes = nonce.to_bytes(32, byteorder="big")
    code_hash = Web3.keccak(text=new_code)
    message_hash = Web3.keccak(
        Web3.to_bytes(hexstr=contract_address) + nonce_bytes + code_hash
    )
    message = encode_defunct(primitive=message_hash)

    # Sign with private key
    signature = Account.sign_message(message, private_key)
    return "0x" + signature.signature.hex()


def rpc_call(url: str, method: str, params: list) -> dict:
    """Make a JSON-RPC call."""
    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "id": 1,
    }

    response = requests.post(url, json=payload, timeout=30)
    result = response.json()

    if "error" in result:
        raise Exception(f"RPC error: {result['error']}")

    return result.get("result")


def wait_for_tx(url: str, tx_hash: str, timeout: int = 60) -> dict:
    """Wait for transaction to be finalized."""
    start = time.time()
    while time.time() - start < timeout:
        tx = rpc_call(url, "eth_getTransactionByHash", [tx_hash])
        if tx:
            status = tx.get("status")
            print(f"  Status: {status}")
            if status in ["FINALIZED", "ACCEPTED", "CANCELED"]:
                return tx
        time.sleep(2)
    raise TimeoutError(f"Transaction {tx_hash} not finalized within {timeout}s")


def main():
    parser = argparse.ArgumentParser(
        description="Upgrade a deployed contract's code",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("contract_address", help="Address of the deployed contract")
    parser.add_argument("code_file", help="Path to the new contract code file")
    parser.add_argument(
        "--rpc-url",
        default="http://localhost:4000/api",
        help="RPC endpoint URL (default: http://localhost:4000/api)",
    )
    parser.add_argument(
        "--private-key", help="Private key of deployer (for signing in hosted mode)"
    )
    parser.add_argument(
        "--admin-key", help="Admin API key (for upgrading any contract in hosted mode)"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Timeout in seconds to wait for upgrade (default: 60)",
    )

    args = parser.parse_args()

    # Read contract code
    code_path = Path(args.code_file)
    if not code_path.exists():
        print(f"Error: File not found: {code_path}")
        sys.exit(1)

    new_code = code_path.read_text()
    print(f"Contract address: {args.contract_address}")
    print(f"Code file: {code_path} ({len(new_code)} bytes)")
    print(f"RPC URL: {args.rpc_url}")
    if args.private_key:
        print("Auth: Using deployer signature")
    elif args.admin_key:
        print("Auth: Using admin key")
    else:
        print("Auth: None (local mode)")
    print()

    # Verify contract exists
    print("1. Verifying contract exists...")
    try:
        schema = rpc_call(
            args.rpc_url, "gen_getContractSchema", [args.contract_address]
        )
        if schema:
            print(f"   Contract found with {len(schema.get('methods', []))} methods")
    except Exception as e:
        print(f"   Warning: Could not get schema: {e}")

    # Prepare signature if private key provided
    signature = None
    if args.private_key:
        print("\n2. Signing upgrade request...")
        try:
            # Fetch nonce for replay protection
            nonce = rpc_call(
                args.rpc_url, "gen_getContractNonce", [args.contract_address]
            )
            print(f"   Contract nonce: {nonce}")
            signature = sign_upgrade_message(
                args.contract_address, new_code, args.private_key, nonce
            )
            print(f"   Signature: {signature[:20]}...")
        except Exception as e:
            print(f"   Error signing: {e}")
            sys.exit(1)

    # Build params: [contract_address, new_code, signature, admin_key]
    params = [args.contract_address, new_code, signature, args.admin_key]

    # Submit upgrade
    print("\n3. Submitting upgrade transaction...")
    try:
        result = rpc_call(args.rpc_url, "sim_upgradeContractCode", params)
        tx_hash = result["transaction_hash"]
        print(f"   Transaction hash: {tx_hash}")
    except Exception as e:
        print(f"   Error: {e}")
        sys.exit(1)

    # Wait for completion
    print(f"\n4. Waiting for upgrade to complete (timeout: {args.timeout}s)...")
    try:
        tx = wait_for_tx(args.rpc_url, tx_hash, timeout=args.timeout)

        if tx["status"] == "FINALIZED":
            print("\n✓ Upgrade successful!")
            print(f"  Transaction: {tx_hash}")
        elif tx["status"] == "CANCELED":
            print("\n✗ Upgrade failed!")
            print(f"  Transaction: {tx_hash}")
            if tx.get("consensus_data", {}).get("error"):
                print(f"  Error: {tx['consensus_data']['error']}")
            sys.exit(1)
        else:
            print(f"\n? Upgrade status: {tx['status']}")

    except TimeoutError as e:
        print(f"\n✗ {e}")
        print(f"  Check transaction status manually: {tx_hash}")
        sys.exit(1)


if __name__ == "__main__":
    main()
