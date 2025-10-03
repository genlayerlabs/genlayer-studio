# rpc/transaction_utils.py

import rlp
from rlp.sedes import text, binary
from rlp.exceptions import DeserializationError, SerializationError
from eth_account import Account
from eth_account._utils.legacy_transactions import Transaction
import eth_utils
from eth_utils import to_checksum_address
from hexbytes import HexBytes
import os
from backend.rollup.consensus_service import ConsensusService
from backend.domain.types import TransactionType

from backend.protocol_rpc.types import (
    DecodedDeploymentData,
    DecodedMethodCallData,
    DecodedMethodSendData,
    DecodedRollupTransaction,
    DecodedRollupTransactionData,
    DecodedRollupTransactionDataArgs,
    DecodedGenlayerTransaction,
    DecodedGenlayerTransactionData,
    DecodedsubmitAppealDataArgs,
    ZERO_ADDRESS,
)


class Boolean:
    """A sedes for booleans
    Copied from rlp/sedes/boolean.py
    Adding custom logic to also handle `False` as `0x00`, since the Frontend library sends `False` as `0x00`
    """

    def serialize(self, obj):
        if not isinstance(obj, bool):
            raise SerializationError("Can only serialize integers", obj)

        if obj is False:
            return b""
        elif obj is True:
            return b"\x01"
        else:
            raise Exception("Invariant: no other options for boolean values")

    def deserialize(self, serial):
        if serial == b"":
            return False
        elif serial == b"\x01":
            return True
        elif serial == b"\x00":  # Custom logic to handle `False` as `0x00`
            return False
        else:
            raise DeserializationError(
                "Invalid serialized boolean.  Must be either 0x01 or 0x00", serial
            )


boolean = Boolean()


