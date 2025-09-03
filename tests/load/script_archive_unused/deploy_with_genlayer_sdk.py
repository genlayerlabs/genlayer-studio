#!/usr/bin/env python3
"""
Deploy contract using GenLayer SDK approach.
This script creates a deployment transaction following the GenLayer SDK patterns.
"""

import os
import sys
import json
import base64
import requests
from pathlib import Path
from eth_account import Account
from eth_utils.address import to_checksum_address
from eth_utils.conversions import to_hex
from web3 import Web3
import rlp


class GenLayerClient:
    """Simple GenLayer client for contract deployment"""

    def __init__(self, rpc_url="http://localhost:4000/api", account=None):
        self.rpc_url = rpc_url
        self.account = account
        self.web3 = Web3()

    def get_nonce(self, address):
        """Get current nonce for an address"""
        request = {
            "jsonrpc": "2.0",
            "method": "eth_getTransactionCount",
            "params": [address, "latest"],
            "id": 1,
        }

        try:
            response = requests.post(self.rpc_url, json=request, timeout=5)
            result = response.json()
            if "result" in result:
                nonce_value = result["result"]
                if isinstance(nonce_value, str):
                    return (
                        int(nonce_value, 16)
                        if nonce_value.startswith("0x")
                        else int(nonce_value)
                    )
                return nonce_value
        except Exception as e:
            print(f"Warning: Could not get nonce: {e}", file=sys.stderr)
            return 0

    def get_contract_schema_for_code(self, contract_code):
        """Get contract schema for validation"""
        contract_hex = "0x" + contract_code.encode("utf-8").hex()

        request = {
            "jsonrpc": "2.0",
            "method": "gen_getContractSchemaForCode",
            "params": [contract_hex],
            "id": 1,
        }

        response = requests.post(self.rpc_url, json=request)
        result = response.json()

        if "result" in result:
            return result["result"]
        else:
            raise Exception(f"Failed to get contract schema: {result}")

    def deploy_contract(self, contract_code, constructor_args=None):
        """Deploy a contract using the GenLayer protocol"""

        if not self.account:
            raise Exception("No account set for deployment")

        # Validate contract with schema
        print("Validating contract schema...", file=sys.stderr)
        schema = self.get_contract_schema_for_code(contract_code)
        print(
            f"Contract schema validated: {json.dumps(schema, indent=2)}",
            file=sys.stderr,
        )

        # Encode constructor arguments
        calldata = self._encode_constructor_args(constructor_args)
        print(f"Constructor calldata (hex): {calldata.hex()}", file=sys.stderr)
        print(
            f"Constructor calldata (base64): {base64.b64encode(calldata).decode()}",
            file=sys.stderr,
        )

        # Create deployment payload
        deployment_data = self._create_deployment_payload(contract_code, calldata)

        # Create transaction
        nonce = self.get_nonce(self.account.address)
        print(f"Using nonce: {nonce}", file=sys.stderr)

        # Build addTransaction call to ConsensusMain
        tx_data = self._encode_add_transaction(
            sender=self.account.address,
            recipient="0x0000000000000000000000000000000000000000",  # Zero address for deployment
            num_validators=5,
            max_rotations=3,
            data=deployment_data,
        )

        # Create and sign transaction
        transaction = {
            "nonce": nonce,
            "gasPrice": 0,  # GenLayer uses zero gas price
            "gas": 0xFFFFFFFF,  # Max gas
            "to": to_checksum_address(
                "0xb7278a61aa25c888815afc32ad3cc52ff24fe575"
            ),  # ConsensusMain
            "value": 0,
            "data": tx_data,
            "chainId": 61999,  # GenLayer chain ID
        }

        signed_tx = self.account.sign_transaction(transaction)
        raw_tx = to_hex(signed_tx.raw_transaction)

        # Send transaction
        print(f"Sending deployment transaction...", file=sys.stderr)
        request = {
            "jsonrpc": "2.0",
            "method": "eth_sendRawTransaction",
            "params": [raw_tx],
            "id": 1,
        }

        response = requests.post(self.rpc_url, json=request)
        result = response.json()

        if "result" in result:
            tx_hash = result["result"]
            print(f"Transaction hash: {tx_hash}", file=sys.stderr)
            return tx_hash
        else:
            raise Exception(f"Failed to deploy contract: {result}")

    def _encode_constructor_args(self, args):
        """
        Encode constructor arguments in GenLayer format.
        The UI format is: 880e04617267730d10
        Which is: 0x88 0x0e 0x04 "args" 0x0d 0x10
        """
        # Build the exact format the UI uses
        result = bytearray()
        result.append(0x88)  # Type/length marker
        result.append(0x0E)  # Sub-type marker
        result.append(0x04)  # Length of "args"
        result.extend(b"args")  # The string "args"
        result.append(0x0D)  # Array with 1 element
        result.append(0x10)  # Boolean true (have_coin = true)

        return bytes(result)

    def _create_deployment_payload(self, contract_code, calldata):
        """Create the deployment payload in RLP format"""
        contract_bytes = contract_code.encode("utf-8")

        # RLP encode: [contract_code, calldata, leader_only]
        deployment_data = rlp.encode(
            [contract_bytes, calldata, b""]  # leader_only = False
        )

        return deployment_data

    def _encode_add_transaction(
        self, sender, recipient, num_validators, max_rotations, data
    ):
        """Encode the addTransaction call"""
        function_signature = "addTransaction(address,address,uint8,uint8,bytes)"
        function_selector = self.web3.keccak(text=function_signature)[:4]

        from eth_abi.abi import encode

        encoded_params = encode(
            ["address", "address", "uint8", "uint8", "bytes"],
            [sender, recipient, num_validators, max_rotations, data],
        )

        return function_selector + encoded_params

    def wait_for_receipt(self, tx_hash, timeout=60):
        """Wait for transaction receipt"""
        import time

        start_time = time.time()

        while time.time() - start_time < timeout:
            request = {
                "jsonrpc": "2.0",
                "method": "eth_getTransactionReceipt",
                "params": [tx_hash],
                "id": 1,
            }

            response = requests.post(self.rpc_url, json=request)
            result = response.json()

            if "result" in result and result["result"]:
                return result["result"]

            time.sleep(2)

        raise TimeoutError(
            f"Transaction {tx_hash} not confirmed after {timeout} seconds"
        )

    def get_transaction_status(self, tx_hash):
        """Get transaction status from the backend"""
        # Try to get transaction details from backend
        request = {
            "jsonrpc": "2.0",
            "method": "eth_getTransactionByHash",
            "params": [tx_hash],
            "id": 1,
        }

        response = requests.post(self.rpc_url, json=request)
        result = response.json()

        if "result" in result and result["result"]:
            return result["result"]
        return None


