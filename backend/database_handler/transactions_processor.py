# consensus/services/transactions_db_service.py
from datetime import datetime
from enum import Enum
import rlp
import re
import random
from sqlalchemy.orm import Session, defer, selectinload
from sqlalchemy import or_, desc, and_, func, JSON, type_coerce, text

from backend.node.types import Receipt, ExecutionResultStatus
from .models import EvmEnvelope, Transactions, TransactionStatus
from eth_utils import to_bytes, keccak, is_address, to_checksum_address
import json
import base64
import time
import os
import copy
from typing import Callable, Iterable
from backend.domain.types import TransactionType
from web3 import Web3
from backend.consensus.types import (
    ConsensusResult,
    ConsensusRound,
    consensus_result_type_code,
    consensus_vote_type_code,
)
from backend.consensus.history import (
    APPEAL_RECOVERY_SNAPSHOT_KEY,
    LEADER_APPEAL_REPLAY_CONTEXT,
    VALIDATOR_APPEAL_CONTEXT,
    completed_consensus_round_index,
    completed_consensus_rounds,
    materialize_decision_metadata,
    prepare_appeal_decision_basis,
    time_unit_consumption,
)
from backend.consensus.utils import determine_consensus_from_votes
from backend.protocol_rpc.fees import (
    FEE_ACCOUNTING_KEY,
    acceptance_dispatch_pending,
    apply_fee_top_up,
    normalize_fees_distribution,
    runtime_rotations_for_round,
)
from backend.database_handler.terminal_snapshot_pruner import (
    SnapshotArchiveReader,
    snapshot_archive_read_through_enabled,
)
from backend.rollup.web3_pool import Web3ConnectionPool

MAX_JSON_SAFE_INTEGER = (2**53) - 1


def _transaction_order_tuple(model=Transactions):
    """Durable Studio queue-slot authority."""

    return model.queue_order


def _transaction_order_value(transaction: Transactions):
    return int(transaction.queue_order)


# Canonical v0.6 ITransactions.TransactionStatus ordinals (0-13). Studio-only
# states map to their on-chain equivalents (ACTIVATED -> Proposing: activation
# transitions the on-chain tx into Proposing).
TRANSACTION_STATUS_CODES = {
    "UNINITIALIZED": 0,
    "PENDING": 1,
    "ACTIVATED": 2,
    "PROPOSING": 2,
    "COMMITTING": 3,
    "REVEALING": 4,
    "ACCEPTED": 5,
    "UNDETERMINED": 6,
    "FINALIZED": 7,
    "CANCELED": 8,
    "APPEAL_REVEALING": 9,
    "APPEAL_COMMITTING": 10,
    "VALIDATORS_TIMEOUT": 11,
    "LEADER_TIMEOUT": 12,
    "LEADER_REVEALING": 13,
}


class TransactionAddressFilter(Enum):
    ALL = "all"
    TO = "to"
    FROM = "from"


def get_validator_vote_hash(
    validator_address: str,
    vote_type: int,
    nonce: int,
    other_execution_fields_hash: bytes = bytes(32),
) -> str:
    """
    Generate a hash for validator vote data using Solidity keccak.

    Args:
        validator_address: Address of the validator
        vote_type: Canonical v0.6 ``ITransactions.VoteType`` ordinal.
        nonce: Transaction nonce

    Returns:
        str: Hex-encoded hash with 0x prefix
    """
    vote_hash_bytes = Web3.solidity_keccak(
        ["address", "uint8", "bytes32", "uint256"],
        [validator_address, vote_type, other_execution_fields_hash, nonce],
    )
    return Web3.to_hex(vote_hash_bytes)


def get_tx_execution_hash(
    leader_address: str,
    vote_type: int,
    nonce: int,
    messages_and_other_fields_hash: bytes = bytes(32),
) -> str:
    """
    Generate a hash for transaction execution data using Solidity keccak.

    Args:
        leader_address: Address of the consensus leader
        vote_type: Canonical v0.6 ``ITransactions.VoteType`` ordinal.
        nonce: Studio's deterministic compatibility salt for this transaction.
        messages_and_other_fields_hash: Canonical bytes32 preimage field when
            available. Studio currently has no Solidity reveal payload, so the
            compatibility view uses the zero sentinel consistently for leader
            and validator hashes.

    Returns:
        str: Hex-encoded hash with 0x prefix
    """
    tx_execution_hash_bytes = Web3.solidity_keccak(
        ["address", "uint8", "bytes32", "uint256"],
        [leader_address, vote_type, messages_and_other_fields_hash, nonce],
    )
    return Web3.to_hex(tx_execution_hash_bytes)


