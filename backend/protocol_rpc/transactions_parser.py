# rpc/transaction_utils.py

import rlp
from rlp.sedes import binary, big_endian_int
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
    DecodedTopUpFeesDataArgs,
    ZERO_ADDRESS,
)

FEE_AWARE_ADD_TRANSACTION_ABI = {
    "inputs": [
        {
            "components": [
                {"internalType": "address", "name": "sender", "type": "address"},
                {"internalType": "address", "name": "recipient", "type": "address"},
                {
                    "internalType": "uint256",
                    "name": "numOfInitialValidators",
                    "type": "uint256",
                },
                {"internalType": "uint256", "name": "maxRotations", "type": "uint256"},
                {"internalType": "uint256", "name": "validUntil", "type": "uint256"},
                {"internalType": "uint256", "name": "saltNonce", "type": "uint256"},
                {"internalType": "uint256", "name": "userValue", "type": "uint256"},
                {
                    "components": [
                        {
                            "internalType": "uint256",
                            "name": "leaderTimeunitsAllocation",
                            "type": "uint256",
                        },
                        {
                            "internalType": "uint256",
                            "name": "validatorTimeunitsAllocation",
                            "type": "uint256",
                        },
                        {
                            "internalType": "uint256",
                            "name": "appealRounds",
                            "type": "uint256",
                        },
                        {
                            "internalType": "uint256",
                            "name": "executionBudgetPerRound",
                            "type": "uint256",
                        },
                        {
                            "internalType": "uint256",
                            "name": "executionConsumed",
                            "type": "uint256",
                        },
                        {
                            "internalType": "uint256",
                            "name": "totalMessageFees",
                            "type": "uint256",
                        },
                        {
                            "internalType": "uint256[]",
                            "name": "rotations",
                            "type": "uint256[]",
                        },
                        {
                            "internalType": "uint256",
                            "name": "maxPriceGenPerTimeUnit",
                            "type": "uint256",
                        },
                        {
                            "internalType": "uint256",
                            "name": "storageFeeMaxGasPrice",
                            "type": "uint256",
                        },
                        {
                            "internalType": "uint256",
                            "name": "receiptFeeMaxGasPrice",
                            "type": "uint256",
                        },
                    ],
                    "internalType": "struct IFeeManager.FeesDistribution",
                    "name": "feesDistribution",
                    "type": "tuple",
                },
                {"internalType": "bytes", "name": "txCalldata", "type": "bytes"},
                {
                    "components": [
                        {
                            "internalType": "enum IMessages.MessageType",
                            "name": "messageType",
                            "type": "uint8",
                        },
                        {
                            "internalType": "bool",
                            "name": "onAcceptance",
                            "type": "bool",
                        },
                        {
                            "internalType": "uint256",
                            "name": "parentIndex",
                            "type": "uint256",
                        },
                        {
                            "internalType": "address",
                            "name": "recipient",
                            "type": "address",
                        },
                        {
                            "internalType": "bytes32",
                            "name": "callKey",
                            "type": "bytes32",
                        },
                        {
                            "internalType": "uint256",
                            "name": "budget",
                            "type": "uint256",
                        },
                        {"internalType": "bytes", "name": "feeParams", "type": "bytes"},
                    ],
                    "internalType": "struct IMessages.MessageFeeAllocationNode[]",
                    "name": "messageAllocations",
                    "type": "tuple[]",
                },
            ],
            "internalType": "struct IConsensusMainWithFees.AddTransactionParams",
            "name": "_params",
            "type": "tuple",
        }
    ],
    "name": "addTransaction",
    "outputs": [],
    "stateMutability": "payable",
    "type": "function",
}

FEE_AWARE_DEPLOY_SALTED_ABI = {
    **FEE_AWARE_ADD_TRANSACTION_ABI,
    "name": "deploySalted",
}

