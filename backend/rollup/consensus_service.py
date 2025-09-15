import json
import os
from web3 import Web3
from typing import Optional, Dict, Any
from pathlib import Path
from hexbytes import HexBytes
import re

from backend.rollup.default_contracts.consensus_main import (
    get_default_consensus_main_contract,
)
from backend.rollup.web3_pool import Web3ConnectionPool


class ConsensusService:
    def __init__(self):
        """
        Initialize the ConsensusService class
        """
        # Use singleton Web3 connection pool
        self.web3 = Web3ConnectionPool.get()

    def _get_contract(self, contract_name: str):
        """
        Get a contract instance

        Returns:
            Contract: The contract instance

        Raises:
            Exception: If the contract deployment data cannot be loaded or the contract does not exist
        """
        # Load deployment data
        deployment_data = self._load_deployment_data(contract_name)
        if not deployment_data:
            # For ConsensusMain, we'll use the default contract if deployment not found
            if contract_name == "ConsensusMain":
                default_contract = get_default_consensus_main_contract()
                if default_contract and "address" in default_contract and "abi" in default_contract:
                    return self.web3.eth.contract(
                        address=default_contract["address"], 
                        abi=default_contract["abi"]
                    )
            raise Exception(f"Failed to load {contract_name} deployment data")

        # Verify contract exists on chain
        code = self.web3.eth.get_code(deployment_data["address"])
        if code == b"" or code == "0x":
            raise Exception(
                f"No contract code found at address {deployment_data['address']}"
            )

        return self.web3.eth.contract(
            address=deployment_data["address"], abi=deployment_data["abi"]
        )

    def load_contract(self, contract_name: str):
        """
        Load a contract from deployment data

        Args:
            contract_name (str): Name of the contract to load

        Returns:
            dict: Contract data including address, abi and functions
            None: If there was an error loading the contract
        """
        try:
            contract = self._get_contract(contract_name)
            deployment_data = self._load_deployment_data(contract_name)

            return {
                "address": contract.address,
                "abi": contract.abi,
                "functions": contract.functions,
                "bytecode": (
                    deployment_data.get("bytecode") if deployment_data else None
                ),
            }

        except Exception as e:
            if contract_name == "ConsensusMain":
                default_contract = get_default_consensus_main_contract()
                print(
                    f"[CONSENSUS_SERVICE]: Error loading contract from network, retrieving default contract: {str(e)}"
                )
                return default_contract
            else:
                raise e

    def _load_deployment_data(self, contract_name: str) -> Optional[Dict[str, Any]]:
        """
        Load contract deployment data from deployments

        Args:
            contract_name (str): The name of the contract to load

        Returns:
            Optional[Dict[str, Any]]: The deployment data or None if loading fails
        """
        try:
            deployment_path = (
                Path("/app/hardhat/deployments/genlayer_network")
                / f"{contract_name}.json"
            )

            if not deployment_path.exists():
                print(
                    f"[CONSENSUS_SERVICE]: Deployment file not found at {deployment_path}"
                )
                return None

            with open(deployment_path, "r") as f:
                return json.load(f)

        except Exception as e:
            print(f"[CONSENSUS_SERVICE]: Error loading deployment data: {str(e)}")
            return None

    def forward_transaction(self, transaction: str | HexBytes) -> dict:
        """
        Forward a transaction to the consensus rollup
        """
        tx_hash = self.web3.eth.send_raw_transaction(transaction)
        receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash)
        return receipt

    def wait_new_transaction_event(self, receipt: dict) -> dict:
        """
        Wait for NewTransaction event from receipt
        """
        consensus_main_contract = self._get_contract("ConsensusMain")

        # Get NewTransaction events from receipt
        new_tx_events = consensus_main_contract.events.NewTransaction().process_receipt(
            receipt
        )

        if new_tx_events:
            # Extract event data
            tx_id = new_tx_events[0]["args"]["txId"]
            recipient = new_tx_events[0]["args"]["recipient"]
            activator = new_tx_events[0]["args"]["activator"]

            # Convert tx_id from bytes to hex string for better readability
            tx_id_hex = "0x" + tx_id.hex() if isinstance(tx_id, bytes) else tx_id

            return {
                "receipt": receipt,
                "tx_id": tx_id,
                "tx_id_hex": tx_id_hex,  # Adding hex version for easier reading
                "recipient": recipient,
                "activator": activator,
            }
        else:
            print("[CONSENSUS_SERVICE]: No NewTransaction event found in receipt")
            return receipt

    def add_transaction(
        self, transaction: dict, from_address: str, retry: bool = True
    ) -> str:
        """
        Forward a transaction to the consensus rollup and wait for NewTransaction event
        """
        if not self.web3.is_connected():
            print(
                "[CONSENSUS_SERVICE]: Not connected to Hardhat node, skipping transaction forwarding"
            )
            return None

        try:
            receipt = self.forward_transaction(transaction)
            return self.wait_new_transaction_event(receipt)

        except Exception as e:
            error_str = str(e)
            error_type = (
                "nonce_too_high"
                if "nonce too high" in error_str.lower()
                else "nonce_too_low" if "nonce too low" in error_str.lower() else None
            )
            if error_type:
                # Extract expected and current nonce from error message
                match = re.search(
                    r"Expected nonce to be (\d+) but got (\d+)", error_str
                )
                if match:
                    current_nonce = int(match.group(2))

                    # Set the nonce to the expected value
                    print(
                        f"[CONSENSUS_SERVICE]: Setting nonce for {from_address} to {current_nonce}"
                    )
                    self.web3.provider.make_request(
                        "hardhat_setNonce", [from_address, hex(current_nonce)]
                    )

                    if retry:
                        return self.add_transaction(
                            transaction, from_address, retry=False
                        )
                else:
                    print(
                        f"[CONSENSUS_SERVICE]: Could not parse nonce from error message: {error_str}"
                    )

            print(f"[CONSENSUS_SERVICE]: Error forwarding transaction: {error_str}")
            return None

    def emit_transaction_event(self, event_name: str, account: dict, *args):
        """
        Generic method to emit transaction events

        Args:
            event_name (str): Name of the event function to call
            account (dict): Account object containing address and private key
            *args: Arguments to pass to the event function
        """
        if not self.web3.is_connected():
            print(
                "[CONSENSUS_SERVICE]: Not connected to Hardhat node, skipping transaction forwarding"
            )
            return None

        if account.get("private_key") is not None:
            account_address = account["address"]
            account_private_key = account["private_key"]
        else:
            print(
                f"[CONSENSUS_SERVICE]: Error emitting {event_name}: Account object must contain private_key"
            )
            return None

        consensus_main_contract = self._get_contract("ConsensusMain")

        try:
            # Get the function from the contract
            event_function = getattr(consensus_main_contract.functions, event_name)

            # Build and send transaction
            tx = event_function(*args).build_transaction(
                {
                    "from": account_address,
                    "gas": 50000000,
                    "gasPrice": 0,
                    "nonce": self.web3.eth.get_transaction_count(account_address),
                }
            )

            # Sign and send transaction
            signed_tx = self.web3.eth.account.sign_transaction(
                tx, private_key=account_private_key
            )

            receipt = self.forward_transaction(signed_tx.raw_transaction)

            if (
                event_name == "emitTransactionAccepted"
                or event_name == "emitTransactionFinalized"
            ):
                new_tx_events = (
                    consensus_main_contract.events.NewTransaction().process_receipt(
                        receipt
                    )
                )

                tx_ids_hex = []
                for new_tx_event in new_tx_events:
                    tx_id = new_tx_event["args"]["txId"]
                    tx_ids_hex.append(
                        "0x" + tx_id.hex() if isinstance(tx_id, bytes) else tx_id
                    )

                return {
                    "receipt": receipt,
                    "tx_ids_hex": tx_ids_hex,
                }

            return receipt

        except Exception as e:
            print(
                f"[CONSENSUS_SERVICE]: Error emitting {event_name}: {str(e)}\n\tevent_name={event_name} account={account} args={args}"
            )
            return None
