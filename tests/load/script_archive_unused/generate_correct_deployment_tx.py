#!/usr/bin/env python3
"""
Generate a deployment transaction with correct data serialization matching the UI format.
This version properly serializes the deployment data for GenVM execution.
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
import base64


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


def encode_constructor_args_correct():
    """
    Encode constructor arguments matching the exact UI format.
    The UI sends: 880e04617267730d10 which decodes to:
    - 0x88 0x0e 0x04 "args" 0x0d 0x10

    This appears to be a custom encoding with:
    - Some type/length markers
    - The literal string "args"
    - Then the actual constructor data
    """
    # Build the exact bytes that the UI sends
    result = bytearray()
    result.append(0x88)  # Type/length marker
    result.append(0x0E)  # Another marker
    result.append(0x04)  # Length of "args"
    result.extend(b"args")  # The string "args"
    result.append(0x0D)  # Array with 1 element: (1 << 3) | 5
    result.append(0x10)  # Boolean true

    return bytes(result)


def create_deployment_payload(contract_code):
    """
    Create the deployment payload matching exactly what the backend expects.
    The backend expects RLP-encoded: [contract_code, calldata, leader_only]
    """
    # Get the constructor args in the exact UI format
    constructor_args = encode_constructor_args_correct()

    # The deployment data should be RLP encoded with structure:
    # [contract_code_bytes, constructor_args, leader_only]
    contract_bytes = contract_code.encode("utf-8")

    # RLP encode the deployment data
    # leader_only is False, which encodes as empty bytes b''
    deployment_data = rlp.encode(
        [contract_bytes, constructor_args, b""]  # leader_only = False
    )

    return deployment_data


def encode_addTransaction_call(
    sender, recipient, num_validators, max_rotations, deployment_data
):
    """
    Encode the addTransaction function call with proper ABI encoding.
    """
    web3 = Web3()

    # Function selector for addTransaction
    function_signature = "addTransaction(address,address,uint8,uint8,bytes)"
    function_selector = web3.keccak(text=function_signature)[:4]

    # Encode parameters using ABI encoding
    from eth_abi.abi import encode

    encoded_params = encode(
        ["address", "address", "uint8", "uint8", "bytes"],
        [sender, recipient, num_validators, max_rotations, deployment_data],
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

    try:
        response = requests.post(api_url, json=nonce_request, timeout=5)
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
    except requests.exceptions.RequestException as e:
        print(f"Warning: Could not get nonce from API: {e}", file=sys.stderr)
        return 0


def main():
    # Use the correct account (Hardhat account #1)
    # This matches what the UI uses
    PRIVATE_KEY = os.getenv(
        "PRIVATE_KEY",
        "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
    )

    # Create account from private key
    account = Account.from_key(PRIVATE_KEY)
    print(f"From address: {account.address}", file=sys.stderr)

    # Verify the expected address
    expected_address = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    if account.address.lower() != expected_address.lower():
        print(
            f"Warning: Address mismatch! Expected {expected_address}, got {account.address}",
            file=sys.stderr,
        )

    # ConsensusMain contract address (fixed in GenLayer)
    CONSENSUS_MAIN_ADDRESS = "0xb7278a61aa25c888815afc32ad3cc52ff24fe575"

    # Zero address for contract deployment
    ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

    # Transaction parameters (matching UI)
    NUM_VALIDATORS = 5
    MAX_ROTATIONS = 3

    # Get nonce from GenLayer Studio API
    studio_api_url = os.getenv("BASE_URL", "http://localhost:4000/api")
    nonce = get_nonce_from_studio(account.address, studio_api_url)
    print(f"Current nonce: {nonce}", file=sys.stderr)

    # Option to override nonce
    override_nonce = os.getenv("OVERRIDE_NONCE")
    if override_nonce is not None:
        nonce = int(override_nonce)
        print(f"Using overridden nonce: {nonce}", file=sys.stderr)

    # Load contract code
    contract_code = load_contract_code()
    print(f"Contract code loaded: {len(contract_code)} bytes", file=sys.stderr)

    # Create deployment payload with correct serialization
    deployment_data = create_deployment_payload(contract_code)
    print(f"Deployment data created: {len(deployment_data)} bytes", file=sys.stderr)

    # Debug: Show the calldata portion
    # The deployment data is RLP([contract_code, calldata, leader_only])
    # Let's decode it to verify
    decoded_deployment = rlp.decode(deployment_data)
    if len(decoded_deployment) >= 2:
        calldata_portion = decoded_deployment[1]
        print(f"Calldata portion (hex): {calldata_portion.hex()}", file=sys.stderr)
        print(
            f"Calldata portion (base64): {base64.b64encode(calldata_portion).decode()}",
            file=sys.stderr,
        )

    # Encode the addTransaction call
    tx_data = encode_addTransaction_call(
        account.address, ZERO_ADDRESS, NUM_VALIDATORS, MAX_ROTATIONS, deployment_data
    )

    print(f"Transaction data length: {len(tx_data)} bytes", file=sys.stderr)
    print(f"Function selector: 0x{tx_data[:4].hex()}", file=sys.stderr)

    # Create the transaction with correct parameters
    # Use max gas like the UI
    transaction = {
        "nonce": nonce,
        "gasPrice": 0,  # GenLayer uses zero gas price
        "gas": 0xFFFFFFFF,  # Max gas like UI
        "to": to_checksum_address(CONSENSUS_MAIN_ADDRESS),
        "value": 0,
        "data": tx_data,
        "chainId": 61127,  # GenLayer Studio Localnet Chain ID (0xeec7)
    }

    # Sign the transaction
    signed_tx = account.sign_transaction(transaction)

    # Output the raw transaction hex
    raw_tx = to_hex(signed_tx.raw_transaction)
    print(raw_tx)

    # Output transaction details for debugging
    print(f"\nTransaction details:", file=sys.stderr)
    print(f"  From: {account.address}", file=sys.stderr)
    print(f"  To: {CONSENSUS_MAIN_ADDRESS}", file=sys.stderr)
    print(f"  Nonce: {nonce}", file=sys.stderr)
    print(
        f"  Gas Limit: {transaction['gas']} (0x{transaction['gas']:x})", file=sys.stderr
    )
    print(f"  Chain ID: {transaction['chainId']}", file=sys.stderr)
    print(f"  Function: addTransaction", file=sys.stderr)
    print(f"  Expected calldata: 880e04617267730d10", file=sys.stderr)


if __name__ == "__main__":
    main()