FEE_AWARE_TOP_UP_FEES_ABI = {
    "inputs": [
        {"internalType": "bytes32", "name": "_txId", "type": "bytes32"},
        FEE_AWARE_ADD_TRANSACTION_ABI["inputs"][0]["components"][7]
        | {"name": "_feesDistribution"},
    ],
    "name": "topUpFees",
    "outputs": [],
    "stateMutability": "payable",
    "type": "function",
}

FEE_AWARE_TOP_UP_AND_SUBMIT_APPEAL_ABI = {
    **FEE_AWARE_TOP_UP_FEES_ABI,
    "name": "topUpAndSubmitAppeal",
}

FEES_DISTRIBUTION_FIELDS = [
    "leaderTimeunitsAllocation",
    "validatorTimeunitsAllocation",
    "appealRounds",
    "executionBudgetPerRound",
    "executionConsumed",
    "totalMessageFees",
    "rotations",
    "maxPriceGenPerTimeUnit",
    "storageFeeMaxGasPrice",
    "receiptFeeMaxGasPrice",
]

ADD_TRANSACTION_PARAMS_FIELDS = [
    "sender",
    "recipient",
    "numOfInitialValidators",
    "maxRotations",
    "validUntil",
    "saltNonce",
    "userValue",
    "feesDistribution",
    "txCalldata",
    "messageAllocations",
]


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

# Execution mode mapping: integer <-> string
# 0=NORMAL, 1=LEADER_ONLY, 2=LEADER_SELF_VALIDATOR
EXECUTION_MODE_INT_TO_STR = {
    0: "NORMAL",
    1: "LEADER_ONLY",
    2: "LEADER_SELF_VALIDATOR",
}