def main():
    """Main deployment function"""

    # Load contract
    contract_path = (
        Path(__file__).parent.parent.parent
        / "examples"
        / "contracts"
        / "wizard_of_coin.py"
    )
    with open(contract_path, "r") as f:
        contract_code = f.read()

    print(f"Loaded WizardOfCoin contract ({len(contract_code)} bytes)")

    # Setup account (Hardhat account #1)
    private_key = os.getenv(
        "PRIVATE_KEY",
        "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
    )
    account = Account.from_key(private_key)
    print(f"Using account: {account.address}")

    # Create GenLayer client
    rpc_url = os.getenv("BASE_URL", "http://localhost:4000/api")
    client = GenLayerClient(rpc_url=rpc_url, account=account)

    # Deploy contract
    try:
        print("\nDeploying WizardOfCoin contract...")
        tx_hash = client.deploy_contract(
            contract_code=contract_code, constructor_args={"have_coin": True}
        )

        print(f"\n✅ Contract deployment transaction sent!")
        print(f"Transaction hash: {tx_hash}")

        # Wait for transaction to be processed
        print("\nWaiting for transaction to be processed...")
        import time

        time.sleep(3)

        # Check transaction status
        tx_status = client.get_transaction_status(tx_hash)
        if tx_status:
            print(f"Transaction type: {tx_status.get('type', 'unknown')}")

        # Try to get receipt
        try:
            receipt = client.wait_for_receipt(tx_hash, timeout=30)

            # Extract contract address from logs
            contract_address = None
            if receipt.get("logs") and len(receipt["logs"]) > 0:
                for log in receipt["logs"]:
                    # Check if this is a NewTransaction event
                    if "topics" in log and len(log["topics"]) > 2:
                        # The contract address might be in topics[2] (recipient)
                        potential_address = "0x" + log["topics"][2][-40:]
                        if potential_address != "0x" + "0" * 40:  # Not zero address
                            contract_address = potential_address
                            break

                    # Also check the log address field
                    if (
                        "address" in log
                        and log["address"]
                        != "0xb7278A61aa25c888815aFC32Ad3cC52fF24fE575"
                    ):
                        contract_address = log["address"]
                        break

            if contract_address:
                print(f"\n✅ Contract deployed at: {contract_address}")

                # Save for later use
                with open(".last_deployed_contract", "w") as f:
                    f.write(contract_address)
                with open(".last_deployment_tx", "w") as f:
                    f.write(tx_hash)

                return contract_address
            else:
                print("⚠️  Could not extract contract address from receipt")
                print(f"Receipt: {json.dumps(receipt, indent=2)}")

        except TimeoutError:
            print(
                f"⚠️  Transaction not confirmed yet. Check manually with hash: {tx_hash}"
            )

            # Save transaction hash for manual checking
            with open(".last_deployment_tx", "w") as f:
                f.write(tx_hash)

    except Exception as e:
        print(f"\n❌ Deployment failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


def deploy_and_get_raw_tx():
    """Deploy contract but return raw transaction for shell script compatibility"""

    # Load contract
    contract_path = (
        Path(__file__).parent.parent.parent
        / "examples"
        / "contracts"
        / "wizard_of_coin.py"
    )
    with open(contract_path, "r") as f:
        contract_code = f.read()

    # Setup account
    private_key = os.getenv(
        "PRIVATE_KEY",
        "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
    )
    account = Account.from_key(private_key)

    # Create GenLayer client
    rpc_url = os.getenv("BASE_URL", "http://localhost:4000/api")
    client = GenLayerClient(rpc_url=rpc_url, account=account)

    # Validate contract
    client.get_contract_schema_for_code(contract_code)

    # Encode constructor arguments
    calldata = client._encode_constructor_args({"have_coin": True})

    # Create deployment payload
    deployment_data = client._create_deployment_payload(contract_code, calldata)

    # Get nonce
    nonce = client.get_nonce(account.address)

    # Build transaction data
    tx_data = client._encode_add_transaction(
        sender=account.address,
        recipient="0x0000000000000000000000000000000000000000",
        num_validators=5,
        max_rotations=3,
        data=deployment_data,
    )

    # Create and sign transaction
    transaction = {
        "nonce": nonce,
        "gasPrice": 0,
        "gas": 0xFFFFFFFF,
        "to": to_checksum_address("0xb7278a61aa25c888815afc32ad3cc52ff24fe575"),
        "value": 0,
        "data": tx_data,
        "chainId": 61999,
    }

    signed_tx = account.sign_transaction(transaction)
    raw_tx = to_hex(signed_tx.raw_transaction)

    # Output only the raw transaction for the shell script
    print(raw_tx)
    return raw_tx


if __name__ == "__main__":
    # Check if running in compatibility mode for shell script
    if os.getenv("RAW_TX_ONLY") == "1":
        deploy_and_get_raw_tx()
    else:
        main()
