import json
from eth_utils import keccak, to_bytes, to_hex
from typing import Optional, Dict, Any
from pathlib import Path
from hexbytes import HexBytes
import re

from backend.rollup.default_contracts.consensus_main import (
    get_default_consensus_main_contract,
)
from backend.rollup.web3_pool import Web3ConnectionPool

FEE_AWARE_SHADOW_SELECTORS = {
    bytes.fromhex("35a251fb"),
    bytes.fromhex("98863702"),
}
LEGACY_ADD_TRANSACTION_SELECTOR = keccak(
    text="addTransaction(address,address,uint256,uint256,bytes)"
)[:4]


class ConsensusService:
    def __init__(self):
        """
        Initialize the ConsensusService class
        """
        # Use singleton Web3 connection pool
        self.web3 = Web3ConnectionPool.get()

    @staticmethod
    def public_consensus_main_address() -> str:
        """Return Studio's user-facing virtual ConsensusMain address.

        A connected Hardhat helper may be deployed at a different ephemeral
        address. That helper is an internal shadow only; signed client
        envelopes continue to target the stable address exported by the SDK.
        """

        return str(get_default_consensus_main_contract()["address"])

    def _get_contract(self, contract_name: str):
        """
        Get a contract instance

        Returns:
            Contract: The contract instance.
            None: If deployment data is unavailable for this contract on this
                instance — the caller should treat this as a soft "not found"
                rather than a system error. Hosted Studio does not ship the
                hardhat deployment artifacts for the rollup-side consensus
                contracts (Queues / RevealingPhase / IdlenessPhase / etc.)
                because newer genlayer-js clients carry that info in their
                chain config; older clients still fall through to this RPC.

        Raises:
            Exception: If the contract should exist but the on-chain code is
                missing, or for any other unexpected error.
        """
        # Load deployment data
        deployment_data = self._load_deployment_data(contract_name)
        if not deployment_data:
            # For ConsensusMain, we'll use the default contract if deployment not found
            if contract_name == "ConsensusMain":
                default_contract = get_default_consensus_main_contract()
                if (
                    default_contract
                    and "address" in default_contract
                    and "abi" in default_contract
                ):
                    return self.web3.eth.contract(
                        address=default_contract["address"], abi=default_contract["abi"]
                    )
            # Soft "not found" — caller (endpoints.get_contract) will turn
            # this into a NotFoundError, which the RPC framework logs at the
            # JSONRPCError soft-error path instead of the noisy
            # "Unexpected error in sim_getConsensusContract" stderr print.
            return None

        # Verify contract exists on chain
        code = self.web3.eth.get_code(deployment_data["address"])
        if code == b"" or code == "0x":
            raise Exception(
                f"No contract code found at address {deployment_data['address']}"
            )

        return self.web3.eth.contract(
            address=deployment_data["address"], abi=deployment_data["abi"]
        )

    def generate_transaction_hash(self, raw_transaction: str) -> str:
        """
        Generate a transaction hash
        """
        return to_hex(keccak(to_bytes(hexstr=raw_transaction)))

    def load_contract(self, contract_name: str):
        """Deprecated: consensus contract info is now provided by genlayer-js chain config.

        Load a contract from deployment data.

        Args:
            contract_name (str): Name of the contract to load

        Returns:
            dict: Contract data including address, abi and functions
            None: If there was an error loading the contract
        """
        try:
            contract = self._get_contract(contract_name)
            if contract is None:
                # _get_contract returns None for the soft "deployment data
                # missing" case — propagate that up so endpoints.get_contract
                # raises NotFoundError instead of the caller seeing a bare
                # exception.
                return None
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

    @staticmethod
    def _bind_shadow_sender_calldata(
        calldata: str | HexBytes,
        authoritative_sender: str | None,
    ) -> str | HexBytes:
        """Replace the user-controlled sender word with the recovered signer.

        Consensus' CreationPhase ignores a spoofed ``params.sender`` on an
        ordinary EOA submission and attributes the transaction to the direct
        caller. Studio relays through a funded Hardhat account, so the shadow
        helper cannot derive that caller itself and must receive the already-
        verified signer from the RPC boundary.
        """

        if authoritative_sender is None:
            return calldata
        payload = bytes(HexBytes(calldata))
        if len(payload) < 4:
            return calldata
        selector = payload[:4]
        if selector in FEE_AWARE_SHADOW_SELECTORS:
            sender_offset = 4 + 32  # selector + dynamic tuple offset word
        elif selector == LEGACY_ADD_TRANSACTION_SELECTOR:
            sender_offset = 4
        else:
            return calldata
        if len(payload) < sender_offset + 32:
            raise RuntimeError("InvalidShadowTransactionEncoding")
        sender = to_bytes(hexstr=authoritative_sender)
        if len(sender) != 20:
            raise RuntimeError("InvalidShadowTransactionSender")
        bound = (
            payload[:sender_offset]
            + (b"\x00" * 12)
            + sender
            + payload[sender_offset + 32 :]
        )
        return to_hex(bound)

    def forward_shadow_transaction(
        self,
        calldata: str | HexBytes,
        authoritative_sender: str | None = None,
    ) -> dict:
        """Submit verified user calldata through Hardhat's funded system account.

        Studio account balances and nonces live in Postgres, not in the helper
        EVM. Replaying the user's raw signed envelope against Hardhat therefore
        fails for ordinary Studio accounts and previously caused deployments
        to fall back to an unrelated random address. The RPC layer has already
        recovered the signer and validated the complete fee envelope; this
        shadow call exists only to let the protocol helper author the child id
        and CREATE/CREATE2 recipient.
        """

        consensus_main = self._get_contract("ConsensusMain")
        if consensus_main is None:
            raise RuntimeError("ConsensusMainUnavailable")
        accounts = self.web3.eth.accounts
        if not accounts:
            raise RuntimeError("ConsensusShadowAccountUnavailable")
        bound_calldata = self._bind_shadow_sender_calldata(
            calldata,
            authoritative_sender,
        )
        tx_hash = self.web3.eth.send_transaction(
            {
                "from": accounts[0],
                "to": consensus_main.address,
                "data": bound_calldata,
                "value": 0,
            }
        )
        return self.web3.eth.wait_for_transaction_receipt(tx_hash)

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
        self,
        transaction: dict,
        from_address: str,
        retry: bool = True,
        calldata: str | HexBytes | None = None,
    ) -> Dict[str, Any] | None:
        """
        Forward a transaction to the consensus rollup and wait for NewTransaction event
        """
        if self.web3 is None or not self.web3.is_connected():
            # print(
            #     "[CONSENSUS_SERVICE]: Not connected to Hardhat node, skipping transaction forwarding"
            # )
            return None

        try:
            receipt = (
                self.forward_shadow_transaction(calldata, from_address)
                if calldata is not None
                else self.forward_transaction(transaction)
            )
            details = self.wait_new_transaction_event(receipt)
            if not isinstance(details, dict) or "tx_id" not in details:
                raise RuntimeError("NewTransactionEventMissing")
            return details

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
                            transaction,
                            from_address,
                            retry=False,
                            calldata=calldata,
                        )
                else:
                    print(
                        f"[CONSENSUS_SERVICE]: Could not parse nonce from error message: {error_str}"
                    )

            print(f"[CONSENSUS_SERVICE]: Error forwarding transaction: {error_str}")
            return None

    def transaction_forwarding_skipped(self, account: dict) -> bool:
        """Reports whether emit_transaction_event is a deliberate no-op.

        Studio runs in deployments that have no rollup at all — the load-test
        compose brings up only jsonrpc + consensus-worker, and hosted Studio
        may hold an account with no private key. In both modes
        emit_transaction_event returns None *by design*, which callers must
        not confuse with a forwarding failure (which also returns None).
        """
        if self.web3 is None or not self.web3.is_connected():
            return True
        return account.get("private_key") is None

    def emit_transaction_event(self, event_name: str, account: dict, *args):
        """
        Generic method to emit transaction events

        Args:
            event_name (str): Name of the event function to call
            account (dict): Account object containing address and private key
            *args: Arguments to pass to the event function
        """
        if self.transaction_forwarding_skipped(account):
            if self.web3 is not None and self.web3.is_connected():
                print(
                    f"[CONSENSUS_SERVICE]: Error emitting {event_name}: Account object must contain private_key"
                )
            return None

        account_address = account["address"]
        account_private_key = account["private_key"]

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
                recipients = []
                for new_tx_event in new_tx_events:
                    tx_id = new_tx_event["args"]["txId"]
                    tx_ids_hex.append(
                        "0x" + tx_id.hex() if isinstance(tx_id, bytes) else tx_id
                    )
                    recipients.append(new_tx_event["args"]["recipient"])

                # The Studio helper bridge makes each parent/phase/payload
                # emission one-shot. A worker retry emits no new events, but
                # it can recover the exact child ids stored by the first call.
                # Always prefer that durable view when the deployed helper
                # exposes it; the event-derived list remains compatible with
                # older local deployments.
                try:
                    stored_tx_ids = (
                        consensus_main_contract.functions.getInternalMessageTxIds(
                            args[0],
                            event_name == "emitTransactionAccepted",
                            args[1],
                        ).call()
                    )
                    tx_ids_hex = [
                        "0x" + tx_id.hex() if isinstance(tx_id, bytes) else tx_id
                        for tx_id in stored_tx_ids
                    ]
                except Exception:
                    pass

                # Deploy messages intentionally submit recipient zero. The
                # helper returns the actual CREATE/CREATE2 ghost address, and
                # retries recover that same address from durable storage.
                try:
                    recipients = list(
                        consensus_main_contract.functions.getInternalMessageRecipients(
                            args[0],
                            event_name == "emitTransactionAccepted",
                            args[1],
                        ).call()
                    )
                except Exception:
                    pass

                return {
                    "receipt": receipt,
                    "tx_ids_hex": tx_ids_hex,
                    "recipients": recipients,
                }

            return receipt

        except Exception as e:
            print(
                f"[CONSENSUS_SERVICE]: Error emitting {event_name}: {str(e)}\n\tevent_name={event_name} account={account} args={args}"
            )
            return None
