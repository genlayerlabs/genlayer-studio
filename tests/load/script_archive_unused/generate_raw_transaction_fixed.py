#!/usr/bin/env python3
"""
Generate a raw signed transaction for deploying WizardOfCoin contract to GenLayer.
This version uses the correct GenLayer deployment format matching the UI.
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


def encode_genlayer_calldata(constructor_args):
    """
    Encode constructor arguments for GenLayer contracts.
    Uses the GenLayer-specific encoding format.
    """
    # For WizardOfCoin, we need to encode: [True] (have_coin boolean)
    # GenLayer encoding:
    # - Array with 1 element: 0x0D (type 5 = array, length 1 = (1 << 3) | 5)
    # - Boolean True: 0x10

    result = bytearray()
    result.append(0x0D)  # Array with 1 element
    result.append(0x10)  # True

    return bytes(result)


def encode_deploy_contract_data(
    sender, recipient, num_validators, max_rotations, contract_code, constructor_args
):
    """
    Encode the deployContract function call matching the UI format.
    This uses a different encoding than addTransaction.
    """
    web3 = Web3()

    # The UI uses function selector 0x27241a99 for deployContract
    # This appears to be a custom GenLayer function
    function_selector = bytes.fromhex("27241a99")

    # Encode the constructor arguments
    calldata = encode_genlayer_calldata(constructor_args)

    # Create the contract deployment payload
    # This needs to match the UI's RLP encoding structure

    # The UI transaction data structure appears to be:
    # 1. Contract code (as bytes)
    # 2. A wrapper with "args" field containing the constructor arguments

    # Build the RLP-encoded contract data
    contract_bytes = contract_code.encode("utf-8")

    # The UI adds additional encoding with "args" field
    # Looking at the hex, it seems to use a specific format:
    # - Contract code
    # - Then: 0x880e04617267730d10... where "args" = 0x61726773

    # Create the deployment data with the "args" wrapper
    args_label = b"args"

    # Build the full deployment data matching UI structure
    deployment_parts = []
    deployment_parts.append(contract_bytes)

    # Add the args section
    # The pattern seems to be: 0x88 0x0e 0x04 "args" then the calldata
    args_section = bytearray()
    args_section.append(0x88)  # Some kind of type marker
    args_section.append(0x0E)  # Length or type
    args_section.append(0x04)  # Length of "args"
    args_section.extend(args_label)
    args_section.extend(calldata)

    # Combine everything
    full_deployment_data = rlp.encode([contract_bytes, bytes(args_section)])

    # Now encode the function parameters
    from eth_abi.abi import encode

    # The function appears to take these parameters:
    # (address sender, address recipient, uint8 validators, uint8 rotations, bytes data)
    encoded_params = encode(
        ["address", "address", "uint8", "uint8", "bytes"],
        [sender, recipient, num_validators, max_rotations, full_deployment_data],
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
            f"Error getting nonce from Studio API: {response.status_code} - {response.text}"
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
    # Use the same account as the UI (account index 1)
    # Address: 0x701a6B9AbAF65a0E1d4B24fA875cAfA5EdB32205
    PRIVATE_KEY = os.getenv(
        "PRIVATE_KEY",
        "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
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

    # ConsensusMain contract address
    CONSENSUS_MAIN_ADDRESS = "0xb7278a61aa25c888815afc32ad3cc52ff24fe575"

    # Zero address for contract deployment
    ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

    # Transaction parameters (matching UI)
    NUM_VALIDATORS = 5
    MAX_ROTATIONS = 3

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
        sys.exit(1)

    # Load contract code
    contract_code = load_contract_code()
    print(f"Contract code loaded: {len(contract_code)} bytes", file=sys.stderr)

    # Encode the transaction data using the UI format
    tx_data = encode_deploy_contract_data(
        account.address,
        ZERO_ADDRESS,
        NUM_VALIDATORS,
        MAX_ROTATIONS,
        contract_code,
        constructor_args,
    )

    print(f"Transaction data length: {len(tx_data)} bytes", file=sys.stderr)
    print(f"Function selector: 0x{tx_data[:4].hex()}", file=sys.stderr)

    # Create the transaction matching UI parameters
    transaction = {
        "nonce": nonce,
        "gasPrice": 0,  # GenLayer uses zero gas price
        "gas": 0xFFFFFFFF,  # Use max gas like the UI (4294967295)
        "to": to_checksum_address(CONSENSUS_MAIN_ADDRESS),
        "value": 0,
        "data": tx_data,
        "chainId": 61127,  # GenLayer Studio Localnet Chain ID
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
    print(f"  Data length: {len(tx_data)} bytes", file=sys.stderr)


if __name__ == "__main__":
    main()
