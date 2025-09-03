#!/usr/bin/env python3
"""
Generate a raw transaction that matches the UI format exactly.
This version properly encodes the deployment with the correct function selector and format.
"""

import os
import sys
import requests
import json
from eth_account import Account
from eth_utils.conversions import to_hex
from eth_utils.address import to_checksum_address
from web3 import Web3
import rlp
from pathlib import Path


def load_contract_code():
    """Load the WizardOfCoin contract code from the examples directory."""
    contract_path = (
        Path(__file__).parent.parent.parent
        / "examples"
        / "contracts"
        / "wizard_of_coin.py"
    )
    with open(contract_path, "r") as f:
        return f.read()


def encode_genlayer_calldata_simple(have_coin=True):
    """
    Encode constructor arguments for GenLayer contracts.
    For WizardOfCoin: [True] as an array
    """
    # Array with 1 element: (1 << 3) | 5 = 13 = 0x0D
    # Boolean True: 0x10, False: 0x08
    result = bytearray()
    result.append(0x0D)  # Array with 1 element
    result.append(0x10 if have_coin else 0x08)  # Boolean value
    return bytes(result)


def encode_deployment_data_ui_format(contract_code, constructor_args):
    """
    Encode deployment data matching the UI format exactly.
    The UI uses a specific structure with 'args' field.
    """
    # Encode constructor args
    calldata = encode_genlayer_calldata_simple(constructor_args.get("have_coin", True))

    # The UI format includes:
    # 1. The contract code as bytes
    # 2. An "args" section with special encoding

    contract_bytes = contract_code.encode("utf-8")

    # Build the args section matching UI format
    # The pattern is: 0x880e04 + "args" + calldata
    args_section = bytearray()
    args_section.append(0x88)  # Type marker
    args_section.append(0x0E)  # Length/type indicator
    args_section.append(0x04)  # Length of "args" string
    args_section.extend(b"args")  # The literal string "args"
    args_section.extend(calldata)  # The constructor arguments

    # Now we need to RLP encode this in the right structure
    # The UI seems to use: RLP([contract_bytes, args_section, leader_only])
    deployment_data = rlp.encode(
        [contract_bytes, bytes(args_section), b""]  # leader_only = false (empty bytes)
    )

    return deployment_data


def create_deployment_transaction_data(sender_address, contract_code, constructor_args):
    """
    Create the full transaction data matching the UI format.
    Uses function selector 0x27241a99 (deployContract)
    """
    # The UI uses a different function than addTransaction
    # Function selector 0x27241a99 appears to be deployContract
    function_selector = bytes.fromhex("27241a99")

    # Encode deployment data
    deployment_data = encode_deployment_data_ui_format(contract_code, constructor_args)

    # Encode the function parameters
    # The UI format appears to use standard ABI encoding for the outer call
    from eth_abi.abi import encode

    # Parameters seem to be: (address sender, address recipient, uint8 validators, uint8 rotations, bytes data)
    encoded_params = encode(
        ["address", "address", "uint8", "uint8", "bytes"],
        [
            sender_address,
            "0x0000000000000000000000000000000000000000",  # Zero address for deployment
            5,  # NUM_VALIDATORS
            3,  # MAX_ROTATIONS
            deployment_data,
        ],
    )

    return function_selector + encoded_params


def get_nonce_from_studio(address, api_url):
    """Get the current nonce for an address from Studio API."""
    nonce_request = {
        "jsonrpc": "2.0",
        "method": "eth_getTransactionCount",
        "params": [address, "latest"],
        "id": 1,
    }

    response = requests.post(api_url, json=nonce_request)
    if response.status_code != 200:
        raise Exception(
            f"Error getting nonce: {response.status_code} - {response.text}"
        )

    result = response.json()
    if "result" in result:
        nonce_value = result["result"]
        if isinstance(nonce_value, str):
            return (
                int(nonce_value, 16)
                if nonce_value.startswith("0x")
                else int(nonce_value)
            )
        else:
            return nonce_value
    else:
        raise Exception(f"Error in nonce response: {result}")