class TransactionsProcessor:
    def __init__(
        self,
        session: Session,
        snapshot_archive: SnapshotArchiveReader | None = None,
    ):
        self.session = session
        self.snapshot_archive = snapshot_archive
        if self.snapshot_archive is None and snapshot_archive_read_through_enabled():
            self.snapshot_archive = SnapshotArchiveReader.from_environment()

        # Use singleton Web3 connection pool
        # Only used for checksum normalisation here, which needs no rollup —
        # and the rollup bridge is optional (HARDHAT_URL may be empty).
        self.web3 = Web3ConnectionPool.get_for_utilities()

    @staticmethod
    def _select_receipt(receipts, index: int = 0) -> dict | None:
        if isinstance(receipts, dict):
            return receipts
        if isinstance(receipts, list) and 0 <= index < len(receipts):
            receipt = receipts[index]
            if isinstance(receipt, dict):
                return receipt
        return None

    @staticmethod
    def _json_safe_numbers(value):
        if isinstance(value, bool) or value is None or isinstance(value, str):
            return value
        if isinstance(value, int):
            return str(value) if abs(value) > MAX_JSON_SAFE_INTEGER else value
        if isinstance(value, list):
            return [TransactionsProcessor._json_safe_numbers(item) for item in value]
        if isinstance(value, dict):
            return {
                key: TransactionsProcessor._json_safe_numbers(item)
                for key, item in value.items()
            }
        return value

    @staticmethod
    def _parse_transaction_data(
        transaction_data: Transactions,
        *,
        include_contract_snapshot: bool = True,
    ) -> dict:
        fee_accounting = (
            transaction_data.data.get(FEE_ACCOUNTING_KEY)
            if isinstance(transaction_data.data, dict)
            else None
        )
        execution_result, execution_result_name = (
            TransactionsProcessor._execution_result_fields(
                transaction_data.consensus_data
            )
        )
        if transaction_data.consensus_data:
            leader_receipts = transaction_data.consensus_data.get("leader_receipt", [])
            if isinstance(leader_receipts, dict):
                result = leader_receipts.get("result", {})
            elif isinstance(leader_receipts, list) and len(leader_receipts) > 0:
                result = leader_receipts[0].get("result", {})
            else:
                result = {}
        else:
            result = transaction_data.consensus_data
        if isinstance(result, dict):
            result = result.get("raw", {})
        return {
            "hash": transaction_data.hash,
            "from_address": transaction_data.from_address,
            "to_address": transaction_data.to_address,
            "data": TransactionsProcessor._json_safe_numbers(transaction_data.data),
            # Numeric-columns contract (tests/db-sqlalchemy/test_numeric_types.py):
            # top-level "value" is a plain int. The blanket _json_safe_numbers
            # stringification (fee-accounting era) broke that contract for
            # values > 2^53; big-int JSON consumers should read the canonical
            # decimal-string fees object instead.
            "value": transaction_data.value,
            "type": transaction_data.type,
            "status": transaction_data.status.value,
            "txExecutionResult": execution_result,
            "txExecutionResultName": execution_result_name,
            "fees": TransactionsProcessor._canonical_fees(
                fee_accounting,
                consensus_history=transaction_data.consensus_history,
                consensus_data=transaction_data.consensus_data,
            ),
            "result": TransactionsProcessor._decode_base64_data(result),
            "consensus_data": TransactionsProcessor._json_safe_numbers(
                transaction_data.consensus_data
            ),
            "gaslimit": transaction_data.nonce,
            "nonce": transaction_data.nonce,
            "r": transaction_data.r,
            "s": transaction_data.s,
            "v": transaction_data.v,
            "created_at": transaction_data.created_at.isoformat(),
            "leader_only": transaction_data.leader_only,
            "execution_mode": transaction_data.execution_mode,
            "origin_address": transaction_data.origin_address,
            "triggered_by": transaction_data.triggered_by_hash,
            "triggered_on": transaction_data.triggered_on,
            "triggered_transactions": [
                transaction.hash
                for transaction in transaction_data.triggered_transactions
                # Consensus exposes only child consensus transactions through
                # getTriggeredTransactionIds. Studio keeps external SEND rows
                # as local execution effects, but they are not protocol child
                # transactions and must not leak into that shared surface.
                if transaction.type != TransactionType.SEND.value
            ],
            "appealed": transaction_data.appealed,
            "timestamp_awaiting_finalization": transaction_data.timestamp_awaiting_finalization,
            "appeal_failed": transaction_data.appeal_failed,
            "appeal_undetermined": transaction_data.appeal_undetermined,
            "consensus_history": transaction_data.consensus_history,
            "timestamp_appeal": transaction_data.timestamp_appeal,
            "appeal_processing_time": transaction_data.appeal_processing_time,
            "contract_snapshot": (
                transaction_data.contract_snapshot
                if include_contract_snapshot
                else None
            ),
            "config_rotation_rounds": transaction_data.config_rotation_rounds,
            "num_of_initial_validators": transaction_data.num_of_initial_validators,
            "last_vote_timestamp": transaction_data.last_vote_timestamp,
            "rotation_count": transaction_data.rotation_count,
            "appeal_leader_timeout": transaction_data.appeal_leader_timeout,
            "leader_timeout_validators": transaction_data.leader_timeout_validators,
            "appeal_validators_timeout": transaction_data.appeal_validators_timeout,
            "sim_config": transaction_data.sim_config,
            # Required for execute_transfer's idempotency guard. Missing this
            # field caused the guard to always read None/false, double-crediting
            # SEND txs created by sim_fundAccount.
            "value_credited": transaction_data.value_credited,
        }

    def _hydrate_archived_contract_snapshot(self, transaction_data: dict) -> None:
        if transaction_data.get("contract_snapshot") is not None:
            return
        if self.snapshot_archive is None:
            return
        tx_hash = transaction_data.get("hash")
        if not tx_hash:
            return

        snapshot = self.snapshot_archive.load_snapshot(self.session, tx_hash)
        if snapshot is not None:
            transaction_data["contract_snapshot"] = snapshot

    @staticmethod
    def _status_payload(status: str) -> dict:
        status_name = str(status)
        return {
            "status": status_name,
            "statusCode": TRANSACTION_STATUS_CODES.get(status_name.upper(), 0),
        }

    @staticmethod
    def _execution_result_fields(consensus_data: dict | None) -> tuple[int, str]:
        receipt = TransactionsProcessor._leader_receipt(consensus_data)
        if not isinstance(receipt, dict):
            return 0, "NOT_VOTED"
        genvm_result = receipt.get("genvm_result")
        if isinstance(genvm_result, dict) and str(
            genvm_result.get("error_code") or ""
        ) in {
            "CONSENSUS_LEADER_EXEC_TIMEOUT",
            "CONSENSUS_VALIDATOR_EXEC_TIMEOUT",
        }:
            return 3, "TIMEOUT"
        value = str(receipt.get("execution_result") or "").upper()
        if value == "SUCCESS":
            return 1, "FINISHED_WITH_RETURN"
        if value in {"ERROR", "FAILURE", "FINISHEDWITHERROR", "FINISHED_WITH_ERROR"}:
            return 2, "FINISHED_WITH_ERROR"
        return 0, "NOT_VOTED"

    @staticmethod
    def _result_type_code(result: ConsensusResult) -> int:
        """Map Studio's decision enum to v0.6 ITransactions.ResultType."""

        return consensus_result_type_code(result)

    @staticmethod
    def _rpc_raw_round(
        transaction_data: dict,
        completed_rounds: list[dict],
    ) -> int:
        """Return the raw round currently materialized by v0.6 Consensus."""

        completed_raw = (
            completed_consensus_round_index(transaction_data.get("consensus_history"))
            if completed_rounds
            else 0
        )
        status = str(transaction_data.get("status") or "").upper()
        executing = status in {
            "PENDING",
            "ACTIVATED",
            "PROPOSING",
            "COMMITTING",
            "REVEALING",
            "LEADER_REVEALING",
        }
        if not executing:
            return completed_raw

        if transaction_data.get("appeal_undetermined") or transaction_data.get(
            "appeal_leader_timeout"
        ):
            return completed_raw + 2
        if transaction_data.get("appealed") or transaction_data.get(
            "appeal_validators_timeout"
        ):
            return completed_raw + (1 if completed_raw % 2 == 0 else 2)

        if completed_rounds:
            last_outcome = str(completed_rounds[-1].get("consensus_round") or "")
            if last_outcome in {
                ConsensusRound.VALIDATOR_APPEAL_SUCCESSFUL.value,
                ConsensusRound.VALIDATOR_TIMEOUT_APPEAL_SUCCESSFUL.value,
            }:
                return completed_raw + 1
        return completed_raw

    @staticmethod
    def _appeal_bond_for_round(accounting: dict | None, raw_round: int) -> int:
        """Project Studio appeal custody onto Consensus RoundData.appealBond."""

        if not isinstance(accounting, dict):
            return 0
        entries = accounting.get("appeal_bonds")
        if not isinstance(entries, list):
            return 0
        for entry in reversed(entries):
            if not isinstance(entry, dict):
                continue
            source_round = int(entry.get("sourceRound", entry.get("round", 0)) or 0)
            status = str(entry.get("status") or "").upper()
            default_appeal_round = (
                source_round + (1 if source_round % 2 == 0 else 2)
                if status in {"ACCEPTED", "VALIDATORS_TIMEOUT"}
                else source_round + 2
            )
            appeal_round = int(
                entry.get("appealRound", default_appeal_round) or default_appeal_round
            )
            if status == "LEADER_TIMEOUT":
                bond_rounds = {appeal_round - 1}
            elif status == "UNDETERMINED":
                bond_rounds = {appeal_round - 1, appeal_round}
            else:
                bond_rounds = {appeal_round}
            if raw_round in bond_rounds:
                return int(entry.get("amount", entry.get("minimumRequired", 0)) or 0)
        return 0

    @staticmethod
    def _last_history_outcome(transaction_data: dict) -> str:
        rounds = completed_consensus_rounds(transaction_data.get("consensus_history"))
        return str(rounds[-1].get("consensus_round") or "") if rounds else ""

    @staticmethod
    def _is_timeout_outcome(outcome: str) -> bool:
        return outcome in {
            ConsensusRound.LEADER_TIMEOUT.value,
            ConsensusRound.VALIDATORS_TIMEOUT.value,
            ConsensusRound.LEADER_TIMEOUT_APPEAL_FAILED.value,
            ConsensusRound.VALIDATORS_TIMEOUT_APPEAL_FAILED.value,
        }

    @staticmethod
    def _leader_receipt(consensus_data: dict | None) -> dict | None:
        if not isinstance(consensus_data, dict):
            return None
        leader_receipts = consensus_data.get("leader_receipt")
        if isinstance(leader_receipts, dict):
            return leader_receipts
        if isinstance(leader_receipts, list) and len(leader_receipts) > 0:
            return leader_receipts[0]
        return None

    @staticmethod
    def _storage_fee_used(accounting: dict) -> int:
        report = accounting.get("execution_fee_report") or {}
        chargeable = (
            report.get("chargeableExecution") if isinstance(report, dict) else {}
        )
        if isinstance(chargeable, dict) and chargeable.get("storage") is not None:
            return int(chargeable.get("storage", 0) or 0)

        chargeable_buckets = accounting.get("execution_fee_consumed_buckets") or []
        if len(chargeable_buckets) > 1:
            return int(chargeable_buckets[1])

        # Legacy Studio receipts exposed storage as a distinct raw GenVM
        # bucket. Retain this final fallback for persisted pre-v0.123 data.
        genvm_buckets = report.get("genvmBuckets") if isinstance(report, dict) else {}
        if isinstance(genvm_buckets, dict):
            return int(genvm_buckets.get("storage", 0) or 0)
        consumed_buckets = accounting.get("genvm_fee_consumed_buckets") or []
        if len(consumed_buckets) > 1:
            return int(consumed_buckets[1])
        return 0

    @staticmethod
    def _policy_int(policy: dict, camel_key: str, snake_key: str) -> int:
        return int(policy.get(camel_key, policy.get(snake_key, 0)) or 0)

    @staticmethod
    def _locked_fee_policy(policy: dict | None) -> dict | None:
        if not isinstance(policy, dict):
            return None
        return {
            "genPerTimeUnit": str(
                TransactionsProcessor._policy_int(
                    policy, "genPerTimeUnit", "gen_per_time_unit"
                )
            ),
            "storageUnitPrice": str(
                TransactionsProcessor._policy_int(
                    policy, "storageUnitPrice", "storage_unit_price"
                )
            ),
            "receiptGasPrice": str(
                TransactionsProcessor._policy_int(
                    policy, "receiptGasPrice", "receipt_gas_price"
                )
            ),
        }

    @staticmethod
    def _fee_distribution_fields(fees: dict) -> dict:
        return {
            "leaderTimeunitsAllocation": str(fees["leaderTimeunitsAllocation"]),
            "validatorTimeunitsAllocation": str(fees["validatorTimeunitsAllocation"]),
            "appealRounds": str(fees["appealRounds"]),
            "executionBudgetPerRound": str(fees["executionBudgetPerRound"]),
            "totalMessageFees": str(fees["totalMessageFees"]),
            "rotations": [str(rotation) for rotation in fees["rotations"]],
            "maxPriceGenPerTimeUnit": str(fees["maxPriceGenPerTimeUnit"]),
            "storageFeeMaxGasPrice": str(fees["storageFeeMaxGasPrice"]),
            "receiptFeeMaxGasPrice": str(fees["receiptFeeMaxGasPrice"]),
        }

    @staticmethod
    def _time_unit_rounds(tu: dict) -> list[dict]:
        return [
            {
                "round": entry["round"],
                "consensusRound": entry["consensus_round"],
                "leaderTimeunits": str(entry["leader_timeunits"]),
                "validatorTimeunits": str(entry["validator_timeunits"]),
                "maxValidatorTimeunits": str(entry["max_validator_timeunits"]),
            }
            for entry in tu["per_round"]
        ]

    @staticmethod
    def _canonical_fees(
        accounting: dict | None,
        consensus_history: dict | None = None,
        consensus_data: dict | None = None,
    ) -> dict | None:
        if not isinstance(accounting, dict):
            return None
        fees = normalize_fees_distribution(accounting.get("fees_distribution") or {})
        tu = time_unit_consumption(consensus_history, consensus_data)
        policy = accounting.get("policy_snapshot")

        return {
            "deposit": str(int(accounting.get("paid_fee_value", 0) or 0)),
            "userValue": str(int(accounting.get("user_value", 0) or 0)),
            "distribution": TransactionsProcessor._fee_distribution_fields(fees),
            "locked": TransactionsProcessor._locked_fee_policy(policy),
            "consumed": {
                "executionConsumed": str(
                    int(accounting.get("execution_fee_consumed", 0) or 0)
                ),
                "storageFeeUsed": str(
                    TransactionsProcessor._storage_fee_used(accounting)
                ),
                "messageFeesConsumed": str(
                    int(accounting.get("message_fee_consumed", 0) or 0)
                ),
                "messageFeesBudgetTotal": str(
                    int(accounting.get("message_fee_budget", 0) or 0)
                ),
                # Protocol unit matches distribution.leaderTimeunitsAllocation and
                # validatorTimeunitsAllocation: 1 TU = 1s GenVM runtime. Values
                # are derived from measured per-execution wall time in ms,
                # rounded UP per execution. validatorTimeunitsUsed is the SUM
                # across validator-mode executions; compare per-validator
                # allocations against maxValidatorTimeunits. This is the
                # reference shape mirrored by nodes.
                "leaderTimeunitsUsed": str(tu["leader_timeunits_used"]),
                "validatorTimeunitsUsed": str(tu["validator_timeunits_used"]),
                "perRound": TransactionsProcessor._time_unit_rounds(tu),
            },
        }

    @staticmethod
    def _transaction_data_to_str(data: dict) -> str:
        """
        NOTE: json doesn't support bytes object, so they need to be encoded somehow
            Common approaches can be: array, hex string, base64 string
            Array takes a lot of space (extra comma for each element)
            Hex is double in size
            Base64 is 1.33 in size
            So base64 is chosen
        """

        def data_encode(d):
            if isinstance(d, bytes):
                return str(base64.b64encode(d), encoding="ascii")
            raise TypeError("Can't encode #{d}")

        return json.dumps(data, default=data_encode)

    @staticmethod
    def _decode_base64_data(data: dict | str) -> dict | str:
        def decode_value(value):
            """Helper function to decode Base64-encoded values if they are strings."""
            if (
                isinstance(value, str)
                and value
                and bool(re.compile(r"^[A-Za-z0-9+/]*={0,2}$").fullmatch(value)) is True
            ):
                try:
                    decoded_str = base64.b64decode(
                        bytes(value, encoding="utf-8")
                    ).decode("utf-8", errors="ignore")
                    byte_content = re.sub(r"^[\x00-\x1f]+", "", decoded_str)
                    return byte_content
                except (ValueError, UnicodeDecodeError):
                    return value  # Return original if decoding fails

            return value  # Return unchanged for non-strings

        if isinstance(data, dict):
            data = {k: decode_value(v) for k, v in data.items()}
            return data
        elif isinstance(data, str):
            data = decode_value(data)
            return data
        elif data is None:
            return None
        else:
            raise TypeError(f"Can't decode unsupported type: {type(data).__name__}")

    @staticmethod
    def _generate_transaction_hash(
        from_address: str,
        to_address: str,
        data: dict,
        value: float,
        type: int,
        nonce: int,
    ) -> str:
        """Generate a fallback transaction hash similar to ConsensusMain._generateTx."""

        # Prepare recipient bytes as the solidity address encoding (20 bytes)
        recipient_bytes = (
            to_bytes(hexstr=to_address) if is_address(to_address) else b"\x00" * 20
        )

        # Use current timestamp with microsecond precision to ensure uniqueness
        timestamp = time.time()
        timestamp_int = int(timestamp * 1_000_000)  # Convert to microseconds as integer
        timestamp_bytes = timestamp_int.to_bytes(32, byteorder="big", signed=False)

        # Derive a deterministic pseudo-random seed from the recipient address
        seed_source = f"{to_address or '0x0'}:{timestamp}"
        rng = random.Random(seed_source)
        random_hex = "".join(rng.choice("0123456789abcdef") for _ in range(64))
        random_seed_bytes = bytes.fromhex(random_hex)

        tx_hash = (
            "0x" + keccak(recipient_bytes + timestamp_bytes + random_seed_bytes).hex()
        )
        return tx_hash

    def insert_transaction(
        self,
        from_address: str,
        to_address: str,
        data: dict,
        value: float,
        type: int,
        nonce: int,
        leader_only: bool,
        config_rotation_rounds: int,
        triggered_by_hash: (
            str | None
        ) = None,  # If filled, the transaction must be present in the database (committed)
        transaction_hash: str | None = None,
        num_of_initial_validators: int | None = None,
        sim_config: dict | None = None,
        triggered_on: str | None = None,  # "accepted" or "finalized"
        execution_mode: str = "NORMAL",  # "NORMAL", "LEADER_ONLY", or "LEADER_SELF_VALIDATOR"
        origin_address: str | None = None,
        commit: bool = True,
    ) -> str:

        if transaction_hash is None:
            current_nonce = self.get_genlayer_transaction_count(from_address)
            transaction_hash = self._generate_transaction_hash(
                from_address, to_address, data, value, type, current_nonce
            )

        # Check if transaction with this hash already exists to avoid UniqueViolation
        # This can happen due to race conditions or duplicate submissions
        existing_transaction = (
            self.session.query(Transactions).filter_by(hash=transaction_hash).first()
        )
        if existing_transaction is not None:
            return transaction_hash

        new_transaction = Transactions(
            hash=transaction_hash,
            from_address=from_address,
            to_address=to_address,
            data=json.loads(self._transaction_data_to_str(data)),
            value=value,
            type=type,
            status=TransactionStatus.PENDING,
            consensus_data=None,  # Will be set when the transaction is finalized
            nonce=nonce,
            # Future fields, unused for now
            gaslimit=None,
            input_data=None,
            r=None,
            s=None,
            v=None,
            leader_only=leader_only,
            origin_address=origin_address if origin_address else from_address,
            execution_mode=execution_mode,
            triggered_by=(
                self.session.query(Transactions).filter_by(hash=triggered_by_hash).one()
                if triggered_by_hash
                else None
            ),
            appealed=False,
            timestamp_awaiting_finalization=None,
            appeal_failed=0,
            appeal_undetermined=False,
            consensus_history={},
            timestamp_appeal=None,
            appeal_processing_time=0,
            contract_snapshot=None,
            config_rotation_rounds=config_rotation_rounds,
            num_of_initial_validators=num_of_initial_validators,
            last_vote_timestamp=None,
            rotation_count=0,
            appeal_leader_timeout=False,
            leader_timeout_validators=None,
            appeal_validators_timeout=False,
            sim_config=sim_config,
            triggered_on=triggered_on,
        )

        self.session.add(new_transaction)

        self.session.flush()  # So that `created_at` gets set
        if commit:
            self.session.commit()  # Persist the transaction to the database

        return transaction_hash

    def _process_round_data(self, transaction_data: dict) -> dict:
        """Process round data and prepare transaction data."""

        vote_nonce = (
            0
            if transaction_data.get("nonce") is None
            else int(transaction_data["nonce"])
        )

        completed_rounds = completed_consensus_rounds(
            transaction_data.get("consensus_history")
        )
        # Despite its historical name, ConsensusData.numOfRounds exposes the
        # current raw round index, not a cardinality (round 0 -> 0, round 3 -> 3).
        raw_round = self._rpc_raw_round(transaction_data, completed_rounds)
        transaction_data["num_of_rounds"] = str(raw_round)

        validator_votes_name = []
        validator_votes = []
        validator_votes_hash = []
        round_validators = []
        round_number = str(raw_round)
        last_round_outcome = ""
        completed_raw = (
            completed_consensus_round_index(transaction_data["consensus_history"])
            if completed_rounds
            else 0
        )
        if completed_rounds and raw_round == completed_raw:
            # Ignore trailing in-round UI events such as Leader Rotation. They
            # do not create a Solidity round and cannot be the lastRound view.
            last_round = completed_rounds[-1]
            last_round_outcome = str(last_round.get("consensus_round") or "")
            leader = self._select_receipt(last_round.get("leader_result"), index=1)
            if (
                leader is not None
                and leader.get("vote") is not None
                and isinstance(leader.get("node_config"), dict)
                and leader["node_config"].get("address") is not None
            ):
                validator_votes_name.append(leader["vote"].upper())
                vote_number = consensus_vote_type_code(
                    leader["vote"], leader.get("execution_result")
                )
                validator_votes.append(vote_number)
                leader_address = leader["node_config"]["address"]
                validator_votes_hash.append(
                    get_validator_vote_hash(leader_address, vote_number, vote_nonce)
                )
                round_validators.append(leader_address)

            for validator in last_round.get("validator_results") or []:
                validator_votes_name.append(validator["vote"].upper())
                vote_number = consensus_vote_type_code(
                    validator["vote"], validator.get("execution_result")
                )
                validator_votes.append(vote_number)
                validator_address = validator["node_config"]["address"]
                validator_votes_hash.append(
                    get_validator_vote_hash(validator_address, vote_number, vote_nonce)
                )
                round_validators.append(validator_address)
        # Handle upgrade transactions specially - they bypass consensus
        # and have upgrade_result instead of votes
        if self._is_timeout_outcome(last_round_outcome):
            last_round_result = self._result_type_code(ConsensusResult.TIMEOUT)
        elif (
            transaction_data.get("type") == TransactionType.UPGRADE_CONTRACT
            and transaction_data.get("consensus_data") is not None
            and "upgrade_result" in transaction_data["consensus_data"]
        ):
            if transaction_data["consensus_data"]["upgrade_result"] == "success":
                last_round_result = self._result_type_code(
                    ConsensusResult.MAJORITY_AGREE
                )
            else:
                last_round_result = self._result_type_code(
                    ConsensusResult.MAJORITY_DISAGREE
                )
        elif (
            # Handle LEADER_ONLY mode specially - no validators, so no votes to count
            # If the transaction is ACCEPTED or FINALIZED, the leader execution was successful
            transaction_data.get("execution_mode") == "LEADER_ONLY"
            and transaction_data.get("status")
            in [TransactionStatus.ACCEPTED.value, TransactionStatus.FINALIZED.value]
        ):
            last_round_result = self._result_type_code(ConsensusResult.MAJORITY_AGREE)
        else:
            last_round_result = self._result_type_code(
                determine_consensus_from_votes(
                    [vote.lower() for vote in validator_votes_name]
                )
            )

        fee_accounting = (transaction_data.get("data") or {}).get(FEE_ACCOUNTING_KEY)
        fees_distribution = (
            fee_accounting.get("fees_distribution")
            if isinstance(fee_accounting, dict)
            else None
        )
        if isinstance(fees_distribution, dict):
            rotation_limit = runtime_rotations_for_round(
                fees_distribution,
                transaction_data.get("config_rotation_rounds"),
                int(round_number),
            )
        else:
            rotation_limit = max(
                0, int(transaction_data.get("config_rotation_rounds") or 0)
            )

        transaction_data["last_round"] = {
            "round": round_number,
            "leader_index": "0",
            "votes_committed": str(len(validator_votes_name)),
            "votes_revealed": str(len(validator_votes_name)),
            "appeal_bond": str(self._appeal_bond_for_round(fee_accounting, raw_round)),
            "rotations_left": str(
                max(
                    0,
                    rotation_limit - int(transaction_data.get("rotation_count") or 0),
                )
            ),
            "result": last_round_result,
            "round_validators": round_validators,
            "validator_votes_hash": validator_votes_hash,
            "validator_votes": validator_votes,
            "validator_votes_name": validator_votes_name,
        }

        return transaction_data

    def _prepare_basic_transaction_data(self, transaction_data: dict) -> dict:
        """Prepare basic transaction data with common fields."""
        transaction_data["current_timestamp"] = str(round(time.time()))
        transaction_data["sender"] = transaction_data["from_address"]
        transaction_data["recipient"] = transaction_data["to_address"]
        transaction_data["created_timestamp"] = str(
            int(datetime.fromisoformat(transaction_data["created_at"]).timestamp())
        )
        transaction_data["last_vote_timestamp"] = str(
            transaction_data.get("last_vote_timestamp", 0)
        )
        transaction_data["random_seed"] = "0x" + "0" * 64
        transaction_data["tx_id"] = transaction_data["hash"]

        transaction_data["read_state_block_range"] = {
            "activation_block": "0",
            "processing_block": "0",
            "proposal_block": "0",
        }
        history_rounds = completed_consensus_rounds(
            transaction_data.get("consensus_history")
        )
        if history_rounds:
            first_round = history_rounds[0]
            leader = self._select_receipt(first_round.get("leader_result"), index=0)
            if (
                leader is not None
                and isinstance(leader.get("node_config"), dict)
                and leader["node_config"].get("address") is not None
            ):
                transaction_data["activator"] = leader["node_config"]["address"]
            else:
                transaction_data["activator"] = ""
        else:
            transaction_data["activator"] = ""

        if (transaction_data["consensus_data"] is not None) and (
            "leader_receipt" in transaction_data["consensus_data"]
        ):
            leader_receipt = self._select_receipt(
                transaction_data["consensus_data"]["leader_receipt"], index=0
            )
            if (
                leader_receipt is not None
                and isinstance(leader_receipt.get("node_config"), dict)
                and leader_receipt["node_config"].get("address") is not None
            ):
                transaction_data["last_leader"] = leader_receipt["node_config"][
                    "address"
                ]
            else:
                transaction_data["last_leader"] = ""
        else:
            transaction_data["last_leader"] = ""
        return transaction_data

    def _transaction_issued_slot(self, transaction: Transactions) -> int:
        """Return Consensus' recipient-scoped, zero-based issuance slot.

        ``Queues.enqueueNewPending`` assigns ``txSlot`` from the recipient's
        monotonically increasing ``issuedTxCount``. Studio's durable
        ``queue_order`` gives us the same serialization boundary. Local SEND
        rows are execution effects rather than Consensus transactions, so they
        must not advance the protocol slot.
        """

        if transaction.type == TransactionType.SEND.value:
            return 0
        if transaction.to_address is None:
            return 0
        return int(
            self.session.query(func.count(Transactions.hash))
            .filter(
                func.lower(Transactions.to_address)
                == str(transaction.to_address).lower(),
                Transactions.type != TransactionType.SEND.value,
                Transactions.queue_order < transaction.queue_order,
            )
            .scalar()
            or 0
        )

    def _encode_transaction_data(self, transaction_data: dict) -> dict:
        to_encode = []
        if transaction_data["data"] is not None:
            if "calldata" in transaction_data["data"]:
                encoded_call_data = base64.b64decode(
                    transaction_data["data"]["calldata"]
                )
                to_encode.append(encoded_call_data)
                to_encode.append(b"\x00")
            if "contract_code" in transaction_data["data"]:
                contract_code_bytes = base64.b64decode(
                    transaction_data["data"]["contract_code"]
                )
                to_encode.insert(0, contract_code_bytes)
        if len(to_encode) == 0:
            transaction_data["tx_data"] = ""
        else:
            transaction_data["tx_data"] = Web3.to_hex(rlp.encode(to_encode))[2:]
        return transaction_data

    def _process_execution_hash(self, transaction_data: dict) -> dict:
        leader_receipt = None
        if (
            transaction_data["consensus_data"] is not None
            and "leader_receipt" in transaction_data["consensus_data"]
        ):
            leader_receipt = self._select_receipt(
                transaction_data["consensus_data"]["leader_receipt"], index=0
            )

        if (
            leader_receipt is not None
            and isinstance(leader_receipt.get("node_config"), dict)
            and leader_receipt["node_config"].get("address") is not None
            and leader_receipt.get("vote") is not None
        ):
            vote_type = self._execution_result_fields(
                {"leader_receipt": leader_receipt}
            )[0]
            if vote_type == 0:
                vote_type = consensus_vote_type_code(
                    leader_receipt["vote"], leader_receipt.get("execution_result")
                )
            transaction_data["tx_execution_hash"] = get_tx_execution_hash(
                leader_receipt["node_config"]["address"],
                vote_type,
                int(transaction_data.get("nonce") or 0),
            )
        else:
            transaction_data["tx_execution_hash"] = ""

        return transaction_data

    def _process_messages(self, transaction_data: dict) -> dict:
        eq_output = []
        if (
            "consensus_history" in transaction_data
            and transaction_data["consensus_history"] is not None
            and "consensus_results" in transaction_data["consensus_history"]
        ):
            for consensus_round in transaction_data["consensus_history"][
                "consensus_results"
            ]:
                leader_result = self._select_receipt(
                    consensus_round.get("leader_result"), index=0
                )
                if (
                    leader_result is not None
                    and leader_result.get("result") is not None
                ):
                    eq_output.append(
                        [
                            len(eq_output),  # key
                            [
                                base64.b64decode(leader_result["result"])[0],  # kind
                                "\x00",
                            ],
                        ]
                    )  # data

        kind = 0
        leader_receipt = None
        if (
            transaction_data["consensus_data"] is not None
            and "leader_receipt" in transaction_data["consensus_data"]
        ):
            leader_receipt = self._select_receipt(
                transaction_data["consensus_data"]["leader_receipt"], index=0
            )
        if leader_receipt is not None and leader_receipt.get("result") is not None:
            kind = base64.b64decode(leader_receipt["result"])[0]

        pending_transactions = []
        messages = []
        pending_messages = (
            leader_receipt.get("pending_transactions")
            if leader_receipt is not None
            else None
        )
        if pending_messages is not None:
            for message in pending_messages:
                pending_transactions.append(
                    [
                        message.get("address", ""),  # Account
                        message.get("calldata", ""),  # Calldata
                        message.get("value", 0),  # Value
                        message.get("on", "finalized"),  # On
                        message.get("code", ""),  # Code
                        message.get("salt_nonce", 0),  # SaltNonce
                    ]
                )
                messages.append(
                    {
                        "messageType": "0",
                        "recipient": message.get("address", ""),
                        "value": message.get("value", 0),
                        "data": message.get("calldata", ""),
                        "onAcceptance": message.get("on", "finalized") == "accepted",
                    }
                )
        transaction_data["eq_blocks_outputs"] = Web3.to_hex(
            rlp.encode(
                [
                    [
                        [kind, "\x00"],  # data
                        pending_transactions,
                        [],  # pending eth transactions
                        bytes.fromhex(""),
                    ],  # storage proof
                    eq_output,
                ]
            )
        )
        transaction_data["messages"] = messages
        return transaction_data

    def _process_queue(self, transaction_data: dict) -> dict:
        status_to_queue_type = {
            TransactionStatus.PENDING.value: "1",
            TransactionStatus.ACTIVATED.value: "1",
            TransactionStatus.PROPOSING.value: "1",
            TransactionStatus.COMMITTING.value: "1",
            TransactionStatus.REVEALING.value: "1",
            "LEADER_REVEALING": "1",
            "APPEAL_COMMITTING": "1",
            "APPEAL_REVEALING": "1",
            TransactionStatus.ACCEPTED.value: "2",
            TransactionStatus.LEADER_TIMEOUT.value: "2",
            TransactionStatus.VALIDATORS_TIMEOUT.value: "2",
            TransactionStatus.UNDETERMINED.value: "3",
        }
        transaction_data["queue_type"] = status_to_queue_type.get(
            transaction_data["status"], "0"
        )
        transaction_data["queue_position"] = "0"

        return transaction_data

    def _process_result(self, transaction_data: dict) -> dict:
        if self._is_timeout_outcome(self._last_history_outcome(transaction_data)):
            consensus_result = ConsensusResult.TIMEOUT
            transaction_data["result"] = self._result_type_code(consensus_result)
            transaction_data["result_name"] = consensus_result.value
            return transaction_data

        # Handle upgrade transactions specially - they bypass consensus
        # and have upgrade_result instead of votes
        if (
            transaction_data.get("type") == TransactionType.UPGRADE_CONTRACT
            and transaction_data.get("consensus_data") is not None
            and "upgrade_result" in transaction_data["consensus_data"]
        ):
            if transaction_data["consensus_data"]["upgrade_result"] == "success":
                consensus_result = ConsensusResult.MAJORITY_AGREE
            else:
                consensus_result = ConsensusResult.MAJORITY_DISAGREE
            transaction_data["result"] = self._result_type_code(consensus_result)
            transaction_data["result_name"] = consensus_result.value
            return transaction_data

        # Handle LEADER_ONLY mode specially - no validators, so no votes to count
        # If the transaction is ACCEPTED or FINALIZED, the leader execution was successful
        if transaction_data.get(
            "execution_mode"
        ) == "LEADER_ONLY" and transaction_data.get("status") in [
            TransactionStatus.ACCEPTED.value,
            TransactionStatus.FINALIZED.value,
        ]:
            consensus_result = ConsensusResult.MAJORITY_AGREE
            transaction_data["result"] = self._result_type_code(consensus_result)
            transaction_data["result_name"] = consensus_result.value
            return transaction_data

        if (transaction_data["consensus_data"] is not None) and (
            "votes" in transaction_data["consensus_data"]
        ):
            votes_temp = list(transaction_data["consensus_data"]["votes"].values())
        else:
            votes_temp = []
        consensus_result = determine_consensus_from_votes(votes_temp)
        transaction_data["result"] = self._result_type_code(consensus_result)
        transaction_data["result_name"] = consensus_result.value
        return transaction_data

    def get_transaction_by_hash(
        self,
        transaction_hash: str,
        sim_config: dict | None = None,
        include_contract_snapshot: bool = True,
    ) -> dict | None:
        # Expire cached ORM objects to ensure we read fresh data after raw SQL writes
        self.session.expire_all()
        query = self.session.query(Transactions)
        if not include_contract_snapshot:
            query = query.options(defer(Transactions.contract_snapshot))
        transaction = query.filter_by(hash=transaction_hash).one_or_none()

        if transaction is None:
            return None

        transaction_data = self._parse_transaction_data(
            transaction, include_contract_snapshot=include_contract_snapshot
        )
        if include_contract_snapshot:
            self._hydrate_archived_contract_snapshot(transaction_data)
        else:
            transaction_data.pop("contract_snapshot", None)

        # Handle contract_state based on sim_config
        include_contract_state = sim_config and sim_config.get(
            "include_contract_state", False
        )

        # Remove contract_state from consensus_data by default (unless explicitly requested)
        if (
            transaction_data.get("consensus_data")
            and "leader_receipt" in transaction_data["consensus_data"]
        ):
            leader_receipt = transaction_data["consensus_data"]["leader_receipt"]

            if isinstance(leader_receipt, dict):
                if not include_contract_state and "contract_state" in leader_receipt:
                    del leader_receipt["contract_state"]

            elif isinstance(leader_receipt, list):
                for receipt in leader_receipt:
                    if isinstance(receipt, dict):
                        if not include_contract_state and "contract_state" in receipt:
                            del receipt["contract_state"]

        # Process for testnet
        transaction_data = self._prepare_basic_transaction_data(transaction_data)
        transaction_data["tx_slot"] = str(self._transaction_issued_slot(transaction))
        transaction_data = self._process_result(transaction_data)
        transaction_data = self._encode_transaction_data(transaction_data)
        transaction_data = self._process_execution_hash(transaction_data)
        transaction_data = self._process_messages(transaction_data)
        transaction_data = self._process_queue(transaction_data)
        transaction_data = self._process_round_data(transaction_data)
        return transaction_data

    def get_studio_transaction_by_hash(
        self, transaction_hash: str, full: bool
    ) -> dict | None:
        transaction = (
            self.session.query(Transactions)
            .filter_by(hash=transaction_hash)
            .one_or_none()
        )

        if transaction is None:
            return None

        transaction_data = self._parse_transaction_data(transaction)
        if full:
            self._hydrate_archived_contract_snapshot(transaction_data)

        # Transform studio fields to testnet fields
        transaction_data["tx_id"] = transaction_data.pop("hash", None)
        transaction_data["sender"] = transaction_data.pop("from_address", None)
        transaction_data["recipient"] = transaction_data.pop("to_address", None)
        transaction_data["initial_rotations"] = transaction_data.pop(
            "config_rotation_rounds", None
        )
        transaction_data["created_timestamp"] = str(
            int(
                datetime.fromisoformat(
                    transaction_data.pop("created_at", "0")
                ).timestamp()
            )
        )
        transaction_data["last_vote_timestamp"] = str(
            transaction_data.pop("last_vote_timestamp", 0)
        )

        if not full:
            # Remove validators info and encoded data
            for key in [
                "data",
                "consensus_data",
                "consensus_history",
                "contract_snapshot",
                "leader_timeout_validators",
                "sim_config",
            ]:
                transaction_data.pop(key, None)

        return transaction_data

    def get_activated_transactions_older_than(self, seconds: int) -> list[dict]:
        """
        Get ACTIVATED transactions that have been stuck for more than the specified seconds.

        Args:
            seconds: Number of seconds a transaction must be ACTIVATED to be considered stuck

        Returns:
            List of transaction data dictionaries for stuck transactions
        """
        from datetime import datetime, timedelta

        cutoff_time = datetime.now() - timedelta(seconds=seconds)
        stuck_transactions = (
            self.session.query(Transactions)
            .options(selectinload(Transactions.triggered_transactions))
            .filter(
                Transactions.status == TransactionStatus.ACTIVATED,
                Transactions.created_at < cutoff_time,
            )
            .order_by(Transactions.created_at)
            .all()
        )

        return [
            self._parse_transaction_data(transaction)
            for transaction in stuck_transactions
        ]

    @staticmethod
    def cancel_transaction_if_available(
        session: Session, transaction_hash: str
    ) -> bool:
        """
        Cancel a transaction only if it is still available to be cancelled.

        Returns:
            bool: True when the transaction was cancelled, False otherwise.
        """
        result = session.execute(
            text(
                """
                UPDATE transactions
                SET status = CAST('CANCELED' AS transaction_status)
                WHERE hash = :hash
                  AND status IN ('PENDING', 'ACTIVATED')
                  AND blocked_at IS NULL
                """
            ),
            {"hash": transaction_hash},
        )
        session.commit()
        return result.rowcount > 0

    def update_transaction_status(
        self,
        transaction_hash: str,
        new_status: TransactionStatus,
        update_current_status_changes: bool = True,
    ):
        if update_current_status_changes:
            # Use server-side JSONB update to avoid loading the full row (which
            # includes the massive contract_snapshot column).
            # Preserve original semantics: if current_status_changes is missing,
            # initialize with [PENDING, new_status]; otherwise append.
            result = self.session.execute(
                text(
                    """
                    UPDATE transactions
                    SET status = CAST(:new_status AS transaction_status),
                        consensus_history = jsonb_set(
                            CASE WHEN jsonb_typeof(consensus_history) = 'object'
                                 THEN consensus_history
                                 ELSE '{}'::jsonb
                            END,
                            '{current_status_changes}',
                            CASE
                                WHEN jsonb_typeof(consensus_history) = 'object'
                                     AND consensus_history->'current_status_changes' IS NOT NULL
                                THEN consensus_history->'current_status_changes' || to_jsonb(CAST(:status_val AS text))
                                ELSE jsonb_build_array(CAST(:pending_val AS text), CAST(:status_val AS text))
                            END
                        )
                    WHERE hash = :hash
                """
                ),
                {
                    "hash": transaction_hash,
                    "new_status": new_status.value,
                    "status_val": new_status.value,
                    "pending_val": TransactionStatus.PENDING.value,
                },
            )
        else:
            result = self.session.execute(
                text(
                    "UPDATE transactions SET status = CAST(:new_status AS transaction_status) WHERE hash = :hash"
                ),
                {"hash": transaction_hash, "new_status": new_status.value},
            )

        if result.rowcount == 0:
            print(
                f"[TRANSACTIONS_PROCESSOR]: Transaction {transaction_hash} not found, skipping status update"
            )
            return

        self.session.commit()

    def add_state_timestamp(self, transaction_hash: str, state_name: str):
        """
        Add a timestamp for when a consensus state is entered.

        Uses server-side JSONB update to avoid loading the full row
        (which includes the massive contract_snapshot column).

        Args:
            transaction_hash (str): Hash of the transaction.
            state_name (str): Name of the state (e.g., "PENDING", "PROPOSING").
        """
        result = self.session.execute(
            text(
                """
                UPDATE transactions
                SET consensus_history = jsonb_set(
                    jsonb_set(
                        CASE WHEN jsonb_typeof(consensus_history) = 'object'
                             THEN consensus_history
                             ELSE '{}'::jsonb
                        END,
                        '{current_monitoring}',
                        CASE WHEN jsonb_typeof(consensus_history) = 'object'
                                  AND consensus_history->'current_monitoring' IS NOT NULL
                             THEN consensus_history->'current_monitoring'
                             ELSE '{}'::jsonb
                        END
                    ),
                    ARRAY['current_monitoring', :state_name],
                    to_jsonb(CAST(:ts AS double precision))
                )
                WHERE hash = :hash
            """
            ),
            {"hash": transaction_hash, "state_name": state_name, "ts": time.time()},
        )

        if result.rowcount == 0:
            print(
                f"[TRANSACTIONS_PROCESSOR]: Transaction {transaction_hash} not found, skipping monitoring update"
            )
            return

        self.session.commit()

    def set_transaction_result(
        self, transaction_hash: str, consensus_data: dict | None
    ):
        result = self.session.execute(
            text(
                "UPDATE transactions SET consensus_data = CAST(:data AS jsonb) WHERE hash = :hash"
            ),
            {
                "hash": transaction_hash,
                "data": json.dumps(consensus_data) if consensus_data else None,
            },
        )

        if result.rowcount == 0:
            print(
                f"[TRANSACTIONS_PROCESSOR]: Transaction {transaction_hash} not found, skipping result update"
            )
            return

        self.session.commit()

    def update_transaction_data(self, transaction_hash: str, data: dict | None):
        result = self.session.execute(
            text(
                "UPDATE transactions SET data = CAST(:data AS jsonb) WHERE hash = :hash"
            ),
            {
                "hash": transaction_hash,
                "data": json.dumps(data) if data is not None else None,
            },
        )
        if result.rowcount == 0:
            print(
                f"[TRANSACTIONS_PROCESSOR]: Transaction {transaction_hash} not found, skipping data update"
            )
            return
        self.session.commit()

    def update_transaction_fee_accounting(
        self, transaction_hash: str, fee_accounting: dict
    ):
        transaction = (
            self.session.query(Transactions)
            .filter_by(hash=transaction_hash)
            .one_or_none()
        )
        if transaction is None:
            print(
                f"[TRANSACTIONS_PROCESSOR]: Transaction {transaction_hash} not found, skipping fee accounting update"
            )
            return
        data = dict(transaction.data or {})
        data["fee_accounting"] = fee_accounting
        self.update_transaction_data(transaction_hash, data)

    def mutate_transaction_fee_accounting(
        self,
        transaction_hash: str,
        mutator: Callable[[dict], dict],
        *,
        commit: bool = True,
    ) -> dict:
        """Mutate the latest fee accounting while holding the transaction row.

        Consensus workers run asynchronously, while RPC top-ups can arrive at
        any in-flight status. Every worker-side accounting transition must
        therefore derive from the post-lock JSON value; writing a snapshot
        captured before a concurrent top-up would erase paid budget.
        """

        row = (
            self.session.execute(
                text("SELECT data FROM transactions WHERE hash = :hash FOR UPDATE"),
                {"hash": transaction_hash},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ValueError("TransactionNotFound")
        data = dict(row["data"] or {})
        current = data.get(FEE_ACCOUNTING_KEY)
        if not isinstance(current, dict):
            raise ValueError("FeeAccountingMissing")
        updated = mutator(current)
        data[FEE_ACCOUNTING_KEY] = updated
        self.session.execute(
            text(
                "UPDATE transactions SET data = CAST(:data AS jsonb)"
                " WHERE hash = :hash"
            ),
            {"hash": transaction_hash, "data": json.dumps(data)},
        )
        if commit:
            self.session.commit()
        return updated

    def apply_transaction_fee_top_up(
        self,
        transaction_hash: str,
        *,
        fees_distribution: dict,
        amount: int,
        sender: str,
        policy,
    ) -> dict:
        """Apply one top-up to the latest accounting snapshot under a row lock.

        The caller commits only after debiting the sender on this same session.
        This gives Studio the serial execution semantics of FeeManager.topUpFees:
        concurrent top-ups compose instead of both charging against one stale
        read and allowing the last JSON write to erase the other contribution.
        """

        row = (
            self.session.execute(
                text(
                    "SELECT status, data, num_of_initial_validators"
                    " FROM transactions WHERE hash = :hash FOR UPDATE"
                ),
                {"hash": transaction_hash},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ValueError("TransactionNotFound")
        if row["status"] in {
            TransactionStatus.ACCEPTED.value,
            TransactionStatus.UNDETERMINED.value,
            TransactionStatus.LEADER_TIMEOUT.value,
            TransactionStatus.VALIDATORS_TIMEOUT.value,
            TransactionStatus.FINALIZED.value,
            TransactionStatus.CANCELED.value,
        }:
            raise ValueError("InvalidTransactionStatus")

        data = dict(row["data"] or {})
        current = data.get(FEE_ACCOUNTING_KEY)
        if current is None:
            raise ValueError("FeeAccountingMissing")
        updated = apply_fee_top_up(
            current,
            fees_distribution=fees_distribution,
            amount=amount,
            sender=sender,
            num_of_validators=int(row["num_of_initial_validators"] or 5),
            policy=policy,
        )
        data[FEE_ACCOUNTING_KEY] = updated
        self.session.execute(
            text(
                "UPDATE transactions SET data = CAST(:data AS jsonb)"
                " WHERE hash = :hash"
            ),
            {"hash": transaction_hash, "data": json.dumps(data)},
        )
        return updated

    def get_transaction_count(self, address: str) -> int:
        # Normalize address to checksum format
        try:
            checksum_address = self.web3.to_checksum_address(address)
        except:
            checksum_address = address

        if address is not None:
            ledger_address = str(checksum_address).lower()
            highest_nonce = (
                self.session.query(func.max(EvmEnvelope.nonce))
                .filter(EvmEnvelope.from_address == ledger_address)
                .scalar()
            )
            return 0 if highest_nonce is None else int(highest_nonce) + 1

        # Studio-only system transactions (funding) are not signed EVM
        # envelopes and retain their historical database-count nonce.
        return int(
            self.session.query(Transactions)
            .filter(Transactions.from_address.is_(None))
            .count()
        )

    def get_genlayer_transaction_count(self, address: str | None) -> int:
        """Count Studio transaction rows authored by one logical sender.

        This is deliberately separate from ``eth_getTransactionCount``. Ghost
        contracts do not submit signed EVM envelopes when MessagePayments
        creates children, but Studio still needs a stable monotonically
        increasing child metadata nonce and fallback-hash input.
        """

        if address is None:
            return int(
                self.session.query(Transactions)
                .filter(Transactions.from_address.is_(None))
                .count()
            )
        try:
            address = self.web3.to_checksum_address(address)
        except Exception:
            pass
        return int(
            self.session.query(Transactions)
            .filter(Transactions.from_address == address)
            .count()
        )

    def begin_evm_envelope(
        self,
        transaction_hash: str,
        from_address: str,
        nonce: int,
    ) -> str | None:
        """Lock one sender and validate exact EVM nonce/replay semantics.

        Returns the prior successful response for an identical raw envelope.
        The caller records a new success only after all protocol mutations have
        completed, in the same DB transaction.
        """

        from_address = to_checksum_address(from_address).lower()
        nonce = int(nonce)
        if nonce < 0:
            raise ValueError("InvalidNonce")

        bind = self.session.get_bind()
        if bind.dialect.name == "postgresql":
            self.session.execute(
                text(
                    "SELECT pg_advisory_xact_lock(" "hashtextextended(:sender_key, 0))"
                ),
                {"sender_key": f"evm-sender:{from_address.lower()}"},
            )

        existing = self.session.get(EvmEnvelope, transaction_hash)
        if existing is not None:
            return existing.result

        expected = self.get_transaction_count(from_address)
        if nonce < expected:
            raise ValueError(f"NonceTooLow(expected={expected},actual={nonce})")
        if nonce > expected:
            raise ValueError(f"NonceTooHigh(expected={expected},actual={nonce})")
        return None

    def record_evm_envelope(
        self,
        transaction_hash: str,
        from_address: str,
        nonce: int,
        result: str,
        *,
        to_address: str | None = None,
        success: bool = True,
        error: str | None = None,
    ) -> None:
        self.session.add(
            EvmEnvelope(
                hash=transaction_hash,
                from_address=to_checksum_address(from_address).lower(),
                nonce=int(nonce),
                result=result,
                to_address=(str(to_address).lower() if to_address else None),
                success=bool(success),
                error=(str(error)[:1024] if error else None),
            )
        )
        self.session.flush()

    def get_evm_envelope(self, transaction_hash: str) -> EvmEnvelope | None:
        return self.session.get(EvmEnvelope, transaction_hash)

    def lock_transaction_admission(self, transaction_hash: str) -> None:
        """Serialize one raw transaction's admission across RPC workers.

        The lock is held by the request transaction until insert_transaction
        commits. This keeps duplicate submissions from both authoring shadow
        helper state before either worker can observe the canonical DB row.
        """

        bind = self.session.get_bind()
        if bind.dialect.name != "postgresql":
            return
        self.session.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtextextended(:transaction_hash, 0))"
            ),
            {"transaction_hash": transaction_hash},
        )

    def lock_ghost_factory(self) -> None:
        """Serialize Studio's virtual GhostFactory for this DB transaction."""

        bind = self.session.get_bind()
        if bind.dialect.name != "postgresql":
            return
        self.session.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtextextended('studio-v0.6-ghost-factory', 0))"
            )
        )

    def lock_pending_recipients(self, addresses: Iterable[str]) -> None:
        """Serialize child admission for every touched recipient queue.

        One Consensus EVM transaction processes a message batch serially.  A
        Studio worker can process unrelated parents concurrently, so acquire
        the same recipient-scoped locks used by top-level RPC admission before
        reading queue depth.  Sorting the complete set keeps overlapping
        multi-recipient batches deadlock-free.
        """

        bind = self.session.get_bind()
        if bind.dialect.name != "postgresql":
            return
        for address in sorted(
            {
                str(address).lower()
                for address in addresses
                if isinstance(address, str) and address
            }
        ):
            self.session.execute(
                text("SELECT pg_advisory_xact_lock(" "hashtextextended(:lock_key, 0))"),
                {"lock_key": f"pending-recipient:{address}"},
            )

    def get_successful_ghost_creation_count(self) -> int:
        """Return the virtual factory nonce offset.

        GhostFactory creates and registers the proxy during transaction
        admission, before GenVM executes.  A later failed or canceled deploy
        therefore still consumed one successful CREATE/CREATE2 and remains a
        registered ghost, so every admitted deployment row is counted.
        """

        return int(
            self.session.query(Transactions)
            .filter(Transactions.type == TransactionType.DEPLOY_CONTRACT.value)
            .count()
        )

    def is_genvm_contract_address(self, address: str) -> bool:
        """Whether Consensus would have registered ``address`` as a ghost."""

        try:
            address = to_checksum_address(address)
        except (TypeError, ValueError):
            return False
        return (
            self.session.query(Transactions.hash)
            .filter(
                Transactions.type == TransactionType.DEPLOY_CONTRACT.value,
                Transactions.to_address == address,
            )
            .first()
            is not None
        )

    def get_transactions_for_address(
        self,
        address: str,
        filter: TransactionAddressFilter,
    ) -> list[dict]:
        try:
            address = to_checksum_address(address)
        except Exception:
            pass
        query = self.session.query(Transactions).options(
            selectinload(Transactions.triggered_transactions)
        )

        if filter == TransactionAddressFilter.TO:
            query = query.filter(Transactions.to_address == address)
        elif filter == TransactionAddressFilter.FROM:
            query = query.filter(Transactions.from_address == address)
        else:  # TransactionFilter.ALL
            query = query.filter(
                or_(
                    Transactions.from_address == address,
                    Transactions.to_address == address,
                )
            )

        transactions = query.order_by(
            Transactions.created_at.desc(),
            Transactions.nonce.desc().nullslast(),
            Transactions.hash.desc(),
        ).all()

        return [
            self._parse_transaction_data(transaction) for transaction in transactions
        ]

    def set_transaction_appeal(self, transaction_hash: str, appeal: bool):
        if not appeal:
            self.session.execute(
                text("UPDATE transactions SET appealed = :appeal WHERE hash = :hash"),
                {"hash": transaction_hash, "appeal": appeal},
            )
            self.session.commit()
        else:
            # Only appeal if transaction is in an appealable status
            result = self.session.execute(
                text(
                    """
                    UPDATE transactions
                    SET appealed = :appeal,
                        timestamp_appeal = :ts
                    WHERE hash = :hash
                      AND status IN ('ACCEPTED', 'UNDETERMINED', 'LEADER_TIMEOUT', 'VALIDATORS_TIMEOUT')
                    """
                ),
                {"hash": transaction_hash, "appeal": appeal, "ts": int(time.time())},
            )
            if result.rowcount > 0:
                self.session.commit()

    def admit_transaction_appeal(
        self,
        transaction_hash: str,
        *,
        expected_decision_id: int,
        submitted_at: int,
        appeal_deadline: int,
        retention_bps: int,
        prepare_fee_accounting: Callable[[dict | None], tuple[dict | None, int]],
    ) -> int:
        """Atomically reserve one exact-decision appeal.

        The caller deliberately commits only after applying the matching
        account debit/refund on this same session. This keeps Studio's appeal
        admission equivalent to one payable Solidity transaction and makes a
        concurrent second submission lose at the ``appealed = false`` gate.
        """

        row = (
            self.session.execute(
                text(
                    "SELECT status, appealed, consensus_history, consensus_data,"
                    " contract_snapshot, appeal_failed, appeal_undetermined,"
                    " appeal_leader_timeout, appeal_validators_timeout,"
                    " appeal_processing_time, rotation_count,"
                    " leader_timeout_validators, timestamp_awaiting_finalization, data"
                    " FROM transactions"
                    " WHERE hash = :hash FOR UPDATE"
                ),
                {"hash": transaction_hash},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ValueError("TransactionNotFound")
        if row["status"] not in {
            TransactionStatus.ACCEPTED.value,
            TransactionStatus.UNDETERMINED.value,
            TransactionStatus.LEADER_TIMEOUT.value,
            TransactionStatus.VALIDATORS_TIMEOUT.value,
        } or bool(row["appealed"]):
            raise ValueError("CanNotAppeal")
        if acceptance_dispatch_pending((row["data"] or {}).get(FEE_ACCOUNTING_KEY)):
            raise ValueError("CanNotAppeal")
        # The RPC may have waited for this row lock. Consensus evaluates the
        # deadline at transaction execution, not when the caller began
        # submitting it, so re-sample after lock acquisition and reject an
        # appeal whose window expired while queued.
        admitted_at = max(int(submitted_at), int(time.time()))
        updated = prepare_appeal_decision_basis(
            row["consensus_history"],
            expected_decision_id=expected_decision_id,
            submitted_at=admitted_at,
            appeal_deadline=appeal_deadline,
            retention_bps=retention_bps,
            appeal_context=(
                LEADER_APPEAL_REPLAY_CONTEXT
                if row["status"]
                in {
                    TransactionStatus.UNDETERMINED.value,
                    TransactionStatus.LEADER_TIMEOUT.value,
                }
                else VALIDATOR_APPEAL_CONTEXT
            ),
        )
        data = dict(row["data"] or {})
        fee_accounting, surplus_refund = prepare_fee_accounting(
            data.get(FEE_ACCOUNTING_KEY)
        )
        if fee_accounting is not None:
            data[FEE_ACCOUNTING_KEY] = fee_accounting
        status_value = (
            row["status"].value
            if hasattr(row["status"], "value")
            else str(row["status"])
        )
        data[APPEAL_RECOVERY_SNAPSHOT_KEY] = {
            "status": status_value,
            "consensusHistory": copy.deepcopy(updated),
            "consensusData": copy.deepcopy(row["consensus_data"]),
            "contractSnapshot": copy.deepcopy(row["contract_snapshot"]),
            "appealFailed": int(row["appeal_failed"] or 0),
            "appealUndetermined": bool(row["appeal_undetermined"]),
            "appealLeaderTimeout": bool(row["appeal_leader_timeout"]),
            "appealValidatorsTimeout": bool(row["appeal_validators_timeout"]),
            "appealProcessingTime": int(row["appeal_processing_time"] or 0),
            "rotationCount": int(row["rotation_count"] or 0),
            "leaderTimeoutValidators": copy.deepcopy(row["leader_timeout_validators"]),
            "timestampAwaitingFinalization": row["timestamp_awaiting_finalization"],
            "timestampAppeal": admitted_at,
        }
        self.session.execute(
            text(
                "UPDATE transactions"
                " SET appealed = true,"
                " timestamp_appeal = :submitted_at,"
                " consensus_history = CAST(:history AS jsonb),"
                " data = CAST(:data AS jsonb)"
                " WHERE hash = :hash"
            ),
            {
                "hash": transaction_hash,
                "submitted_at": admitted_at,
                "history": json.dumps(updated),
                "data": json.dumps(data),
            },
        )
        return int(surplus_refund)

    def restore_transaction_appeal_for_retry(self, transaction_hash: str) -> bool:
        """Restore the exact admitted state after interrupted appeal work."""

        transaction = (
            self.session.query(Transactions)
            .filter_by(hash=transaction_hash)
            .populate_existing()
            .with_for_update()
            .one_or_none()
        )
        if transaction is None or not isinstance(transaction.data, dict):
            return False
        snapshot = transaction.data.get(APPEAL_RECOVERY_SNAPSHOT_KEY)
        if not isinstance(snapshot, dict):
            return False
        try:
            transaction.status = TransactionStatus(str(snapshot["status"]))
        except (KeyError, TypeError, ValueError):
            return False

        transaction.consensus_history = copy.deepcopy(snapshot.get("consensusHistory"))
        transaction.consensus_data = copy.deepcopy(snapshot.get("consensusData"))
        transaction.contract_snapshot = copy.deepcopy(snapshot.get("contractSnapshot"))
        transaction.appealed = True
        transaction.appeal_failed = int(snapshot.get("appealFailed", 0) or 0)
        transaction.appeal_undetermined = bool(
            snapshot.get("appealUndetermined", False)
        )
        transaction.appeal_leader_timeout = bool(
            snapshot.get("appealLeaderTimeout", False)
        )
        transaction.appeal_validators_timeout = bool(
            snapshot.get("appealValidatorsTimeout", False)
        )
        transaction.appeal_processing_time = int(
            snapshot.get("appealProcessingTime", 0) or 0
        )
        transaction.rotation_count = int(snapshot.get("rotationCount", 0) or 0)
        transaction.leader_timeout_validators = copy.deepcopy(
            snapshot.get("leaderTimeoutValidators")
        )
        transaction.timestamp_awaiting_finalization = snapshot.get(
            "timestampAwaitingFinalization"
        )
        transaction.timestamp_appeal = int(snapshot.get("timestampAppeal", 0) or 0)
        self.session.flush()
        return True

    def clear_transaction_appeal_recovery_snapshot(
        self, transaction_hash: str, *, include_pending: bool = True
    ) -> None:
        """Drop the transient snapshot once an appeal has completed."""

        pending_guard = "" if include_pending else " AND status != 'PENDING'"
        self.session.execute(
            text(
                "UPDATE transactions SET data = data - :snapshot_key"
                " WHERE hash = :hash AND data ? :snapshot_key" + pending_guard
            ),
            {
                "hash": transaction_hash,
                "snapshot_key": APPEAL_RECOVERY_SNAPSHOT_KEY,
            },
        )

    def set_transaction_timestamp_awaiting_finalization(
        self, transaction_hash: str, timestamp_awaiting_finalization: int = None
    ):
        ts = (
            timestamp_awaiting_finalization
            if timestamp_awaiting_finalization
            else int(time.time())
        )
        self.session.execute(
            text(
                "UPDATE transactions SET timestamp_awaiting_finalization = :ts WHERE hash = :hash"
            ),
            {"hash": transaction_hash, "ts": ts},
        )

    def set_transaction_appeal_failed(self, transaction_hash: str, appeal_failed: int):
        if appeal_failed < 0:
            raise ValueError("appeal_failed must be a non-negative integer")
        result = self.session.execute(
            text("UPDATE transactions SET appeal_failed = :val WHERE hash = :hash"),
            {"hash": transaction_hash, "val": appeal_failed},
        )
        if result.rowcount == 0:
            print(
                f"[TRANSACTIONS_PROCESSOR]: Transaction {transaction_hash} not found, skipping appeal_failed update"
            )
            return
        self.session.commit()

    def set_transaction_appeal_undetermined(
        self, transaction_hash: str, appeal_undetermined: bool
    ):
        result = self.session.execute(
            text(
                "UPDATE transactions SET appeal_undetermined = :val WHERE hash = :hash"
            ),
            {"hash": transaction_hash, "val": appeal_undetermined},
        )
        if result.rowcount == 0:
            print(
                f"[TRANSACTIONS_PROCESSOR]: Transaction {transaction_hash} not found, skipping appeal_undetermined update"
            )
            return
        self.session.commit()

    def get_highest_timestamp(self) -> int:
        transaction = (
            self.session.query(Transactions)
            .filter(Transactions.timestamp_awaiting_finalization.isnot(None))
            .order_by(desc(Transactions.timestamp_awaiting_finalization))
            .first()
        )
        if transaction is None:
            return 0
        return transaction.timestamp_awaiting_finalization

    def get_transactions_for_block(
        self,
        block_number: int,
        include_full_tx: bool,
        include_contract_snapshot: bool = True,
    ) -> dict:
        query = self.session.query(Transactions).filter(
            Transactions.timestamp_awaiting_finalization == block_number
        )
        # Only eager load triggered_transactions if we need full transaction data
        if include_full_tx:
            query = query.options(selectinload(Transactions.triggered_transactions))
            if not include_contract_snapshot:
                query = query.options(defer(Transactions.contract_snapshot))
        transactions = query.all()

        block_hash = "0x" + "0" * 64
        parent_hash = "0x" + "0" * 64  # Placeholder for parent block hash
        timestamp = (
            transactions[0].timestamp_awaiting_finalization
            if len(transactions) > 0
            else int(time.time())
        )

        if include_full_tx:
            transaction_data = [
                self._parse_transaction_data(
                    tx, include_contract_snapshot=include_contract_snapshot
                )
                for tx in transactions
            ]
            if not include_contract_snapshot:
                for transaction in transaction_data:
                    transaction.pop("contract_snapshot", None)
        else:
            transaction_data = [tx.hash for tx in transactions]

        block_details = {
            "number": hex(block_number),
            "hash": block_hash,
            "parentHash": parent_hash,
            "sha3Uncles": "0x1dcc4de8dec75d7aab85b567b6ccd41ad312451b948a7413f0a142fd40d49347",
            "nonce": "0x" + "0" * 16,
            "logsBloom": "0x" + "00" * 256,
            "transactionsRoot": "0x" + "0" * 64,
            "stateRoot": "0x" + "0" * 64,
            "receiptsRoot": "0x" + "0" * 64,
            "transactions": transaction_data,
            "timestamp": hex(int(timestamp)),
            "miner": "0x" + "0" * 40,
            "difficulty": "0x0",
            "totalDifficulty": "0x0",
            "gasUsed": "0x0",
            "gasLimit": "0x1c9c380",  # 30M gas limit (standard)
            "baseFeePerGas": "0x0",
            "size": "0x0",
            "extraData": "0x",
            "mixHash": "0x" + "0" * 64,
            "uncles": [],
        }

        return block_details

    def get_newer_transactions(self, transaction_hash: str):
        transaction = (
            self.session.query(Transactions).filter_by(hash=transaction_hash).one()
        )
        transactions = (
            self.session.query(Transactions)
            .options(selectinload(Transactions.triggered_transactions))
            .filter(
                _transaction_order_tuple() > _transaction_order_value(transaction),
                Transactions.to_address == transaction.to_address,
            )
            .order_by(Transactions.queue_order)
            .all()
        )
        return [
            self._parse_transaction_data(transaction) for transaction in transactions
        ]

    def update_consensus_history(
        self,
        transaction_hash: str,
        consensus_round: ConsensusRound,
        leader_result: list[Receipt] | None,
        validator_results: list[Receipt],
        extra_status_change: TransactionStatus | None = None,
    ):
        # Narrow SELECT — only loads consensus_history, avoids 53MB contract_snapshot.
        # FOR UPDATE locks the row to prevent lost updates from concurrent workers.
        row = self.session.execute(
            text(
                "SELECT consensus_history FROM transactions"
                " WHERE hash = :hash FOR UPDATE"
            ),
            {"hash": transaction_hash},
        ).one()
        current_history = row[0] or {}

        status_changes_to_use = list(current_history.get("current_status_changes", []))
        if extra_status_change:
            status_changes_to_use.append(extra_status_change.value)

        monitoring_to_use = current_history.get("current_monitoring", {})

        current_consensus_results = {
            "consensus_round": consensus_round.value,
            "leader_result": (
                [
                    receipt.to_dict(strip_contract_state=True)
                    for receipt in leader_result
                ]
                if leader_result
                else None
            ),
            "validator_results": [
                receipt.to_dict(strip_contract_state=True)
                for receipt in validator_results
            ],
            "status_changes": status_changes_to_use,
            "monitoring": monitoring_to_use,
        }

        consensus_results = list(current_history.get("consensus_results", []))
        consensus_results.append(current_consensus_results)

        new_history = {
            **current_history,
            "consensus_results": consensus_results,
            "current_status_changes": [],
            "current_monitoring": {},
        }
        if extra_status_change in {
            TransactionStatus.ACCEPTED,
            TransactionStatus.UNDETERMINED,
            TransactionStatus.LEADER_TIMEOUT,
            TransactionStatus.VALIDATORS_TIMEOUT,
        }:
            new_history = materialize_decision_metadata(
                new_history,
                status=extra_status_change.value,
                materialized_at=int(time.time()),
                default_appeal_window=int(
                    os.environ.get("VITE_FINALITY_WINDOW", "1800")
                ),
            )

        self.session.execute(
            text(
                "UPDATE transactions"
                " SET consensus_history = CAST(:data AS jsonb)"
                " WHERE hash = :hash"
            ),
            {"hash": transaction_hash, "data": json.dumps(new_history)},
        )
        self.session.commit()

    def reset_consensus_history(self, transaction_hash: str):
        self.session.execute(
            text(
                "UPDATE transactions SET consensus_history = '{}'::jsonb WHERE hash = :hash"
            ),
            {"hash": transaction_hash},
        )
        self.session.commit()

    def reset_transaction_for_recomputation(
        self,
        transaction_hash: str,
        data: dict | None,
    ) -> None:
        """Invalidate every attempt-local field before an ancestor rewind.

        The transaction identity, escrow, queue order, and credited user value
        remain durable. Consensus evidence and worker ownership belong to the
        invalidated descendant attempt and must not survive into recomputation.
        """

        result = self.session.execute(
            text(
                """
                UPDATE transactions
                SET status = 'PENDING'::transaction_status,
                    data = CAST(:data AS jsonb),
                    consensus_data = NULL,
                    consensus_history = '{}'::jsonb,
                    contract_snapshot = NULL,
                    appealed = false,
                    appeal_failed = 0,
                    appeal_undetermined = false,
                    appeal_leader_timeout = false,
                    appeal_validators_timeout = false,
                    timestamp_appeal = NULL,
                    appeal_processing_time = 0,
                    timestamp_awaiting_finalization = NULL,
                    last_vote_timestamp = NULL,
                    rotation_count = 0,
                    leader_timeout_validators = NULL,
                    blocked_at = NULL,
                    worker_id = NULL
                WHERE hash = :hash
                """
            ),
            {
                "hash": transaction_hash,
                "data": json.dumps(data) if data is not None else None,
            },
        )
        if result.rowcount == 0:
            raise ValueError("TransactionNotFound")
        self.session.commit()

    def set_transaction_timestamp_appeal(
        self, transaction: Transactions | str, timestamp_appeal: int | None
    ):
        tx_hash = transaction if isinstance(transaction, str) else transaction.hash
        self.session.execute(
            text("UPDATE transactions SET timestamp_appeal = :ts WHERE hash = :hash"),
            {"hash": tx_hash, "ts": timestamp_appeal},
        )

    def set_transaction_appeal_processing_time(self, transaction_hash: str):
        result = self.session.execute(
            text(
                """
                UPDATE transactions
                SET appeal_processing_time = appeal_processing_time + (CAST(:now AS integer) - timestamp_appeal)
                WHERE hash = :hash
                  AND timestamp_appeal IS NOT NULL
                """
            ),
            {"hash": transaction_hash, "now": round(time.time())},
        )
        if result.rowcount == 0:
            print(
                f"[TRANSACTIONS_PROCESSOR]: Transaction {transaction_hash} not found or has no timestamp_appeal, skipping appeal_processing_time update"
            )
            return
        self.session.commit()

    def reset_transaction_appeal_processing_time(self, transaction_hash: str):
        self.session.execute(
            text(
                "UPDATE transactions SET appeal_processing_time = 0 WHERE hash = :hash"
            ),
            {"hash": transaction_hash},
        )
        self.session.commit()

    def set_transaction_contract_snapshot(
        self, transaction_hash: str, contract_snapshot: dict | None
    ):
        self.session.execute(
            text(
                "UPDATE transactions SET contract_snapshot = CAST(:data AS jsonb) WHERE hash = :hash"
            ),
            {
                "hash": transaction_hash,
                "data": json.dumps(contract_snapshot) if contract_snapshot else None,
            },
        )
        self.session.commit()

    def transactions_in_process_by_contract(self) -> list[dict]:
        transactions = (
            self.session.query(Transactions)
            .options(selectinload(Transactions.triggered_transactions))
            .filter(
                Transactions.to_address.isnot(None),
                Transactions.status.in_(
                    [
                        TransactionStatus.ACTIVATED,
                        TransactionStatus.PROPOSING,
                        TransactionStatus.COMMITTING,
                        TransactionStatus.REVEALING,
                    ]
                ),
            )
            .distinct(Transactions.to_address)
            .order_by(
                Transactions.to_address,
                Transactions.queue_order.asc(),
            )
            .all()
        )

        return [
            self._parse_transaction_data(transaction) for transaction in transactions
        ]

    def get_previous_transaction(
        self,
        transaction_hash: str,
        status: TransactionStatus | None = None,
        filter_success: bool = False,
    ) -> dict | None:
        # Expire cached ORM objects to ensure we read fresh data after raw SQL writes
        self.session.expire_all()
        transaction = (
            self.session.query(Transactions).filter_by(hash=transaction_hash).one()
        )

        if transaction.type == TransactionType.DEPLOY_CONTRACT:
            return None

        filters = [
            _transaction_order_tuple() < _transaction_order_value(transaction),
            Transactions.to_address == transaction.to_address,
        ]
        if status is not None:
            filters.append(Transactions.status == status)

        if filter_success:
            consensus_data = type_coerce(Transactions.consensus_data, JSON)

            # Handle both formats of leader_receipt (dict and array)
            filters.append(
                and_(
                    consensus_data.isnot(None),
                    consensus_data["leader_receipt"].isnot(None),
                    text(
                        """
                        (
                            (jsonb_typeof(consensus_data::jsonb->'leader_receipt') = 'object'
                             AND consensus_data::jsonb->'leader_receipt'->>'execution_result' = :status)
                            OR
                            (jsonb_typeof(consensus_data::jsonb->'leader_receipt') = 'array'
                             AND consensus_data::jsonb->'leader_receipt'->0->>'execution_result' = :status)
                        )
                    """
                    ).bindparams(status=ExecutionResultStatus.SUCCESS.value),
                )
            )

        closest_transaction = (
            self.session.query(Transactions)
            .filter(*filters)
            .order_by(Transactions.queue_order.desc())
            .first()
        )

        return (
            self._parse_transaction_data(closest_transaction)
            if closest_transaction
            else None
        )

    def set_transaction_timestamp_last_vote(self, transaction_hash: str):
        self.session.execute(
            text(
                "UPDATE transactions SET last_vote_timestamp = :ts WHERE hash = :hash"
            ),
            {"hash": transaction_hash, "ts": int(time.time())},
        )
        self.session.commit()

    def increase_transaction_rotation_count(self, transaction_hash: str):
        self.session.execute(
            text(
                """
                UPDATE transactions
                SET rotation_count = rotation_count + 1
                WHERE hash = :hash
                  AND (config_rotation_rounds IS NULL
                       OR config_rotation_rounds = 0
                       OR rotation_count < config_rotation_rounds)
                """
            ),
            {"hash": transaction_hash},
        )
        self.session.commit()

    def reset_transaction_rotation_count(self, transaction_hash: str):
        self.session.execute(
            text("UPDATE transactions SET rotation_count = 0 WHERE hash = :hash"),
            {"hash": transaction_hash},
        )
        self.session.commit()

    def set_transaction_appeal_leader_timeout(
        self, transaction_hash: str, appeal_leader_timeout: bool
    ) -> bool:
        result = self.session.execute(
            text(
                "UPDATE transactions SET appeal_leader_timeout = :val WHERE hash = :hash"
            ),
            {"hash": transaction_hash, "val": appeal_leader_timeout},
        )
        if result.rowcount == 0:
            print(
                f"[TRANSACTIONS_PROCESSOR]: Transaction {transaction_hash} not found, skipping appeal_leader_timeout update"
            )
            return False
        self.session.commit()
        return appeal_leader_timeout

    def set_leader_timeout_validators(self, transaction_hash: str, validators: list):
        self.session.execute(
            text(
                "UPDATE transactions SET leader_timeout_validators = CAST(:data AS jsonb) WHERE hash = :hash"
            ),
            {"hash": transaction_hash, "data": json.dumps(validators)},
        )
        self.session.commit()

    def set_transaction_appeal_validators_timeout(
        self, transaction_hash: str, appeal_validators_timeout: bool
    ) -> bool:
        result = self.session.execute(
            text(
                "UPDATE transactions SET appeal_validators_timeout = :val WHERE hash = :hash"
            ),
            {"hash": transaction_hash, "val": appeal_validators_timeout},
        )
        if result.rowcount == 0:
            print(
                f"[TRANSACTIONS_PROCESSOR]: Transaction {transaction_hash} not found, skipping appeal_validators_timeout update"
            )
            return False
        self.session.commit()
        return appeal_validators_timeout

    def get_pending_transaction_count_for_address(self, address: str) -> int:
        """
        Get Consensus pending-queue membership for a recipient.

        The activated head remains in Queues.pending until it reaches a
        decision, so all pre-decision processing states consume one slot.

        Args:
            address: The recipient address to count pending transactions for

        Returns:
            int: The number of pending transactions for the address
        """
        try:
            # Normalize address to checksum format
            checksum_address = to_checksum_address(address)
        except (TypeError, ValueError):
            # If address normalization fails, use as-is
            checksum_address = address

        count = (
            self.session.query(Transactions)
            .filter(
                Transactions.to_address == checksum_address,
                Transactions.status.in_(
                    [
                        TransactionStatus.PENDING,
                        TransactionStatus.ACTIVATED,
                        TransactionStatus.PROPOSING,
                        TransactionStatus.COMMITTING,
                        TransactionStatus.REVEALING,
                    ]
                ),
            )
            .count()
        )
        return count

    def get_transaction_status(self, transaction_hash: str) -> dict | None:
        transaction = (
            self.session.query(Transactions).filter_by(hash=transaction_hash).first()
        )
        if not transaction:
            return None
        transaction_status = transaction.status
        return self._status_payload(transaction_status.value)

    def is_transaction_finalization_head(self, transaction_hash: str) -> bool:
        """Return whether no older non-terminal transaction blocks this decision.

        This mirrors the per-recipient ordering gate used by
        ``claim_next_finalization`` so lifecycle projection never advertises a
        Finalize action that the worker is not allowed to execute.
        """
        transaction = (
            self.session.query(Transactions).filter_by(hash=transaction_hash).first()
        )
        if transaction is None:
            return False
        return (
            not self.session.query(Transactions.hash)
            .filter(
                Transactions.to_address == transaction.to_address,
                _transaction_order_tuple() < _transaction_order_value(transaction),
                Transactions.hash != transaction.hash,
                Transactions.status.notin_(
                    [TransactionStatus.FINALIZED, TransactionStatus.CANCELED]
                ),
            )
            .first()
        )

    def get_processing_transaction_for_contract(
        self, contract_address: str
    ) -> dict | None:
        """
        Check if there's a transaction currently being processed for a contract.

        Args:
            contract_address: The contract address to check

        Returns:
            Transaction data if processing, None otherwise
        """
        try:
            contract_address = to_checksum_address(contract_address)
        except Exception:
            pass
        processing_tx = (
            self.session.query(Transactions)
            .filter(
                Transactions.to_address == contract_address,
                Transactions.status.in_(
                    [
                        TransactionStatus.ACTIVATED,
                        TransactionStatus.PROPOSING,
                        TransactionStatus.COMMITTING,
                        TransactionStatus.REVEALING,
                    ]
                ),
            )
            .first()
        )

        return self._parse_transaction_data(processing_tx) if processing_tx else None

    def get_oldest_pending_for_contract(self, contract_address: str) -> dict | None:
        """
        Get the oldest pending transaction for a specific contract.

        Args:
            contract_address: The contract address

        Returns:
            Oldest pending transaction data or None
        """
        try:
            contract_address = to_checksum_address(contract_address)
        except Exception:
            pass
        pending_tx = (
            self.session.query(Transactions)
            .filter(
                Transactions.to_address == contract_address,
                Transactions.status == TransactionStatus.PENDING,
            )
            .order_by(Transactions.queue_order)
            .first()
        )

        return self._parse_transaction_data(pending_tx) if pending_tx else None

    def get_contracts_with_pending(self) -> list[str]:
        """
        Get all distinct contract addresses that have pending transactions.
        Also includes a special marker for None addresses (burn transactions).

        Returns:
            List of contract addresses with pending transactions (may include special marker)
        """
        results = (
            self.session.query(Transactions.to_address)
            .filter(Transactions.status == TransactionStatus.PENDING)
            .distinct()
            .all()
        )

        # Convert None addresses to a special marker
        addresses = []
        for (addr,) in results:
            if addr is None:
                addresses.append(
                    "__zero_address__"
                )  # Special marker for burn transactions
            else:
                addresses.append(addr)
        return addresses

    def reset_stuck_transactions(self, timeout_seconds: int = 900) -> int:
        """
        Reset transactions that have been stuck in processing states.

        Args:
            timeout_seconds: How long a transaction must be in processing state to be considered stuck

        Returns:
            Number of transactions reset
        """
        from datetime import datetime, timedelta, timezone

        cutoff_time = datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)

        stuck_transactions = (
            self.session.query(Transactions)
            .filter(
                Transactions.status.in_(
                    [
                        TransactionStatus.ACTIVATED,
                        TransactionStatus.PROPOSING,
                        TransactionStatus.COMMITTING,
                        TransactionStatus.REVEALING,
                    ]
                ),
                Transactions.created_at < cutoff_time,
            )
            .all()
        )

        count = 0
        for tx in stuck_transactions:
            tx.status = TransactionStatus.PENDING
            # Reset appeal flags if consensus_data is missing (can't process appeal without it)
            if tx.consensus_data is None:
                tx.appealed = False
                tx.appeal_undetermined = False
                tx.appeal_validators_timeout = False
                tx.appeal_leader_timeout = False
            count += 1

        if count > 0:
            self.session.commit()

        return count