EXECUTION_MODE_STR_TO_INT = {v: k for k, v in EXECUTION_MODE_INT_TO_STR.items()}


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
                    # EIP-2930 (0x01) access list tx
                    # Fields: 0 chainId, 1 nonce, 2 gasPrice, 3 gas, 4 to, 5 value, 6 data, 7 accessList, 8 v, 9 r, 10 s
                    # EIP-1559 (0x02) dynamic fee tx
                    # Fields: 0 chainId, 1 nonce, 2 maxPriorityFeePerGas, 3 maxFeePerGas, 4 gas, 5 to, 6 value, 7 data, 8 accessList, 9 v, 10 r, 11 s

                    def _to_int(value: bytes) -> int:
                        return int.from_bytes(value, byteorder="big") if value else 0

                    chain_id = _to_int(decoded_items[0])
                    nonce = _to_int(decoded_items[1])

                    if tx_type == 2:
                        gas = _to_int(decoded_items[4])
                        to_field = decoded_items[5] if decoded_items[5] else None
                        value = _to_int(decoded_items[6])
                        data_field = decoded_items[7] if decoded_items[7] else b""
                    elif tx_type == 1:
                        gas = _to_int(decoded_items[3])
                        to_field = decoded_items[4] if decoded_items[4] else None
                        value = _to_int(decoded_items[5])
                        data_field = decoded_items[6] if decoded_items[6] else b""
                    else:
                        # Unknown typed transaction; fallback to legacy path below
                        raise ValueError("Unsupported typed transaction")

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
                # Treat empty bytes as burn (no recipient)
                if len(to_raw) == 0:
                    to_address = None
                else:
                    hex_to = HexBytes(to_raw).hex()
                    to_address = (
                        to_checksum_address(f"0x{hex_to}")
                        if len(hex_to) == 40
                        else None
                    )
            elif isinstance(to_raw, str):
                # Accept only full hex addresses; '0x' or empty means burn
                if to_raw.lower() == "0x" or len(to_raw) == 0:
                    to_address = None
                else:
                    to_address = to_checksum_address(to_raw)
            else:
                to_address = None
            nonce = signed_transaction_as_dict["nonce"]
            value = signed_transaction_as_dict["value"]
            submitted_value = int(value)
            fee_value = 0
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
                        function_signature = f"{abi_entry['name']}({','.join([self._canonical_abi_type(input) for input in abi_entry['inputs']])})"
                        calculated_selector = self.web3.keccak(text=function_signature)[
                            :4
                        ].hex()

                        if calculated_selector == function_selector:
                            # Decode parameters using the input types from ABI
                            input_types = [
                                self._canonical_abi_type(input)
                                for input in abi_entry["inputs"]
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
                            if decoded_data["function"] in {
                                "addTransaction",
                                "deploySalted",
                            }:
                                params = decoded_data["params"]
                                decoded_data, value, fee_value = (
                                    self._decode_add_transaction_data(
                                        decoded_data["function"], params, value
                                    )
                                )
                            elif decoded_data["function"] == "submitAppeal":
                                params = decoded_data["params"]
                                decoded_data = DecodedsubmitAppealDataArgs(
                                    tx_id=params["_txId"],
                                )
                            elif decoded_data["function"] == "topUpFees":
                                params = decoded_data["params"]
                                decoded_data = DecodedTopUpFeesDataArgs(
                                    tx_id=params["_txId"],
                                    fees_distribution=self._fees_distribution_to_dict(
                                        params["_feesDistribution"]
                                    ),
                                )
                                fee_value = int(value)
                                value = 0
                            elif decoded_data["function"] == "topUpAndSubmitAppeal":
                                params = decoded_data["params"]
                                decoded_data = DecodedsubmitAppealDataArgs(
                                    tx_id=params["_txId"],
                                    fees_distribution=self._fees_distribution_to_dict(
                                        params["_feesDistribution"]
                                    ),
                                    top_up_and_submit=True,
                                )
                                fee_value = int(value)
                                value = 0

            return DecodedRollupTransaction(
                from_address=sender,
                to_address=to_address,
                data=decoded_data,
                type=signed_transaction_as_dict.get("type", 0),
                nonce=nonce,
                value=value,
                fee_value=fee_value,
                submitted_value=submitted_value,
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
                # Try V1 format first (leader_only boolean) for backward compatibility
                try:
                    return rlp.decode(data_bytes, DeploymentContractTransactionPayload)
                except rlp.exceptions.DeserializationError:
                    pass

                # Try V2 format (execution_mode integer)
                try:
                    return rlp.decode(
                        data_bytes, DeploymentContractTransactionPayloadV2
                    )
                except rlp.exceptions.DeserializationError:
                    pass

                # Fallback to default format (no flag)
                return rlp.decode(
                    data_bytes, DeploymentContractTransactionPayloadDefault
                )

            elif type == TransactionType.RUN_CONTRACT:
                # Try V1 format first (leader_only boolean) for backward compatibility
                try:
                    return rlp.decode(data_bytes, MethodSendTransactionPayload)
                except rlp.exceptions.DeserializationError:
                    pass

                # Try V2 format (execution_mode integer)
                try:
                    return rlp.decode(data_bytes, MethodSendTransactionPayloadV2)
                except rlp.exceptions.DeserializationError:
                    pass

                # Fallback to default format (no flag)
                return rlp.decode(data_bytes, MethodSendTransactionPayloadDefault)
        except rlp.exceptions.DeserializationError as e:
            print("ERROR | all decoding attempts failed:", e)
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

        # Determine execution_mode from decoded data
        # V2 format has execution_mode as integer, V1 has leader_only boolean
        if hasattr(data, "execution_mode"):
            # V2 format: execution_mode is an integer
            execution_mode = EXECUTION_MODE_INT_TO_STR.get(
                data.execution_mode, "NORMAL"
            )
            # Compute leader_only for backward compatibility
            leader_only = execution_mode != "NORMAL"
        elif hasattr(data, "leader_only") and data.leader_only:
            # V1 format: leader_only boolean is True -> LEADER_ONLY (no validation)
            execution_mode = "LEADER_ONLY"
            leader_only = True
        else:
            # Default format or leader_only=False: NORMAL mode
            execution_mode = "NORMAL"
            leader_only = False

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
                leader_only=leader_only,
                execution_mode=execution_mode,
            ),
        )

    def transaction_has_valid_signature(
        self, raw_transaction: str, decoded_tx: DecodedRollupTransaction
    ) -> bool:
        recovered_address = Account.recover_transaction(raw_transaction)
        return recovered_address == decoded_tx.from_address

    def decode_method_send_data(self, data: str) -> DecodedMethodSendData:
        data_bytes = HexBytes(data)

        # Try V1 format first (leader_only boolean) for backward compatibility
        # V1 accepts True/False encoded as b'\x01'/b''
        try:
            data_decoded = rlp.decode(data_bytes, MethodSendTransactionPayload)
            leader_only = getattr(data_decoded, "leader_only", False)
            execution_mode = "LEADER_ONLY" if leader_only else "NORMAL"
            return DecodedMethodSendData(
                calldata=data_decoded["calldata"],
                leader_only=leader_only,
                execution_mode=execution_mode,
            )
        except rlp.exceptions.DeserializationError:
            pass

        # Try V2 format (execution_mode integer 0=NORMAL, 1=LEADER_ONLY, 2=LEADER_SELF_VALIDATOR)
        # This will only succeed for new clients sending values > 1 (e.g., 2 for LEADER_SELF_VALIDATOR)
        try:
            data_decoded = rlp.decode(data_bytes, MethodSendTransactionPayloadV2)
            execution_mode = EXECUTION_MODE_INT_TO_STR.get(
                data_decoded.execution_mode, "NORMAL"
            )
            leader_only = execution_mode != "NORMAL"
            return DecodedMethodSendData(
                calldata=data_decoded["calldata"],
                leader_only=leader_only,
                execution_mode=execution_mode,
            )
        except rlp.exceptions.DeserializationError:
            pass

        # Fallback to default format
        data_decoded = rlp.decode(data_bytes, MethodSendTransactionPayloadDefault)
        return DecodedMethodSendData(
            calldata=data_decoded["calldata"],
            leader_only=False,
            execution_mode="NORMAL",
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

        # Try V1 format first (leader_only boolean) for backward compatibility
        try:
            data_decoded = rlp.decode(data_bytes, DeploymentContractTransactionPayload)
            leader_only = getattr(data_decoded, "leader_only", False)
            execution_mode = "LEADER_ONLY" if leader_only else "NORMAL"
            return DecodedDeploymentData(
                contract_code=data_decoded["contract_code"],
                calldata=data_decoded["calldata"],
                leader_only=leader_only,
                execution_mode=execution_mode,
            )
        except rlp.exceptions.DeserializationError:
            pass

        # Try V2 format (execution_mode integer 0=NORMAL, 1=LEADER_ONLY, 2=LEADER_SELF_VALIDATOR)
        try:
            data_decoded = rlp.decode(
                data_bytes, DeploymentContractTransactionPayloadV2
            )
            execution_mode = EXECUTION_MODE_INT_TO_STR.get(
                data_decoded.execution_mode, "NORMAL"
            )
            leader_only = execution_mode != "NORMAL"
            return DecodedDeploymentData(
                contract_code=data_decoded["contract_code"],
                calldata=data_decoded["calldata"],
                leader_only=leader_only,
                execution_mode=execution_mode,
            )
        except rlp.exceptions.DeserializationError:
            pass

        # Fallback to default format
        data_decoded = rlp.decode(
            data_bytes, DeploymentContractTransactionPayloadDefault
        )
        return DecodedDeploymentData(
            contract_code=data_decoded["contract_code"],
            calldata=data_decoded["calldata"],
            leader_only=False,
            execution_mode="NORMAL",
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
        contract_abi = list(contract_data["abi"]) if contract_data else []
        contract_abi.extend(
            [
                FEE_AWARE_ADD_TRANSACTION_ABI,
                FEE_AWARE_DEPLOY_SALTED_ABI,
                FEE_AWARE_TOP_UP_FEES_ABI,
                FEE_AWARE_TOP_UP_AND_SUBMIT_APPEAL_ABI,
            ]
        )
        return contract_abi

    def _canonical_abi_type(self, abi_input: dict) -> str:
        input_type = abi_input["type"]
        if not input_type.startswith("tuple"):
            return input_type

        suffix = input_type[5:]
        component_types = ",".join(
            self._canonical_abi_type(component)
            for component in abi_input.get("components", [])
        )
        return f"({component_types}){suffix}"

    def _decode_add_transaction_data(
        self, function_name: str, params: dict, msg_value: int
    ) -> tuple[DecodedRollupTransactionData, int, int]:
        if "_params" in params:
            add_params = dict(zip(ADD_TRANSACTION_PARAMS_FIELDS, params["_params"]))
            user_value = int(add_params["userValue"])
            fee_value = max(0, int(msg_value) - user_value)
            return (
                DecodedRollupTransactionData(
                    function_name=function_name,
                    args=DecodedRollupTransactionDataArgs(
                        sender=to_checksum_address(add_params["sender"]),
                        recipient=to_checksum_address(add_params["recipient"]),
                        num_of_initial_validators=int(
                            add_params["numOfInitialValidators"]
                        ),
                        max_rotations=int(add_params["maxRotations"]),
                        data=add_params["txCalldata"],
                        valid_until=int(add_params["validUntil"]),
                        salt_nonce=int(add_params["saltNonce"]),
                        user_value=user_value,
                        fees_distribution=self._fees_distribution_to_dict(
                            add_params["feesDistribution"]
                        ),
                        message_allocations=[
                            self._message_allocation_to_dict(allocation)
                            for allocation in add_params["messageAllocations"]
                        ],
                        message_allocations_count=len(add_params["messageAllocations"]),
                    ),
                ),
                user_value,
                fee_value,
            )

        return (
            DecodedRollupTransactionData(
                function_name=function_name,
                args=DecodedRollupTransactionDataArgs(
                    sender=to_checksum_address(params["_sender"]),
                    recipient=to_checksum_address(params["_recipient"]),
                    num_of_initial_validators=int(params["_numOfInitialValidators"]),
                    max_rotations=int(params["_maxRotations"]),
                    data=params["_txData"],
                ),
            ),
            int(msg_value),
            0,
        )

    def _fees_distribution_to_dict(self, fees_distribution: tuple) -> dict:
        result = dict(zip(FEES_DISTRIBUTION_FIELDS, fees_distribution))
        result["rotations"] = [int(rotation) for rotation in result["rotations"]]
        for key, value in result.items():
            if key != "rotations":
                result[key] = int(value)
        return result

    def _message_allocation_to_dict(self, message_allocation: tuple) -> dict:
        return {
            "messageType": int(message_allocation[0]),
            "onAcceptance": bool(message_allocation[1]),
            "parentIndex": int(message_allocation[2]),
            "recipient": to_checksum_address(message_allocation[3]),
            "callKey": eth_utils.to_hex(message_allocation[4]),
            "budget": int(message_allocation[5]),
            "feeParams": bytes(message_allocation[6]),
        }


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


# V2 payloads with execution_mode as integer (0=NORMAL, 1=LEADER_ONLY, 2=LEADER_SELF_VALIDATOR)
class DeploymentContractTransactionPayloadV2(rlp.Serializable):
    fields = [
        ("contract_code", binary),
        ("calldata", binary),
        ("execution_mode", big_endian_int),
    ]


class MethodSendTransactionPayloadV2(rlp.Serializable):
    fields = [
        ("calldata", binary),
        ("execution_mode", big_endian_int),
    ]