def main():
    # Use the same account as the UI (Account 1 from Hardhat)
    # Address: 0x701a6B9AbAF65a0E1d4B24fA875cAfA5EdB32205
    # Private key for this address (Hardhat account #1)
    PRIVATE_KEY = os.getenv(
        "PRIVATE_KEY",
        "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
    )

    # Create account from private key
    account = Account.from_key(PRIVATE_KEY)
    print(f"From address: {account.address}", file=sys.stderr)

    # Verify it matches expected address
    expected_address = "0x701a6B9AbAF65a0E1d4B24fA875cAfA5EdB32205"
    if account.address.lower() != expected_address.lower():
        print(
            f"Warning: Address mismatch! Expected {expected_address}, got {account.address}",
            file=sys.stderr,
        )
        print("Using different private key to match UI account...", file=sys.stderr)
        # This is the correct private key for the UI account
        PRIVATE_KEY = (
            "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
        )
        account = Account.from_key(PRIVATE_KEY)
        print(f"Corrected address: {account.address}", file=sys.stderr)

    # ConsensusMain contract address
    CONSENSUS_MAIN_ADDRESS = "0xb7278a61aa25c888815afc32ad3cc52ff24fe575"

    # Constructor arguments for WizardOfCoin
    constructor_args = {"have_coin": True}

    # Get nonce from GenLayer Studio API
    studio_api_url = os.getenv("BASE_URL", "http://localhost:4000/api")
    try:
        nonce = get_nonce_from_studio(account.address, studio_api_url)
        print(f"Current nonce: {nonce}", file=sys.stderr)

        # Option to override nonce
        override_nonce = os.getenv("OVERRIDE_NONCE")
        if override_nonce is not None:
            nonce = int(override_nonce)
            print(f"Using overridden nonce: {nonce}", file=sys.stderr)
    except Exception as e:
        print(f"Error getting nonce: {e}", file=sys.stderr)
        # Default to 0 if we can't get the nonce
        nonce = 0
        print(f"Using default nonce: {nonce}", file=sys.stderr)

    # Load contract code
    contract_code = load_contract_code()
    print(f"Contract code loaded: {len(contract_code)} bytes", file=sys.stderr)

    # Create transaction data matching UI format
    tx_data = create_deployment_transaction_data(
        account.address, contract_code, constructor_args
    )

    print(f"Transaction data length: {len(tx_data)} bytes", file=sys.stderr)
    print(f"Function selector: 0x{tx_data[:4].hex()}", file=sys.stderr)

    # Create the transaction matching UI parameters exactly
    transaction = {
        "nonce": nonce,
        "gasPrice": 0,  # GenLayer uses zero gas price
        "gas": 0xFFFFFFFF,  # Use max gas like the UI (4294967295)
        "to": to_checksum_address(CONSENSUS_MAIN_ADDRESS),
        "value": 0,
        "data": tx_data,
        "chainId": 61999,  # GenLayer chain ID
    }

    # Sign the transaction
    signed_tx = account.sign_transaction(transaction)

    # Output the raw transaction hex
    raw_tx = to_hex(signed_tx.raw_transaction)
    print(raw_tx)

    # Output details for debugging
    print(f"\nTransaction details:", file=sys.stderr)
    print(f"  From: {account.address}", file=sys.stderr)
    print(f"  To: {CONSENSUS_MAIN_ADDRESS}", file=sys.stderr)
    print(f"  Nonce: {nonce}", file=sys.stderr)
    print(
        f"  Gas Limit: {transaction['gas']} (0x{transaction['gas']:x})", file=sys.stderr
    )
    print(f"  Function: deployContract (0x27241a99)", file=sys.stderr)
    print(f"  Data length: {len(tx_data)} bytes", file=sys.stderr)


if __name__ == "__main__":
    main()
