# tests/common/request.py
import os
import eth_utils
import json
import requests
import time
from dotenv import load_dotenv
from eth_account import Account
from eth_abi import encode
from web3 import Web3
import base64
from tests.common.transactions import sign_transaction, encode_transaction_data

import backend.node.genvm.origin.calldata as calldata

load_dotenv()

ZERO_ADDRESS = "0x" + "0" * 40


def payload(function_name: str, *args) -> dict:
    return {
        "jsonrpc": "2.0",
        "method": function_name,
        "params": [*args],
        "id": 1,
    }


def post_request(
    payload: dict,
    protocol: str,
    host: str,
    port: str,
):
    jsonrpc_url = (
        protocol + "://" + host + ":" + port + "/api"
        if protocol and host and port
        else os.environ.get("JSONRPC_SERVER_URL", "http://localhost:4000")
    )
    return requests.post(
        jsonrpc_url + "/api",
        data=json.dumps(payload),
        headers={"Content-Type": "application/json"},
    )


def post_request_localhost(payload: dict):
    return post_request(payload, "http", "localhost", "4000")


def get_transaction_by_hash(transaction_hash: str):
    payload_data = payload("eth_getTransactionByHash", transaction_hash)
    raw_response = post_request_localhost(payload_data)
    parsed_raw_response = raw_response.json()
    return parsed_raw_response["result"]


def get_transaction_count(account_address: str):
    payload_data = payload("eth_getTransactionCount", account_address)
    raw_response = post_request_localhost(payload_data)
    parsed_raw_response = raw_response.json()
    return parsed_raw_response["result"]


def get_consensus_contract_address() -> str:
    payload_data = payload("sim_getConsensusContract", "ConsensusMain")
    raw_response = post_request_localhost(payload_data)
    parsed_raw_response = raw_response.json()
    return parsed_raw_response["result"]["address"]


def call_contract_method(
    contract_address: str,
    from_account: Account,
    method_name: str,
    method_args: list,
):
    encoded_data = eth_utils.hexadecimal.encode_hex(
        calldata.encode({"method": method_name, "args": method_args})
    )
    method_response = post_request_localhost(
        payload(
            "eth_call",
            {
                "to": contract_address,
                "from": from_account.address,
                "data": encoded_data,
            },
        )
    ).json()
    enc_result = method_response["result"]
    result = calldata.decode(eth_utils.hexadecimal.decode_hex(enc_result))
    print(f"Result of {method_name}: {result}")
    return result


def _prepare_transaction(
    account: Account,
    recipient_address: str | None,
    genlayer_transaction_data: str | bytes | None,
    value: int = 0,
) -> str:
    """Helper function to prepare a transaction for the consensus contract"""
    # Get consensus contract address from JSONRPC
    consensus_contract_address = get_consensus_contract_address()
    # Default values from environment or constants
    num_initial_validators = int(os.environ.get("DEFAULT_NUM_INITIAL_VALIDATORS", 1))
    max_rotations = int(os.environ.get("DEFAULT_CONSENSUS_MAX_ROTATIONS", 100))

    # Original logic for non-transfer transactions
    actual_recipient = ZERO_ADDRESS if recipient_address is None else recipient_address
    # Convert hex string to bytes if it starts with '0x'
    bytes_param = (
        b""
        if genlayer_transaction_data is None
        else (
            Web3.to_bytes(hexstr=genlayer_transaction_data)
            if isinstance(genlayer_transaction_data, str)
            and genlayer_transaction_data.startswith("0x")
            else genlayer_transaction_data
        )
    )

    params = encode(
        ["address", "address", "uint256", "uint256", "bytes"],
        [
            account.address,
            actual_recipient,
            num_initial_validators,
            max_rotations,
            bytes_param,
        ],
    )

    # Encode the addTransaction function call
    function_signature = "addTransaction(address,address,uint256,uint256,bytes)"
    function_selector = eth_utils.keccak(text=function_signature)[:4].hex()
    encoded_data = "0x" + function_selector + params.hex()

    # Get nonce and send transaction
    nonce = get_transaction_count(account.address)
    return sign_transaction(
        account=account,
        data=encoded_data,
        to=consensus_contract_address,
        value=value,
        nonce=nonce,
    )


def send_transaction(sender: Account, recipient: str, value: int):
    nonce = get_transaction_count(sender.address)
    signed_transaction = sign_transaction(
        account=sender,
        data=None,
        to=recipient,
        value=value,
        nonce=nonce,
    )
    return send_raw_transaction(signed_transaction)


def send_raw_transaction(signed_transaction: str):
    payload_data = payload("eth_sendRawTransaction", signed_transaction)
    raw_response = post_request_localhost(payload_data)
    call_method_response = raw_response.json()
    transaction_hash = call_method_response["result"]
    return wait_for_transaction(transaction_hash)


def wait_for_transaction(transaction_hash: str, interval: int = 10, retries: int = 15):
    attempts = 0
    while attempts < retries:
        transaction_response = get_transaction_by_hash(str(transaction_hash))
        status = transaction_response["status"]
        if status == "FINALIZED":
            return transaction_response
        time.sleep(interval)
        attempts += 1

    raise TimeoutError(
        f"Transaction {transaction_hash} not finalized after {retries} retries"
    )


def decode_base64(encoded_str):
    try:
        return base64.b64decode(encoded_str).decode("utf-8")
    except UnicodeDecodeError:
        return encoded_str


def decode_contract_state(contract_state):
    decoded_state = {}
    for key, value in contract_state.items():
        decoded_state[decode_base64(key)] = decode_base64(value)
    return decoded_state


def decode_nested_data(data):
    """
    Helper function to decode data from the transaction response to have more readable output
    """
    if isinstance(data, dict):
        decoded_data = {}
        for key, value in data.items():
            if key == "calldata" and isinstance(value, str):
                decoded_data[key] = calldata.decode(base64.b64decode(value))
            elif key == "contract_state" and isinstance(value, dict):
                decoded_data[key] = decode_contract_state(value)
            else:
                decoded_data[key] = decode_nested_data(value)
        return decoded_data
    elif isinstance(data, list):
        return [decode_nested_data(item) for item in data]
    else:
        return data
