#!/usr/bin/env python3
"""
State integrity test for the lost update bug.

Deploys a simple Counter contract, fires N concurrent increment() transactions,
waits for all to finalize, then asserts get_count() == N.

If concurrent workers overwrite each other's state (the lost update bug),
the final count will be less than N.

Requires multiple consensus workers to trigger the race condition:
  CONSENSUS_WORKERS=3 docker compose up -d

Usage:
  python3 test_state_integrity.py [API_URL] [--txs N] [--timeout SECONDS]
"""

import argparse
import concurrent.futures
import sys
import time
from pathlib import Path

import requests
from genlayer_py import create_account, create_client, localnet


def rpc_call(api_url: str, method: str, params: list | None = None) -> dict:
    resp = requests.post(
        api_url,
        json={
            "jsonrpc": "2.0",
            "method": method,
            "params": params or [],
            "id": 1,
        },
        timeout=30,
    )
    return resp.json()


def get_contract_address(api_url: str, tx_hash: str) -> str:
    """Extract contract address from deploy transaction."""
    data = rpc_call(api_url, "eth_getTransactionByHash", [tx_hash])
    if "result" not in data:
        raise RuntimeError(f"Failed to get tx {tx_hash}: {data}")
    raw = data["result"]
    for field in ("to_address", "recipient"):
        addr = raw.get(field)
        if addr and addr != "0x" + "0" * 40:
            return addr
    # Fallback: scan for any address-like field
    for value in raw.values():
        if isinstance(value, str) and value.startswith("0x") and len(value) == 42:
            if value != "0x" + "0" * 40:
                return value
    raise RuntimeError(f"No contract address found in tx {tx_hash}")


def wait_for_tx(client, tx_hash: str, timeout: int = 300) -> bool:
    """Wait for a transaction receipt. Returns True if successful."""
    try:
        receipt = client.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)
        return receipt.status == 1
    except Exception as e:
        print(f"  tx {tx_hash[:16]}... failed: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="State integrity test")
    parser.add_argument("api_url", nargs="?", default="http://localhost:4000/api")
    parser.add_argument(
        "--txs",
        type=int,
        default=20,
        help="Number of increment transactions (default: 20)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Per-tx timeout in seconds (default: 600)",
    )
    parser.add_argument(
        "--batch-delay",
        type=float,
        default=0.1,
        help="Delay between sending txs (default: 0.1s)",
    )
    args = parser.parse_args()

    api_url = args.api_url
    num_txs = args.txs
    tx_timeout = args.timeout

    print("=" * 60)
    print("  STATE INTEGRITY TEST — Lost Update Detection")
    print("=" * 60)
    print(f"API:         {api_url}")
    print(f"Increments:  {num_txs}")
    print(f"TX timeout:  {tx_timeout}s")
    print()

    # --- Check workers ---
    print("Checking consensus worker count...")
    # No direct API for this, but we can note that the test is most useful
    # with CONSENSUS_WORKERS >= 2

    # --- Deploy counter contract ---
    print("Deploying Counter contract...")
    contract_path = Path(__file__).parent / "contracts" / "counter.py"
    if not contract_path.exists():
        print(f"ERROR: Contract not found at {contract_path}")
        return 1

    contract_code = contract_path.read_text()
    print(f"  Contract loaded ({len(contract_code)} bytes)")

    client = create_client(chain=localnet, endpoint=api_url)
    account = create_account()
    client.local_account = account
    print(f"  Account: {account.address}")

    deploy_hash = client.deploy_contract(code=contract_code, args=[])
    print(f"  Deploy tx: {deploy_hash}")

    receipt = client.w3.eth.wait_for_transaction_receipt(
        deploy_hash, timeout=tx_timeout
    )
    if receipt.status != 1:
        print("ERROR: Deployment failed")
        return 1

    time.sleep(3)  # Wait for indexing

    contract_address = get_contract_address(api_url, deploy_hash)
    print(f"  Contract: {contract_address}")

    # --- Verify initial state ---
    initial_count = client.read_contract(
        address=contract_address, function_name="get_count"
    )
    print(f"  Initial count: {initial_count}")
    assert initial_count == 0, f"Expected initial count 0, got {initial_count}"
    print()

    # --- Fire N increment transactions as fast as possible ---
    print(f"Sending {num_txs} increment transactions...")
    tx_hashes = []
    send_start = time.time()

    for i in range(num_txs):
        # Each tx needs its own account to avoid nonce conflicts
        sender = create_account()
        sender_client = create_client(chain=localnet, endpoint=api_url)
        sender_client.local_account = sender

        try:
            tx_hash = sender_client.write_contract(
                address=contract_address,
                function_name="increment",
                args=[],
            )
            tx_hashes.append(tx_hash)
            if (i + 1) % 5 == 0:
                print(f"  Sent {i + 1}/{num_txs}")
        except Exception as e:
            print(f"  ERROR sending tx {i + 1}: {e}")

        if args.batch_delay > 0:
            time.sleep(args.batch_delay)

    send_elapsed = time.time() - send_start
    print(f"  Sent {len(tx_hashes)}/{num_txs} transactions in {send_elapsed:.1f}s")
    print()

    if not tx_hashes:
        print("ERROR: No transactions were sent")
        return 1

    # --- Wait for all transactions to finalize ---
    print(f"Waiting for {len(tx_hashes)} transactions to finalize...")
    wait_start = time.time()

    succeeded = 0
    failed = 0

    # Wait for receipts in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futures = {
            pool.submit(wait_for_tx, client, tx_hash, tx_timeout): tx_hash
            for tx_hash in tx_hashes
        }
        for future in concurrent.futures.as_completed(futures):
            if future.result():
                succeeded += 1
            else:
                failed += 1
            done = succeeded + failed
            if done % 5 == 0:
                print(
                    f"  Finalized {done}/{len(tx_hashes)} (ok={succeeded}, fail={failed})"
                )

    wait_elapsed = time.time() - wait_start
    print(f"  All done in {wait_elapsed:.1f}s — {succeeded} succeeded, {failed} failed")
    print()

    # --- Read final count ---
    time.sleep(2)  # Brief pause for state to settle

    print("Reading final contract state...")
    final_count = client.read_contract(
        address=contract_address, function_name="get_count"
    )
    print(f"  Final count:    {final_count}")
    print(f"  Expected count: {succeeded}")
    print()

    # --- Verdict ---
    print("=" * 60)
    if final_count == succeeded:
        print(f"  PASS: All {succeeded} increments preserved")
        print("=" * 60)
        return 0
    else:
        lost = succeeded - final_count
        loss_rate = (lost / succeeded * 100) if succeeded > 0 else 0
        print(f"  FAIL: LOST UPDATE DETECTED")
        print(f"  Lost {lost}/{succeeded} increments ({loss_rate:.1f}% loss rate)")
        print()
        print(f"  This confirms the concurrent state overwrite bug.")
        print(f"  See: Rally2/docs/genvm-state-mismatch-bug.md")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())