class TransactionParser:
    def __init__(self, consensus_service: ConsensusService):
        self.consensus_service = consensus_service
        self.web3 = consensus_service.web3

    def decode_signed_transaction(
        self, raw_transaction: str
    ) -> DecodedRollupTransaction | None:
        try:
            transaction_bytes = HexBytes(raw_transaction)
            # Try decoding typed transactions first (supports EIP-2718/EIP-1559)
            signed_transaction_as_dict = None
            typed_decode_err = None
            try:
                if len(transaction_bytes) > 0 and transaction_bytes[0] in (1, 2):
                    tx_type = transaction_bytes[0]
                    decoded_items = rlp.decode(bytes(transaction_bytes[1:]))
                    # EIP-2930 (0x01) and EIP-1559 (0x02) share first positions for common fields
                    # Index mapping for 0x02 dynamic fee:
                    # 0 chainId, 1 nonce, 2 maxPriorityFeePerGas, 3 maxFeePerGas, 4 gas,
                    # 5 to, 6 value, 7 data, 8 accessList, 9 v, 10 r, 11 s

                    def _to_int(value: bytes) -> int:
                        return int.from_bytes(value, byteorder="big") if value else 0

                    chain_id = _to_int(decoded_items[0])
                    nonce = _to_int(decoded_items[1])
                    gas = _to_int(decoded_items[4])
                    to_field = decoded_items[5] if decoded_items[5] else None
                    value = _to_int(decoded_items[6])
                    data_field = decoded_items[7] if decoded_items[7] else b""

                    signed_transaction_as_dict = {
                        "type": tx_type,
                        "chainId": chain_id,
                        "nonce": nonce,
                        "gas": gas,
                        "to": to_field,
                        "value": value,
                        "data": data_field,
                    }
                else:
                    raise ValueError("Not a typed transaction")
            except Exception as e:
                typed_decode_err = e
                # Fallback to legacy transaction decoding
                try:
                    signed_transaction = Transaction.from_bytes(transaction_bytes)
                    signed_transaction_as_dict = signed_transaction.as_dict()
                except Exception as legacy_err:
                    print(
                        "Error decoding transaction (typed and legacy failed)",
                        typed_decode_err,
                        legacy_err,
                    )
                    raise

            # extracting sender address
            sender = Account.recover_transaction(raw_transaction)

            # Normalize `to` field which may be bytes/HexBytes or string depending on decoder
            to_raw = signed_transaction_as_dict.get("to")
            if to_raw is None:
                to_address = None
            elif isinstance(to_raw, (bytes, bytearray, HexBytes)):
                to_address = to_checksum_address(f"0x{HexBytes(to_raw).hex()}")
            elif isinstance(to_raw, str) and len(to_raw) > 0:
                to_address = to_checksum_address(to_raw)
            else:
                to_address = None
            nonce = signed_transaction_as_dict["nonce"]
            value = signed_transaction_as_dict["value"]
            # Some decoders return `data`, others return `input`
            input_raw = (
                signed_transaction_as_dict.get("data")
                if signed_transaction_as_dict.get("data") is not None
                else signed_transaction_as_dict.get("input")
            )
            if input_raw is None:
                data = None
            elif isinstance(input_raw, (bytes, bytearray, HexBytes)):
                data = HexBytes(input_raw).hex()
            elif isinstance(input_raw, str):
                data = input_raw
            else:
                data = None
            decoded_data = None
            contract_abi = self._get_contract_abi()
            if data and contract_abi:
                # Remove '0x' prefix if present
                data = data.removeprefix("0x")
                # The first 4 bytes (8 hex characters) are the function selector
                function_selector = data[:8]
                # The rest is the encoded parameters
                parameters = data[8:]

                # Find matching function in ABI
                for abi_entry in contract_abi:
                    if abi_entry["type"] == "function":
                        # Calculate function selector from ABI
                        function_signature = f"{abi_entry['name']}({','.join([input['type'] for input in abi_entry['inputs']])})"
                        calculated_selector = self.web3.keccak(text=function_signature)[
                            :4
                        ].hex()

                        if calculated_selector == function_selector:
                            # Decode parameters using the input types from ABI
                            input_types = [
                                input["type"] for input in abi_entry["inputs"]
                            ]
                            decoded_params = self.web3.codec.decode(
                                input_types, bytes.fromhex(parameters)
                            )
                            # Create a dictionary mapping parameter names to values
                            decoded_data = {
                                "function": abi_entry["name"],
                                "params": dict(
                                    zip(
                                        [
                                            input["name"]
                                            for input in abi_entry["inputs"]
                                        ],
                                        decoded_params,
                                    )
                                ),
                            }
                            # Convert the decoded data into proper dataclasses
                            if decoded_data["function"] == "addTransaction":
                                params = decoded_data["params"]
                                decoded_data = DecodedRollupTransactionData(
                                    function_name=decoded_data["function"],
                                    args=DecodedRollupTransactionDataArgs(
                                        sender=to_checksum_address(params["_sender"]),
                                        recipient=to_checksum_address(
                                            params["_recipient"]
                                        ),
                                        num_of_initial_validators=params[
                                            "_numOfInitialValidators"
                                        ],
                                        max_rotations=params["_maxRotations"],
                                        data=params["_txData"],
                                    ),
                                )
                            elif decoded_data["function"] == "submitAppeal":
                                params = decoded_data["params"]
                                decoded_data = DecodedsubmitAppealDataArgs(
                                    tx_id=params["_txId"],
                                )

            return DecodedRollupTransaction(
                from_address=sender,
                to_address=to_address,
                data=decoded_data,
                type=signed_transaction_as_dict.get("type", 0),
                nonce=nonce,
                value=value,
            )

        except Exception as e:
            print("Error decoding transaction", e)
            return None

    def _get_genlayer_transaction_data(
        self,
        type: TransactionType,
        rollup_transaction_data_args: DecodedRollupTransactionDataArgs,
    ) -> str:
        try:
            data_bytes = HexBytes(rollup_transaction_data_args.data)

            if type == TransactionType.DEPLOY_CONTRACT:
                try:
                    return rlp.decode(data_bytes, DeploymentContractTransactionPayload)
                except rlp.exceptions.DeserializationError as e:
                    return rlp.decode(
                        data_bytes, DeploymentContractTransactionPayloadDefault
                    )
            elif type == TransactionType.RUN_CONTRACT:
                try:
                    return rlp.decode(data_bytes, MethodSendTransactionPayload)
                except rlp.exceptions.DeserializationError as e:
                    return rlp.decode(data_bytes, MethodSendTransactionPayloadDefault)
        except rlp.exceptions.DeserializationError as e:
            print("ERROR | both decoding attempts failed:", e)
            raise e

    def _get_genlayer_transaction_type(self, to_address: str) -> TransactionType:
        if to_address == ZERO_ADDRESS:
            return TransactionType.DEPLOY_CONTRACT
        return TransactionType.RUN_CONTRACT

    def get_genlayer_transaction(
        self, rollup_transaction: DecodedRollupTransaction
    ) -> DecodedGenlayerTransaction:
        if rollup_transaction.data is None or rollup_transaction.data.args is None:
            return DecodedGenlayerTransaction(
                type=TransactionType.SEND,
                from_address=rollup_transaction.from_address,
                to_address=rollup_transaction.to_address,
                data=None,
                max_rotations=int(os.getenv("VITE_MAX_ROTATIONS", 3)),
                num_of_initial_validators=None,
            )

        sender = rollup_transaction.data.args.sender
        recipient = rollup_transaction.data.args.recipient
        max_rotations = rollup_transaction.data.args.max_rotations
        type = self._get_genlayer_transaction_type(recipient)
        data = self._get_genlayer_transaction_data(type, rollup_transaction.data.args)
        num_of_initial_validators = (
            rollup_transaction.data.args.num_of_initial_validators
        )

        return DecodedGenlayerTransaction(
            from_address=sender,
            to_address=recipient,
            type=type,
            max_rotations=max_rotations,
            num_of_initial_validators=num_of_initial_validators,
            data=DecodedGenlayerTransactionData(
                contract_code=(
                    data.contract_code if hasattr(data, "contract_code") else None
                ),
                calldata=data.calldata,
                leader_only=(
                    data.leader_only if hasattr(data, "leader_only") else False
                ),
            ),
        )

    def transaction_has_valid_signature(
        self, raw_transaction: str, decoded_tx: DecodedRollupTransaction
    ) -> bool:
        recovered_address = Account.recover_transaction(raw_transaction)
        return recovered_address == decoded_tx.from_address

    def decode_method_send_data(self, data: str) -> DecodedMethodSendData:
        data_bytes = HexBytes(data)

        try:
            data_decoded = rlp.decode(data_bytes, MethodSendTransactionPayload)
        except rlp.exceptions.DeserializationError as e:
            print("WARN | falling back to default decode method call data:", e)
            data_decoded = rlp.decode(data_bytes, MethodSendTransactionPayloadDefault)

        leader_only = getattr(data_decoded, "leader_only", False)

        return DecodedMethodSendData(
            calldata=data_decoded["calldata"],
            leader_only=leader_only,
        )

    def decode_method_call_data(self, data: str) -> DecodedMethodCallData:
        raw_bytes = eth_utils.hexadecimal.decode_hex(data)

        # Remove the null byte
        if raw_bytes[-1] == 0:
            raw_bytes = raw_bytes[:-1]

            # Try to decode the outer list first
            if raw_bytes[0] >= 0xF8:  # Long list
                raw_bytes = raw_bytes[2:]  # Skip list prefix and length
            elif raw_bytes[0] >= 0xC0:  # Short list
                raw_bytes = raw_bytes[1:]  # Skip list prefix

            # Now try to decode the inner string
            raw_bytes = rlp.decode(raw_bytes)

        return DecodedMethodCallData(raw_bytes)

    def decode_deployment_data(self, data: str) -> DecodedDeploymentData:
        data_bytes = HexBytes(data)

        try:
            data_decoded = rlp.decode(data_bytes, DeploymentContractTransactionPayload)
        except rlp.exceptions.DeserializationError as e:
            print("Error decoding deployment data, falling back to default:", e)
            data_decoded = rlp.decode(
                data_bytes, DeploymentContractTransactionPayloadDefault
            )

        leader_only = getattr(data_decoded, "leader_only", False)

        return DecodedDeploymentData(
            contract_code=data_decoded["contract_code"],
            calldata=data_decoded["calldata"],
            leader_only=leader_only,
        )

    def _hash_of_signed_transaction(self, signed_transaction) -> bytes:
        # Helper method to get transaction hash
        return signed_transaction.hash()

    def _vrs_from(self, signed_transaction) -> tuple:
        # Helper method to extract v, r, s values
        return (signed_transaction.v, signed_transaction.r, signed_transaction.s)

    def _get_contract_abi(self) -> list:
        # Get contract ABI from consensus service
        contract_data = self.consensus_service.load_contract("ConsensusMain")
        return contract_data["abi"] if contract_data else []


class DeploymentContractTransactionPayload(rlp.Serializable):
    fields = [
        ("contract_code", binary),
        ("calldata", binary),
        ("leader_only", boolean),
    ]


class DeploymentContractTransactionPayloadDefault(rlp.Serializable):
    fields = [
        ("contract_code", binary),
        ("calldata", binary),
    ]


class MethodSendTransactionPayload(rlp.Serializable):
    fields = [
        ("calldata", binary),
        ("leader_only", boolean),
    ]


class MethodSendTransactionPayloadDefault(rlp.Serializable):
    fields = [
        ("calldata", binary),
    ]
