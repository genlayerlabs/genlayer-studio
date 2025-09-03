#!/usr/bin/env python3
"""
Generate a raw signed transaction for deploying WizardOfCoin contract to GenLayer.
This script creates a properly formatted transaction with dynamic nonce handling.
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
    contract_path = Path(__file__).parent.parent.parent / "examples" / "contracts" / "wizard_of_coin.py"
    with open(contract_path, 'r') as f:
        return f.read()


def encode_calldata(constructor_args):
    """
    Encode constructor arguments for the contract.
    For WizardOfCoin, this is just the have_coin boolean.
    
    The calldata should be encoded as an array of arguments.
    """
    # Import the calldata encoder from the backend (we'll implement it here)
    # Based on backend/node/genvm/origin/calldata.py
    
    # For constructor, we need to pass arguments as an array
    # The have_coin boolean is the only argument
    have_coin = constructor_args.get("have_coin", True)
    
    # Create an array with the single boolean argument
    # Array encoding: TYPE_ARR = 5, so for 1 element: (1 << 3) | 5 = 13 = 0x0D
    # Then the boolean: SPECIAL_TRUE = 0x10 or SPECIAL_FALSE = 0x08
    
    result = bytearray()
    # Array with 1 element: (1 << 3) | 5 = 13
    result.append(0x0D)
    # Boolean value
    if have_coin:
        result.append(0x10)  # SPECIAL_TRUE
    else:
        result.append(0x08)  # SPECIAL_FALSE
    
    return bytes(result)


def encode_deployment_data(contract_code, constructor_args, leader_only=False):
    """
    Encode the deployment data using RLP encoding.
    Structure: [contract_code, calldata, leader_only]
    """
    calldata = encode_calldata(constructor_args)
    
    # Create the deployment payload
    deployment_data = [
        contract_code.encode('utf-8'),  # Contract code as bytes
        calldata,                        # Constructor arguments
        b'\x01' if leader_only else b''  # Leader only flag
    ]
    
    return rlp.encode(deployment_data)


def encode_add_transaction(sender, recipient, num_validators, max_rotations, tx_data):
    """
    Encode the addTransaction function call for ConsensusMain contract.
    """
    # We only need Web3 for keccak hashing, not for network connection
    web3 = Web3()
    
    # Function signature for addTransaction
    function_signature = "addTransaction(address,address,uint8,uint8,bytes)"
    function_selector = web3.keccak(text=function_signature)[:4]
    
    # Encode parameters
    # Using eth_abi to encode the parameters
    from eth_abi.abi import encode
    encoded_params = encode(
        ['address', 'address', 'uint8', 'uint8', 'bytes'],
        [sender, recipient, num_validators, max_rotations, tx_data]
    )
    
    return function_selector + encoded_params


def get_nonce_from_studio(address, api_url):
    """Get the current nonce for an address from Studio API."""
    nonce_request = {
        "jsonrpc": "2.0",
        "method": "eth_getTransactionCount",
        "params": [address, "latest"],
        "id": 1
    }

    
    
    response = requests.post(api_url, json=nonce_request)
    if response.status_code != 200:
        raise Exception(f"Error getting nonce from Studio API: {response.status_code} - {response.text}")
    
    result = response.json()
    print(f"Nonce response: {result}", file=sys.stderr)
    if 'result' in result:
        # Studio API returns nonce as integer, not hex
        nonce_value = result['result']
        if isinstance(nonce_value, str):
            # If it's a hex string, convert it
            return int(nonce_value, 16) if nonce_value.startswith('0x') else int(nonce_value)
        else:
            # If it's already an integer, use it directly
            return nonce_value
    else:
        raise Exception(f"Error in nonce response: {result}")


def main():
    # Account configuration (using test account)
    # This is account index 8 from Hardhat's default accounts
    PRIVATE_KEY = os.getenv("PRIVATE_KEY", "0x701b615bbdfb9de65240bc28bd21ddc0d996645a3dd57e7b12bc2bdf6f192c82")
    # Create account from private key to get the correct address
    account = Account.from_key(PRIVATE_KEY)
    FROM_ADDRESS = account.address
    print(f"From address: {FROM_ADDRESS}", file=sys.stderr)
    
    # ConsensusMain contract address (fixed in GenLayer)
    CONSENSUS_MAIN_ADDRESS = "0xb7278a61aa25c888815afc32ad3cc52ff24fe575"
    print(f"Consensus main address: {CONSENSUS_MAIN_ADDRESS}", file=sys.stderr)
    
    # Zero address for contract deployment
    ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
    print(f"Zero address: {ZERO_ADDRESS}", file=sys.stderr)
    # Transaction parameters
    NUM_VALIDATORS = 5
    MAX_ROTATIONS = 3
    
    # Constructor arguments for WizardOfCoin
    constructor_args = {"have_coin": True}
    print(f"Constructor args: {constructor_args}", file=sys.stderr)
    # Get nonce from GenLayer Studio API (NOT from Hardhat directly)
    studio_api_url = os.getenv("BASE_URL", "http://localhost:4000/api")
    print(f"Studio API URL: {studio_api_url}", file=sys.stderr)
    try:
        # Get nonce for the account address (derived from private key)
        nonce = get_nonce_from_studio(account.address, studio_api_url)
        print(f"Current nonce from API: {nonce}", file=sys.stderr)
        
        # Option to override nonce for testing
        override_nonce = os.getenv("OVERRIDE_NONCE")
        if override_nonce is not None:
            nonce = int(override_nonce)
            print(f"Using overridden nonce: {nonce}", file=sys.stderr)
    except Exception as e:
        print(f"Error getting nonce: {e}", file=sys.stderr)
        sys.exit(1)
    
    # Load contract code
    contract_code = load_contract_code()
    
    # Print first 100 characters of contract code for debugging
    print(f"Contract code (first 100 chars): {contract_code[:100]}", file=sys.stderr)
    print(f"Constructor args: {constructor_args}", file=sys.stderr)
    # Encode deployment data
    deployment_data = encode_deployment_data(contract_code, constructor_args)
    print(f"Deployment data length: {len(deployment_data)} bytes", file=sys.stderr)
    print(f"Deployment data (hex): {deployment_data.hex()[:100]}...", file=sys.stderr)
    
    # Encode the addTransaction call
    # Note: The UI seems to use a different sender address in the function params
    # This might be the issue - let's use the same approach
    tx_data = encode_add_transaction(
        account.address,  # Use the actual account address from the private key
        ZERO_ADDRESS,  # recipient is zero address for deployment
        NUM_VALIDATORS,
        MAX_ROTATIONS,
        deployment_data
    )
    
    # Create the transaction (ensure to address is checksum)
    # Use legacy transaction (type 0) - backend only supports this
    # Don't include 'type' field for legacy transactions in eth_account
    transaction = {
        'nonce': nonce,
        'gasPrice': 0,  # GenLayer uses zero gas price
        'gas': 21000,  # Standard gas limit used by UI (21000 = 0x5208)
        'to': to_checksum_address(CONSENSUS_MAIN_ADDRESS),  # Send to ConsensusMain, NOT zero address!
        # 'to': to_checksum_address(ZERO_ADDRESS),
        'data': tx_data,
        'chainId': 61999  # GenLayer chain ID
    }
    
    # Sign the transaction (account already created above)
    signed_tx = account.sign_transaction(transaction)
    
    # Output the raw transaction hex
    raw_tx = to_hex(signed_tx.raw_transaction)
    print(raw_tx)
    
    # Also output transaction details to stderr for debugging
    print(f"Transaction details:", file=sys.stderr)
    print(f"  From: {account.address}", file=sys.stderr)
    print(f"  To: {CONSENSUS_MAIN_ADDRESS}", file=sys.stderr)
    print(f"  Nonce: {nonce}", file=sys.stderr)
    print(f"  Data length: {len(tx_data)} bytes", file=sys.stderr)


if __name__ == "__main__":
    main()