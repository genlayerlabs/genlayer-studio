#!/usr/bin/env python3
"""
Generate a deployment transaction that exactly matches the UI format.
This version properly encodes the nested RLP structure.
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


def encode_genlayer_constructor_args(have_coin=True):
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


def create_deployment_data(contract_code, constructor_args):
    """
    Create the deployment data in the exact UI format.
    Looking at the UI transaction, it seems to use a nested RLP structure.
    """
    # Get the contract code as bytes
    contract_bytes = contract_code.encode("utf-8")

    # Encode constructor arguments
    calldata = encode_genlayer_constructor_args(constructor_args.get("have_coin", True))

    # The UI transaction has a special encoding with "args" wrapper
    # Looking at the hex dump: 880e04617267730d10
    # 88 = some type marker
    # 0e = another marker
    # 04 = length of "args"
    # 61726773 = "args" in hex
    # 0d10 = the constructor args

    # Create the args wrapper
    args_wrapper = bytearray()
    args_wrapper.append(0x88)  # Type/length marker
    args_wrapper.append(0x0E)  # Sub-type marker
    args_wrapper.append(0x04)  # Length of "args"
    args_wrapper.extend(b"args")  # The string "args"
    args_wrapper.extend(calldata)  # The actual constructor arguments

    # The deployment data should be RLP encoded with structure:
    # [contract_code, args_wrapper, leader_only]
    deployment_data = rlp.encode(
        [contract_bytes, bytes(args_wrapper), b""]  # leader_only = false (empty bytes)
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
    # This is the correct private key for account index 1
    PRIVATE_KEY = os.getenv(
        "PRIVATE_KEY",
        "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
    )

    # Create account from private key
    account = Account.from_key(PRIVATE_KEY)
    print(f"From address: {account.address}", file=sys.stderr)

    # Verify the address matches what we expect
    expected_address = (
        "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"  # This is account[1]
    )
    if account.address != expected_address:
        print(
            f"Note: Address is {account.address}, expected {expected_address}",
            file=sys.stderr,
        )
        print("Adjusting to use the UI account address...", file=sys.stderr)
        # Actually the UI uses a different account, let's use the exact same one
        # From the UI transaction analysis, it uses: 0x701a6B9AbAF65a0E1d4B24fA875cAfA5EdB32205
        # Let's find the right private key for this address
        # This appears to be account[1] with a different derivation
        # Let me use the exact account from the UI
        PRIVATE_KEY = (
            "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
        )
        account = Account.from_key(PRIVATE_KEY)
        print(f"Adjusted address: {account.address}", file=sys.stderr)

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
        print(f"Warning: Could not get nonce: {e}", file=sys.stderr)
        print("Using nonce 0", file=sys.stderr)
        nonce = 0

    # Load contract code
    contract_code = load_contract_code()
    print(f"Contract code loaded: {len(contract_code)} bytes", file=sys.stderr)

    # Create deployment data in UI format
    deployment_data = create_deployment_data(contract_code, constructor_args)
    print(f"Deployment data created: {len(deployment_data)} bytes", file=sys.stderr)

    # Encode the addTransaction call
    tx_data = encode_addTransaction_call(
        account.address, ZERO_ADDRESS, NUM_VALIDATORS, MAX_ROTATIONS, deployment_data
    )

    print(f"Transaction data length: {len(tx_data)} bytes", file=sys.stderr)
    print(f"Function selector: 0x{tx_data[:4].hex()}", file=sys.stderr)

    # Create the transaction matching UI parameters
    # The UI uses gas limit 0xffffffff instead of 0x5208
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

    # Output transaction details for debugging
    print(f"\nTransaction details:", file=sys.stderr)
    print(f"  From: {account.address}", file=sys.stderr)
    print(f"  To: {CONSENSUS_MAIN_ADDRESS}", file=sys.stderr)
    print(f"  Nonce: {nonce}", file=sys.stderr)
    print(
        f"  Gas Limit: {transaction['gas']} (0x{transaction['gas']:x})", file=sys.stderr
    )
    print(f"  Function: addTransaction (0xd20aae67)", file=sys.stderr)
    print(f"  Data length: {len(tx_data)} bytes", file=sys.stderr)
    print(
        f"  Deployment data structure: [contract_code, args_wrapper, leader_only]",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
