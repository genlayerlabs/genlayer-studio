# backend/consensus/base.py

DEFAULT_VALIDATORS_COUNT = 5
ACTIVATED_TRANSACTION_TIMEOUT = 900
MAX_IDLE_REPLACEMENTS = 5
DEFAULT_EXEC_TIMEOUT_SECONDS = 600
DEFAULT_LEADER_EXEC_TIMEOUT_SECONDS = DEFAULT_EXEC_TIMEOUT_SECONDS
DEFAULT_VALIDATOR_EXEC_TIMEOUT_SECONDS = DEFAULT_EXEC_TIMEOUT_SECONDS

import os
import asyncio
from typing import Any, Callable, List, Iterable, Literal
import time
from abc import ABC, abstractmethod
import random
from copy import deepcopy
import json
import base64

from eth_utils import is_address, keccak, to_bytes, to_checksum_address
from sqlalchemy import text
from sqlalchemy.orm import Session
from backend.consensus.vrf import get_validators_for_transaction
from backend.database_handler.chain_snapshot import ChainSnapshot
from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.database_handler.contract_processor import ContractProcessor
from backend.database_handler.errors import ContractNotFoundError
from backend.database_handler.transactions_processor import (
    TransactionsProcessor,
    TransactionStatus,
)
from backend.database_handler.models import Transactions
from backend.database_handler.accounts_manager import AccountsManager
from backend.database_handler.types import ConsensusData
from backend.domain.types import (
    Transaction,
    TransactionType,
    TransactionExecutionMode,
    LLMProvider,
    Validator,
)
from backend.node.base import Node
from backend.node.types import (
    ExecutionMode,
    Receipt,
    Vote,
    ExecutionResultStatus,
    PendingTransaction,
)
from backend.protocol_rpc.message_handler.base import MessageHandler
from backend.protocol_rpc.message_handler.types import (
    LogEvent,
    EventType,
    EventScope,
)
from backend.protocol_rpc.types import ZERO_ADDRESS
from backend.protocol_rpc.ghost_factory import GhostFactoryConfig
from backend.protocol_rpc.fees import (
    FEE_ACCOUNTING_KEY,
    VALIDATORS_PER_ROUND,
    FeeValidationError,
    StudioFeePolicy,
    activate_fee_accounting,
    consume_message_fees,
    create_child_fee_accounting,
    derive_external_message_call_key,
    discard_active_message_generation,
    fill_message_fee_payload_from_allocation,
    mark_message_effects_delivered,
    message_effect_identities,
    message_novelty_mask,
    prepare_reveal_message_generation,
    record_external_message_execution_fees,
    refund_failed_internal_message_fee,
    runtime_rotations_for_round,
    stamp_receipt_execution_policy,
    unwind_reveal_message_fees,
    validate_receipt_admission_caps,
)
from backend.rollup.consensus_service import ConsensusService

import backend.validators as validators
from backend.node.genvm.origin.host_fns import ResultCode
from backend.consensus.types import (
    ConsensusResult,
    ConsensusRound,
    consensus_vote_type_code,
)
from backend.consensus.history import (
    TERMINAL_VALIDATOR_APPEAL_ROUNDS,
    latest_decision_metadata,
    logical_fee_round_entries,
)
from backend.consensus.utils import determine_consensus_from_votes
from backend.consensus.decisions import (
    decide_undetermined,
    decide_leader_timeout,
    decide_validators_timeout,
    decide_accepted,
    decide_finalizing,
    decide_revealing,
    merge_appeal_validators,
    decide_pending_pre,
    decide_pending_activate,
    prepare_proposing,
    decide_post_proposal,
    prepare_committing,
    decide_post_committing,
    should_rollback_after_accepted,
)
from backend.consensus.effect_executor import EffectExecutor
from backend.node.genvm import get_code_slot
from backend.node.genvm.error_codes import GenVMInternalError, GenVMErrorCode
from backend.node.base import Manager as GenVMManager


def _validators_in_frozen_selection_pool(
    all_validators: list[dict],
    fee_accounting: dict | None,
) -> list[dict]:
    """Apply Consensus's activation-pinned selection-pool identity gate."""

    frozen_pool = (
        fee_accounting.get("selection_pool_addresses")
        if isinstance(fee_accounting, dict)
        else None
    )
    if not isinstance(frozen_pool, list) or not frozen_pool:
        return list(all_validators)
    frozen_addresses = {str(address).lower() for address in frozen_pool if address}
    return [
        validator
        for validator in all_validators
        if str(validator.get("address") or "").lower() in frozen_addresses
    ]


# Cap on concurrently executing validators per transaction. Bounds GenVM
# subprocess memory, fd, and DB-session usage; larger committees run through
# this window. See issue #1721.
VALIDATOR_MAX_CONCURRENT = max(
    1, int(os.environ.get("CONSENSUS_VALIDATOR_MAX_CONCURRENT", "8"))
)

type NodeFactory = Callable[
    [
        dict,
        ExecutionMode,
        ContractSnapshot,
        Receipt | None,
        MessageHandler,
        Callable[[str], ContractSnapshot],
        validators.Snapshot,
        Callable[[str], None] | None,
        GenVMManager,
        dict[str, bytes] | None,
        dict[str, ContractSnapshot] | None,
    ],
    Node,
]


class NoValidatorsAvailableError(Exception):
    """Raised when no validators are available to process a transaction."""


class InternalMessageEmissionError(RuntimeError):
    """Raised when the helper EVM did not durably accept a message phase."""


def _redact_consensus_data_for_log(consensus_data_dict: dict) -> dict:
    """
    Return a redacted copy of the consensus data suitable for logging.

    Removes heavy/noisy fields like `contract_state` from any leader receipts,
    and sensitive configuration data from node configs.
    """
    try:
        redacted = deepcopy(consensus_data_dict)
    except Exception:
        # In case deepcopy fails for any reason, avoid breaking logging
        return {"error": "failed_to_copy_consensus_data_for_log"}

    # Remove validators key entirely
    redacted.pop("validators", None)

    leader_receipt = redacted.get("leader_receipt")
    if isinstance(leader_receipt, dict):
        _redact_receipt_data(leader_receipt)
    elif isinstance(leader_receipt, list):
        # Only keep the first receipt (leader_receipt[0]), remove others
        if len(leader_receipt) > 1:
            redacted["leader_receipt"] = [leader_receipt[0]]

        for receipt in redacted["leader_receipt"]:
            if isinstance(receipt, dict):
                _redact_receipt_data(receipt)

    return redacted


def _redact_receipt_data(receipt: dict) -> None:
    """
    Redact sensitive data from a single receipt.
    """
    # Remove contract_state (existing behavior)
    receipt.pop("contract_state", None)

    # Redact node_config sensitive data
    node_config = receipt.get("node_config")
    if isinstance(node_config, dict):
        # Remove private_key
        node_config.pop("private_key", None)

        # Redact primary_model config data
        primary_model = node_config.get("primary_model")
        if isinstance(primary_model, dict):
            primary_model.pop("config", None)
            primary_model.pop("plugin_config", None)

        # Redact secondary_model config data
        secondary_model = node_config.get("secondary_model")
        if isinstance(secondary_model, dict):
            secondary_model.pop("config", None)
            secondary_model.pop("plugin_config", None)

    # Handle genvm_result stdout/stderr
    genvm_result = receipt.get("genvm_result")
    if isinstance(genvm_result, dict):
        # Only remove stdout if stderr is not present
        if "stderr" not in genvm_result or not genvm_result.get("stderr"):
            genvm_result.pop("stdout", None)

    # Handle calldata
    if isinstance(receipt.get("calldata"), str):
        receipt["calldata"] = f"<truncated {len(receipt['calldata'])} characters>"

    # Handle pending transactions - truncate code field
    pending_transactions = receipt.get("pending_transactions")
    if isinstance(pending_transactions, list):
        for pending_tx in pending_transactions:
            if isinstance(pending_tx, dict) and "code" in pending_tx:
                code = pending_tx["code"]
                if code is not None:
                    pending_tx["code"] = f"<truncated {len(str(code))} characters>"


def _redact_transaction_for_log(transaction_dict: dict) -> dict:
    """
    Return a redacted copy of the transaction data suitable for logging.

    Replaces contract_code from data with truncation message to reduce log verbosity.
    """
    try:
        redacted = deepcopy(transaction_dict)
    except Exception:
        # In case deepcopy fails for any reason, avoid breaking logging
        return {"error": "failed_to_copy_transaction_for_log"}

    # Replace data.contract_code with truncation message if present
    data = redacted.get("data")
    if isinstance(data, dict):
        contract_code = data.get("contract_code")
        if contract_code is not None:
            data["contract_code"] = f"<truncated {len(str(contract_code))} characters>"

    return redacted


def _redact_contract_for_log(contract_dict: dict) -> dict:
    """
    Return a redacted copy of the contract data suitable for logging.

    Removes data.state and truncates data.code to reduce log verbosity.
    """
    try:
        redacted = deepcopy(contract_dict)
    except Exception:
        # In case deepcopy fails for any reason, avoid breaking logging
        return {"error": "failed_to_copy_contract_for_log"}

    # Remove data.state if present
    data = redacted.get("data")
    if isinstance(data, dict):
        data.pop("state", None)

    return redacted


def _slot_budget_seconds(
    transaction: Transaction,
    role: Literal["leader", "validator"],
) -> float:
    """Per-tx slot budget.

    Future: read from transaction.sim_config / gas mechanism. Today: env var
    fallback.
    """
    _ = transaction
    if role == "leader":
        env_key = "CONSENSUS_LEADER_EXEC_TIMEOUT_SECONDS"
        default = DEFAULT_LEADER_EXEC_TIMEOUT_SECONDS
    else:
        env_key = "CONSENSUS_VALIDATOR_EXEC_TIMEOUT_SECONDS"
        default = DEFAULT_VALIDATOR_EXEC_TIMEOUT_SECONDS

    raw_timeout = os.getenv(env_key)
    if raw_timeout is None:
        return float(default)
    try:
        timeout = float(raw_timeout)
    except ValueError:
        return float(default)
    if timeout <= 0:
        return float(default)
    return timeout


def node_factory(
    validator: dict,
    validator_mode: ExecutionMode,
    contract_snapshot: ContractSnapshot,
    leader_receipt: Receipt | None,
    msg_handler: MessageHandler,
    contract_snapshot_factory: Callable[[str], ContractSnapshot],
    validators_manager_snapshot: validators.Snapshot,
    timing_callback: Callable[[str], None] | None,
    genvm_manager: GenVMManager,
    shared_decoded_value_cache: dict[str, bytes] | None = None,
    shared_contract_snapshot_cache: dict[str, ContractSnapshot] | None = None,
) -> Node:
    """
    Factory function to create a Node instance.

    Args:
        validator (dict): Validator information.
        validator_mode (ExecutionMode): Mode of execution for the validator.
        contract_snapshot (ContractSnapshot): Snapshot of the contract state.
        leader_receipt (Receipt | None): Receipt of the leader node.
        msg_handler (MessageHandler): Handler for messaging.
        contract_snapshot_factory (Callable[[str], ContractSnapshot]): Factory function to create contract snapshots.
        timing_callback (Callable[[str], None] | None): Optional callback for timing measurements.

    Returns:
        Node: A new Node instance.
    """
    # Create a node instance with the provided parameters
    return Node(
        contract_snapshot=contract_snapshot,
        validator_mode=validator_mode,
        leader_receipt=leader_receipt,
        msg_handler=msg_handler,
        validator=Validator(
            address=validator["address"],
            private_key=validator["private_key"],
            stake=validator["stake"],
            llmprovider=LLMProvider(
                provider=validator["provider"],
                model=validator["model"],
                config=validator["config"],
                plugin=validator["plugin"],
                plugin_config=validator["plugin_config"],
            ),
            fallback_validator=validator["fallback_validator"],
        ),
        contract_snapshot_factory=contract_snapshot_factory,
        validators_snapshot=validators_manager_snapshot,
        timing_callback=timing_callback,
        manager=genvm_manager,
        shared_decoded_value_cache=shared_decoded_value_cache,
        shared_contract_snapshot_cache=shared_contract_snapshot_cache,
    )


def transaction_genvm_executor_selector(transaction: Transaction) -> str | None:
    """Studio-only GenVM executor override carried by the transaction."""
    return (
        transaction.sim_config.genvm_executor_selector
        if transaction.sim_config
        else None
    )


def contract_snapshot_factory(
    contract_address: str,
    session: Session,
    transaction: Transaction,
):
    """
    Factory function to create a ContractSnapshot instance.

    Args:
        contract_address (str): The address of the contract.
        session (Session): The database session.
        transaction (Transaction): The transaction related to the contract.

    Returns:
        ContractSnapshot: A new ContractSnapshot instance.
    """
    try:
        contract_address = to_checksum_address(contract_address)
    except Exception:
        pass
    # Check if the transaction is a contract deployment and the contract address matches the transaction's to address
    if (
        transaction.type == TransactionType.DEPLOY_CONTRACT
        and contract_address == transaction.to_address
        and transaction.status
        not in [TransactionStatus.ACCEPTED, TransactionStatus.FINALIZED]
    ):
        # Create a new ContractSnapshot instance for the new contract
        ret = ContractSnapshot(None, session)
        ret.contract_address = transaction.to_address
        ret.contract_code = transaction.data["contract_code"]
        ret.balance = transaction.value or 0
        ret.states = {"accepted": {}, "finalized": {}}
        # The contract row is still empty at deploy time, so the executor
        # override can only come from the deploy transaction itself.
        ret.genvm_executor_selector = transaction_genvm_executor_selector(transaction)
        return ret

    # Return a ContractSnapshot instance for an existing contract
    return ContractSnapshot(contract_address, session)


def contract_processor_factory(session: Session):
    """
    Factory function to create a ContractProcessor instance.
    """
    return ContractProcessor(session)


def chain_snapshot_factory(session: Session):
    """
    Factory function to create a ChainSnapshot instance.

    Args:
        session (Session): The database session.

    Returns:
        ChainSnapshot: A new ChainSnapshot instance.
    """
    return ChainSnapshot(session)


def transactions_processor_factory(session: Session):
    """
    Factory function to create a TransactionsProcessor instance.

    Args:
        session (Session): The database session.

    Returns:
        TransactionsProcessor: A new TransactionsProcessor instance.
    """
    return TransactionsProcessor(session)


def accounts_manager_factory(session: Session):
    """
    Factory function to create an AccountsManager instance.

    Args:
        session (Session): The database session.

    Returns:
        AccountsManager: A new AccountsManager instance.
    """
    return AccountsManager(session)


class TransactionContext:
    """
    Class representing the context of a transaction.

    Attributes:
        transaction (Transaction): The transaction.
        transactions_processor (TransactionsProcessor): Instance responsible for handling transaction operations within the database.
        chain_snapshot (ChainSnapshot): Snapshot of the chain state.
        accounts_manager (AccountsManager): Manager for accounts.
        contract_snapshot_factory (Callable[[str], ContractSnapshot]): Factory function to create contract snapshots.
        node_factory (Callable[[dict, ExecutionMode, ContractSnapshot, Receipt | None, MessageHandler, Callable[[str], ContractSnapshot]], Node]): Factory function to create nodes.
        msg_handler (MessageHandler): Handler for messaging.
        consensus_data (ConsensusData): Data related to the consensus process.
        iterator_rotation (Iterator[list] | None): Iterator for rotating validators.
        remaining_validators (list): List of remaining validators.
        num_validators (int): Number of validators.
        contract_snapshot (ContractSnapshot | None): Snapshot of the contract state.
        votes (dict): Dictionary of votes.
        validator_nodes (list): List of validator nodes.
        validation_results (list): List of validation results.
        consensus_service (ConsensusService): Consensus service to interact with the rollup.
    """

    def __init__(
        self,
        transaction: Transaction,
        transactions_processor: TransactionsProcessor,
        chain_snapshot: ChainSnapshot | None,
        accounts_manager: AccountsManager,
        contract_snapshot_factory: Callable[[str], ContractSnapshot],
        contract_processor: ContractProcessor,
        node_factory: NodeFactory,
        msg_handler: MessageHandler,
        consensus_service: ConsensusService,
        validators_snapshot: validators.Snapshot | None,
        genvm_manager: GenVMManager,
    ):
        """
        Initialize the TransactionContext.

        Args:
            transaction (Transaction): The transaction.
            transactions_processor (TransactionsProcessor): Instance responsible for handling transaction operations within the database.
            chain_snapshot (ChainSnapshot): Snapshot of the chain state.
            accounts_manager (AccountsManager): Manager for accounts.
            contract_snapshot_factory (Callable[[str], ContractSnapshot]): Factory function to create contract snapshots.
            node_factory (Callable[[dict, ExecutionMode, ContractSnapshot, Receipt | None, MessageHandler, Callable[[str], ContractSnapshot]], Node]): Factory function to create nodes.
            msg_handler (MessageHandler): Handler for messaging.
            consensus_service (ConsensusService): Consensus service to interact with the rollup.
        """
        self.transaction = transaction
        self.transactions_processor = transactions_processor
        self.chain_snapshot = chain_snapshot
        self.accounts_manager = accounts_manager
        self.contract_snapshot_factory = contract_snapshot_factory
        self.contract_processor = contract_processor
        self.node_factory = node_factory
        self.genvm_manager = genvm_manager
        self.msg_handler = msg_handler
        self.consensus_data = ConsensusData(
            votes={}, leader_receipt=None, validators=[]
        )
        self.involved_validators: list[dict] = []
        self.remaining_validators: list = []
        self.num_validators: int = 0
        self.votes: dict = {}
        self.validator_nodes: list = []
        self.validation_results: list = []
        self.rotation_count: int = 0
        self.active_fee_round: int | None = None
        self.consensus_service = consensus_service
        self.leader: dict = {}
        # Shared for the lifetime of this transaction context (leader + validators).
        self.shared_decoded_value_cache: dict[str, bytes] = {}
        self.shared_contract_snapshot_cache: dict[str, ContractSnapshot] = {}

        if self.transaction.type != TransactionType.SEND:
            saved = self.transaction.contract_snapshot
            has_real_data = saved and (
                (hasattr(saved, "states") and saved.states.get("accepted"))
                or (hasattr(saved, "balance") and saved.balance is not None)
            )
            if has_real_data:
                self.contract_snapshot = saved
                # Saved snapshots now carry balance (pre-execution balance at acceptance time).
                # This ensures appeal validators see the correct balance when verifying
                # the original execution. Only hydrate from DB if snapshot has no balance
                # (legacy snapshots created before this change).
                if not hasattr(saved, "balance") or saved.balance is None:
                    fresh = self.contract_snapshot_factory(self.transaction.to_address)
                    self.contract_snapshot.balance = fresh.balance
            else:
                try:
                    self.contract_snapshot = self.contract_snapshot_factory(
                        self.transaction.to_address
                    )
                except ContractNotFoundError:
                    # For DEPLOY, the current_state row exists (created at tx
                    # submission) but is still empty — this is expected, the
                    # contract hasn't executed yet.
                    #
                    # For RUN_CONTRACT / UPGRADE_CONTRACT, a missing contract
                    # (no row, or data={} left behind by a failed deploy) means
                    # the user is calling something that was never successfully
                    # deployed. Re-raise so the worker-level handler can
                    # finalize the tx with a proper error receipt instead of
                    # letting it hang.
                    if self.transaction.type == TransactionType.DEPLOY_CONTRACT:
                        self.contract_snapshot = None
                    else:
                        raise

        self.validators_snapshot = validators_snapshot


class ConsensusAlgorithm:
    """
    Class representing the consensus algorithm.

    Attributes:
        get_session (Callable[[], Session]): Function to get a database session.
        msg_handler (MessageHandler): Handler for messaging.
        consensus_service (ConsensusService): Consensus service to interact with the rollup.
        finality_window_time (int): Time in seconds for the finality window.
    """

    def __init__(
        self,
        get_session: Callable[[], Session],
        msg_handler: MessageHandler,
        consensus_service: ConsensusService,
        validators_manager: validators.Manager,
        genvm_manager: GenVMManager,
    ):
        """
        Initialize the ConsensusAlgorithm.

        Args:
            get_session (Callable[[], Session]): Function to get a database session.
            msg_handler (MessageHandler): Handler for messaging.
            consensus_service (ConsensusService): Consensus service to interact with the rollup.
        """
        self.get_session = get_session
        self.msg_handler = msg_handler
        self.consensus_service = consensus_service
        self.finality_window_time = int(os.environ["VITE_FINALITY_WINDOW"])
        self.finality_window_appeal_failed_reduction = float(
            os.environ["VITE_FINALITY_WINDOW_APPEAL_FAILED_REDUCTION"]
        )
        self.validators_manager = validators_manager
        self.genvm_manager = genvm_manager

    async def exec_transaction(
        self,
        transaction: Transaction,
        transactions_processor: TransactionsProcessor,
        chain_snapshot: ChainSnapshot | None,
        accounts_manager: AccountsManager,
        contract_snapshot_factory: Callable[[str], ContractSnapshot],
        contract_processor: ContractProcessor,
        node_factory: NodeFactory,
        validators_snapshot: validators.Snapshot,
    ):
        """
        Execute a transaction.

        Args:
            transaction (Transaction): The transaction to execute.
            transactions_processor (TransactionsProcessor): Instance responsible for handling transaction operations within the database.
            chain_snapshot (ChainSnapshot): Snapshot of the chain state.
            accounts_manager (AccountsManager): Manager for accounts.
            contract_snapshot_factory (Callable[[str], ContractSnapshot]): Factory function to create contract snapshots.
            node_factory (Callable[[dict, ExecutionMode, ContractSnapshot, Receipt | None, MessageHandler, Callable[[str], ContractSnapshot]], Node]): Factory function to create nodes.
        """
        # Create initial state context for the transaction
        context = TransactionContext(
            transaction=transaction,
            transactions_processor=transactions_processor,
            chain_snapshot=chain_snapshot,
            accounts_manager=accounts_manager,
            contract_snapshot_factory=contract_snapshot_factory,
            contract_processor=contract_processor,
            node_factory=node_factory,
            msg_handler=self.msg_handler,
            consensus_service=self.consensus_service,
            validators_snapshot=validators_snapshot,
            genvm_manager=self.genvm_manager,
        )

        previous_transaction = transactions_processor.get_previous_transaction(
            transaction.hash,
        )

        if (
            (previous_transaction is None)
            or (previous_transaction["appealed"])
            or (previous_transaction["appeal_undetermined"])
            or (previous_transaction["appeal_leader_timeout"])
            or (previous_transaction["appeal_validators_timeout"])
            or (
                previous_transaction["status"]
                in [
                    TransactionStatus.ACCEPTED.value,
                    TransactionStatus.UNDETERMINED.value,
                    TransactionStatus.FINALIZED.value,
                    TransactionStatus.CANCELED.value,
                    TransactionStatus.LEADER_TIMEOUT.value,
                    TransactionStatus.VALIDATORS_TIMEOUT.value,
                ]
            )
        ):
            # Begin state transitions starting from PendingState
            state = PendingState()
            while True:
                next_state = await state.handle(context)
                if next_state is None:
                    break
                elif next_state == ConsensusRound.ACCEPTED:
                    if should_rollback_after_accepted(
                        context.transaction.consensus_history
                    ):
                        await self.rollback_transactions(context)
                    break
                state = next_state

    @staticmethod
    async def dispatch_transaction_status_update(
        transactions_processor: TransactionsProcessor,
        transaction_hash: str,
        new_status: TransactionStatus,
        msg_handler: MessageHandler,
        update_current_status_changes: bool = True,
    ):
        """
        Dispatch a transaction status update asynchronously and await message delivery.
        This ensures Redis publish completes before returning, preventing delays from blocking operations.

        Args:
            transactions_processor (TransactionsProcessor): Instance responsible for handling transaction operations within the database.
            transaction_hash (str): Hash of the transaction.
            new_status (TransactionStatus): New status of the transaction.
            msg_handler (MessageHandler): Handler for messaging.
            update_current_status_changes (bool): Whether to update current status changes (default True)
        """
        # Update the transaction status in the transactions processor
        transactions_processor.update_transaction_status(
            transaction_hash,
            new_status,
            update_current_status_changes,
        )

        # Send a message indicating the transaction status update and await completion
        log_event = LogEvent(
            "transaction_status_updated",
            EventType.INFO,
            EventScope.CONSENSUS,
            f"{str(new_status.value)} {str(transaction_hash)}",
            {
                "hash": str(transaction_hash),
                "new_status": str(new_status.value),
            },
            transaction_hash=transaction_hash,
        )

        # Check if msg_handler has async send_message_async method
        if hasattr(msg_handler, "send_message_async"):
            await msg_handler.send_message_async(log_event)
        else:
            # Fallback to synchronous send_message
            msg_handler.send_message(log_event)

    @staticmethod
    async def execute_transfer(
        transaction: Transaction,
        transactions_processor: TransactionsProcessor,
        accounts_manager: AccountsManager,
        msg_handler: MessageHandler,
    ):
        """
        Executes a native token transfer between Externally Owned Accounts (EOAs).

        This function handles the transfer of native tokens from one EOA to another.
        It updates the balances of both the sender and recipient accounts, and
        manages the transaction status throughout the process.

        Args:
            transaction (dict): The transaction details including from_address, to_address, and value.
            transactions_processor (TransactionsProcessor): Instance responsible for handling transaction operations within the database.
            accounts_manager (AccountsManager): Manager to handle account balance updates.
        """

        # Idempotency guard: if the tx was already credited elsewhere (e.g. the
        # `sim_fundAccount` endpoint sets value_credited=true after crediting
        # the recipient directly), skip debit+credit to avoid double-processing.
        # This fixes a latent 2x-balance bug for faucet txs where both the
        # endpoint and this function credited the recipient.
        existing_tx = transactions_processor.get_transaction_by_hash(transaction.hash)
        if existing_tx and existing_tx.get("value_credited"):
            await ConsensusAlgorithm.dispatch_transaction_status_update(
                transactions_processor,
                transaction.hash,
                TransactionStatus.FINALIZED,
                msg_handler,
            )
            return

        # For triggered (child) transactions, the parent contract was already
        # debited at acceptance time. Skip sender debit to avoid double-debit.
        is_triggered = transaction.triggered_by_hash is not None

        if not is_triggered and transaction.from_address is not None:
            # Get the balance of the sender account
            from_balance = accounts_manager.get_account_balance(
                transaction.from_address
            )

            # Check if the sender has enough balance
            if from_balance < transaction.value:
                # UNDETERMINED is finalization-eligible: claim_next_finalization
                # filters on timestamp_awaiting_finalization IS NOT NULL.
                # Without this stamp the row strands forever (16 such rows
                # accumulated on Studio Prod over 19 days before this fix).
                transactions_processor.set_transaction_timestamp_awaiting_finalization(
                    transaction.hash
                )
                await ConsensusAlgorithm.dispatch_transaction_status_update(
                    transactions_processor,
                    transaction.hash,
                    TransactionStatus.UNDETERMINED,
                    msg_handler,
                )

                return

            # Update the balance of the sender account
            accounts_manager.update_account_balance(
                transaction.from_address, from_balance - transaction.value
            )

        if transaction.to_address is not None:
            # Get the balance of the recipient account
            to_balance = accounts_manager.get_account_balance(transaction.to_address)

            # Update the balance of the recipient account
            accounts_manager.update_account_balance(
                transaction.to_address, to_balance + transaction.value
            )

        # Mark the tx as credited so a later retry (or duplicate sync path)
        # hits the idempotency guard above and no-ops.
        if transaction.value and transaction.value > 0:
            accounts_manager.session.execute(
                text(
                    "UPDATE transactions SET value_credited = true "
                    "WHERE hash = :hash"
                ),
                {"hash": transaction.hash},
            )

        # Dispatch a transaction status update to FINALIZED
        await ConsensusAlgorithm.dispatch_transaction_status_update(
            transactions_processor,
            transaction.hash,
            TransactionStatus.FINALIZED,
            msg_handler,
        )

    def can_finalize_transaction(
        self,
        transactions_processor: TransactionsProcessor,
        transaction: Transaction,
        index: int,
        awaiting_finalization_queue: list[dict],
    ) -> bool:
        """
        Check if the transaction can be finalized based on the following criteria:
        - The transaction is in LEADER_ONLY or LEADER_SELF_VALIDATOR mode (immediate finalization)
        - The transaction has exceeded the finality window (for NORMAL mode)
        - The previous transaction has been finalized

        Args:
            transactions_processor (TransactionsProcessor): The transactions processor instance.
            transaction (Transaction): The transaction to be possibly finalized.
            index (int): The index of the current transaction in the awaiting_finalization_queue.
            awaiting_finalization_queue (list[dict]): The list of accepted and undetermined transactions for one contract.

        Returns:
            bool: True if the transaction can be finalized, False otherwise.
        """
        # Determine execution mode from transaction
        execution_mode = TransactionExecutionMode(
            transaction.execution_mode.value
            if isinstance(transaction.execution_mode, TransactionExecutionMode)
            else transaction.execution_mode
        )

        # Both LEADER_ONLY and LEADER_SELF_VALIDATOR modes finalize immediately
        immediate_finalization = execution_mode in [
            TransactionExecutionMode.LEADER_ONLY,
            TransactionExecutionMode.LEADER_SELF_VALIDATOR,
        ]

        # Check if finalization criteria are met
        decision = latest_decision_metadata(
            getattr(transaction, "consensus_history", None)
        )
        exact_deadline = None
        if decision is not None:
            try:
                exact_deadline = int(decision.get("appealDeadline") or 0)
            except (TypeError, ValueError):
                exact_deadline = None
        time_based_finalization = (
            time.time() >= exact_deadline
            if exact_deadline
            else (
                time.time()
                - transaction.timestamp_awaiting_finalization
                - transaction.appeal_processing_time
            )
            >= self.finality_window_time
            * (
                (1 - self.finality_window_appeal_failed_reduction)
                ** transaction.appeal_failed
            )
        )

        if immediate_finalization or time_based_finalization:
            if index == 0:
                return True
            else:
                previous_transaction_hash = awaiting_finalization_queue[index - 1][
                    "hash"
                ]
                previous_transaction = transactions_processor.get_transaction_by_hash(
                    previous_transaction_hash
                )
                if previous_transaction["status"] == TransactionStatus.FINALIZED.value:
                    return True
                else:
                    return False
        else:
            return False

    async def repair_accepted_message_delivery(
        self,
        transaction: Transaction,
        transactions_processor: TransactionsProcessor,
        accounts_manager: AccountsManager,
        contract_snapshot_factory: Callable[[str], ContractSnapshot],
        contract_processor: ContractProcessor,
        node_factory: NodeFactory,
    ) -> None:
        """Replay an agreed acceptance phase without finalizing the tx."""

        context = TransactionContext(
            transaction=transaction,
            transactions_processor=transactions_processor,
            chain_snapshot=None,
            accounts_manager=accounts_manager,
            contract_snapshot_factory=contract_snapshot_factory,
            contract_processor=contract_processor,
            node_factory=node_factory,
            msg_handler=self.msg_handler,
            consensus_service=self.consensus_service,
            validators_snapshot=None,
            genvm_manager=self.genvm_manager,
        )
        leader_receipt = transaction.consensus_data.leader_receipt[0]
        delivered = _dispatch_messages_for_phase(context, leader_receipt, "accepted")
        if delivered:
            await self.dispatch_transaction_status_update(
                transactions_processor,
                transaction.hash,
                TransactionStatus.ACCEPTED,
                self.msg_handler,
                update_current_status_changes=False,
            )

    async def process_finalization(
        self,
        transaction: Transaction,
        transactions_processor: TransactionsProcessor,
        chain_snapshot: ChainSnapshot | None,
        accounts_manager: AccountsManager,
        contract_snapshot_factory: Callable[[str], ContractSnapshot],
        contract_processor: ContractProcessor,
        node_factory: NodeFactory,
    ):
        """
        Process the finalization of a transaction.

        Args:
            transaction (Transaction): The transaction to finalize.
            transactions_processor (TransactionsProcessor): Instance responsible for handling transaction operations within the database.
            chain_snapshot (ChainSnapshot): Snapshot of the chain state.
            accounts_manager (AccountsManager): Manager for accounts.
            contract_snapshot_factory (Callable[[str], ContractSnapshot]): Factory function to create contract snapshots.
            node_factory (Callable[[dict, ExecutionMode, ContractSnapshot, Receipt | None, MessageHandler, Callable[[str], ContractSnapshot]], Node]): Factory function to create nodes.
        """
        # Create a transaction context for finalizing the transaction
        context = TransactionContext(
            transaction=transaction,
            transactions_processor=transactions_processor,
            chain_snapshot=chain_snapshot,
            accounts_manager=accounts_manager,
            contract_snapshot_factory=contract_snapshot_factory,
            contract_processor=contract_processor,
            node_factory=node_factory,
            msg_handler=self.msg_handler,
            consensus_service=self.consensus_service,
            validators_snapshot=None,
            genvm_manager=self.genvm_manager,
        )

        # Transition to the FinalizingState
        state = FinalizingState()
        await state.handle(context)

    async def process_leader_appeal(
        self,
        transaction: Transaction,
        transactions_processor: TransactionsProcessor,
        chain_snapshot: ChainSnapshot | None,
        accounts_manager: AccountsManager,
        contract_snapshot_factory: Callable[[str], ContractSnapshot],
        contract_processor: ContractProcessor,
        node_factory: NodeFactory,
        validators_snapshot: validators.Snapshot,
    ):
        """
        Process the leader appeal of a transaction.

        Args:
            transaction (Transaction): The transaction to appeal.
            transactions_processor (TransactionsProcessor): Instance responsible for handling transaction operations within the database.
            chain_snapshot (ChainSnapshot | None): Snapshot of the chain state (unused in worker path).
            accounts_manager (AccountsManager): Manager for accounts.
            contract_snapshot_factory (Callable[[str], ContractSnapshot]): Factory function to create contract snapshots.
            node_factory (Callable[[dict, ExecutionMode, ContractSnapshot, Receipt | None, MessageHandler, Callable[[str], ContractSnapshot]], Node]): Factory function to create nodes.
        """
        # Create a transaction context for the appeal
        context = TransactionContext(
            transaction=transaction,
            transactions_processor=transactions_processor,
            chain_snapshot=chain_snapshot,
            accounts_manager=accounts_manager,
            contract_snapshot_factory=contract_snapshot_factory,
            contract_processor=contract_processor,
            node_factory=node_factory,
            msg_handler=self.msg_handler,
            validators_snapshot=validators_snapshot,
            consensus_service=self.consensus_service,
            genvm_manager=self.genvm_manager,
        )

        consumed_addresses = ConsensusAlgorithm.get_consumed_validator_addresses(
            context.transactions_processor.get_transaction_by_hash(
                context.transaction.hash
            )["consensus_history"],
            transaction.consensus_data,
        )
        fee_accounting = (transaction.data or {}).get(FEE_ACCOUNTING_KEY) or {}
        live_validators = [
            node.validator.to_dict() for node in validators_snapshot.nodes
        ]
        selection_pool = {
            str(validator.get("address") or "").lower()
            for validator in _validators_in_frozen_selection_pool(
                live_validators,
                fee_accounting,
            )
            if validator.get("address")
        }

        logical_entries = logical_fee_round_entries(transaction.consensus_history)
        current_round = logical_entries[-1][0] if logical_entries else 0
        required_fresh = VALIDATORS_PER_ROUND[
            min(current_round + 1, len(VALIDATORS_PER_ROUND) - 1)
        ]
        available_fresh = max(
            0,
            len(selection_pool - consumed_addresses),
        )

        if available_fresh < required_fresh:
            accounts_manager.abort_tx_appeal_admission_once(
                transaction.hash,
                "appeal_committee_unavailable",
            )
            transaction.appealed = False
            self.msg_handler.send_message(
                LogEvent(
                    "consensus_event",
                    EventType.ERROR,
                    EventScope.CONSENSUS,
                    "Appeal failed, no validators found to process the appeal",
                    {
                        "transaction_hash": transaction.hash,
                    },
                    transaction_hash=transaction.hash,
                )
            )
            self.msg_handler.send_message(
                log_event=LogEvent(
                    "transaction_appeal_updated",
                    EventType.INFO,
                    EventScope.CONSENSUS,
                    "Set transaction appealed",
                    {
                        "hash": context.transaction.hash,
                    },
                ),
                log_to_terminal=False,
            )

        else:
            transactions_processor.set_transaction_appeal(transaction.hash, False)
            transaction.appealed = False
            # Appeal data member is used in the frontend for all types of appeals
            # Here the type is refined based on the status
            transactions_processor.set_transaction_appeal_undetermined(
                transaction.hash, True
            )
            transaction.appeal_undetermined = True

            # Begin state transitions starting from PendingState
            state = PendingState()
            while True:
                next_state = await state.handle(context)
                if next_state is None:
                    break
                elif next_state == ConsensusRound.LEADER_APPEAL_SUCCESSFUL:
                    await self.rollback_transactions(context)
                    break
                state = next_state

    async def process_leader_timeout_appeal(
        self,
        transaction: Transaction,
        transactions_processor: TransactionsProcessor,
        chain_snapshot: ChainSnapshot | None,
        accounts_manager: AccountsManager,
        contract_snapshot_factory: Callable[[str], ContractSnapshot],
        contract_processor: ContractProcessor,
        node_factory: NodeFactory,
        validators_snapshot: validators.Snapshot,
    ):
        """
        Handle the appeal process for a transaction that experienced a leader timeout.

        Args:
            transaction (Transaction): The transaction undergoing the appeal process.
            transactions_processor (TransactionsProcessor): Manages transaction operations within the database.
            chain_snapshot (ChainSnapshot): Represents the current state of the blockchain.
            accounts_manager (AccountsManager): Handles account-related operations.
            contract_snapshot_factory (Callable[[str], ContractSnapshot]): Function to generate contract snapshots.
            contract_processor (ContractProcessor): Responsible for processing contract-related operations.
            node_factory (Callable[[dict, ExecutionMode, ContractSnapshot, Receipt | None, MessageHandler, Callable[[str], ContractSnapshot]], Node]): Function to create nodes for processing.
            validators_snapshot (validators.Snapshot): Snapshot of the current validators' state.
        """
        # Create a transaction context for the appeal
        context = TransactionContext(
            transaction=transaction,
            transactions_processor=transactions_processor,
            chain_snapshot=chain_snapshot,
            accounts_manager=accounts_manager,
            contract_snapshot_factory=contract_snapshot_factory,
            contract_processor=contract_processor,
            node_factory=node_factory,
            msg_handler=self.msg_handler,
            validators_snapshot=validators_snapshot,
            consensus_service=self.consensus_service,
            genvm_manager=self.genvm_manager,
        )

        if context.transaction.appeal_undetermined:
            context.transactions_processor.set_transaction_appeal_undetermined(
                context.transaction.hash, False
            )
            context.transaction.appeal_undetermined = False

        if not transaction.leader_timeout_validators:
            accounts_manager.abort_tx_appeal_admission_once(
                transaction.hash,
                "appeal_committee_unavailable",
            )
            transaction.appealed = False
            self.msg_handler.send_message(
                LogEvent(
                    "consensus_event",
                    EventType.ERROR,
                    EventScope.CONSENSUS,
                    "Appeal failed, no validators found to process the appeal",
                    {
                        "transaction_hash": transaction.hash,
                    },
                    transaction_hash=transaction.hash,
                )
            )
            self.msg_handler.send_message(
                log_event=LogEvent(
                    "transaction_appeal_updated",
                    EventType.INFO,
                    EventScope.CONSENSUS,
                    "Set transaction appealed",
                    {
                        "hash": context.transaction.hash,
                    },
                ),
                log_to_terminal=False,
            )

        else:
            transactions_processor.set_transaction_appeal(transaction.hash, False)
            transaction.appealed = False
            # Appeal data member is used in the frontend for all types of appeals
            # Here the type is refined based on the status
            transaction.appeal_leader_timeout = (
                transactions_processor.set_transaction_appeal_leader_timeout(
                    transaction.hash, True
                )
            )

            # Begin state transitions starting from PendingState
            state = PendingState()
            while True:
                next_state = await state.handle(context)
                if next_state is None:
                    break
                elif next_state == ConsensusRound.LEADER_TIMEOUT_APPEAL_SUCCESSFUL:
                    await self.rollback_transactions(context)
                    break
                state = next_state

    async def process_validator_appeal(
        self,
        transaction: Transaction,
        transactions_processor: TransactionsProcessor,
        chain_snapshot: ChainSnapshot | None,
        accounts_manager: AccountsManager,
        contract_snapshot_factory: Callable[[str], ContractSnapshot],
        contract_processor: ContractProcessor,
        node_factory: NodeFactory,
        validators_snapshot: validators.Snapshot,
    ):
        """
        Process the validator appeal of a transaction.

        Args:
            transaction (Transaction): The transaction to appeal.
            transactions_processor (TransactionsProcessor): Instance responsible for handling transaction operations within the database.
            chain_snapshot (ChainSnapshot): Snapshot of the chain state.
            accounts_manager (AccountsManager): Manager for accounts.
            contract_snapshot_factory (Callable[[str], ContractSnapshot]): Factory function to create contract snapshots.
            node_factory (Callable[[dict, ExecutionMode, ContractSnapshot, Receipt | None, MessageHandler, Callable[[str], ContractSnapshot]], Node]): Factory function to create nodes.
        """
        # Create a transaction context for the appeal
        context = TransactionContext(
            transaction=transaction,
            transactions_processor=transactions_processor,
            chain_snapshot=chain_snapshot,
            accounts_manager=accounts_manager,
            contract_snapshot_factory=contract_snapshot_factory,
            contract_processor=contract_processor,
            node_factory=node_factory,
            msg_handler=self.msg_handler,
            consensus_service=self.consensus_service,
            validators_snapshot=validators_snapshot,
            genvm_manager=self.genvm_manager,
        )

        # Set the leader receipt in the context
        context.consensus_data.leader_receipt = (
            transaction.consensus_data.leader_receipt
        )
        logical_entries = logical_fee_round_entries(transaction.consensus_history)
        current_round = logical_entries[-1][0] if logical_entries else 0
        fee_accounting = (transaction.data or {}).get(FEE_ACCOUNTING_KEY) or {}
        eligible_validators = _validators_in_frozen_selection_pool(
            [x.validator.to_dict() for x in validators_snapshot.nodes],
            fee_accounting,
        )
        try:
            # Attempt to get extra validators for the appeal process
            _, context.remaining_validators = ConsensusAlgorithm.get_extra_validators(
                eligible_validators,
                transaction.consensus_history,
                transaction.consensus_data,
                transaction.appeal_failed,
                required_extra_validators=VALIDATORS_PER_ROUND[
                    min(
                        current_round + 1,
                        len(VALIDATORS_PER_ROUND) - 1,
                    )
                ],
                allow_short=True,
            )
        except ValueError as e:
            # When no validators are found, then the appeal failed
            context.msg_handler.send_message(
                LogEvent(
                    "consensus_event",
                    EventType.ERROR,
                    EventScope.CONSENSUS,
                    "Appeal failed, no validators found to process the appeal",
                    {
                        "transaction_hash": context.transaction.hash,
                        "error": str(e),
                    },
                    transaction_hash=context.transaction.hash,
                )
            )
            accounts_manager.abort_tx_appeal_admission_once(
                context.transaction.hash,
                "appeal_committee_unavailable",
            )
            context.transaction.appealed = False
            self.msg_handler.send_message(
                log_event=LogEvent(
                    "transaction_appeal_updated",
                    EventType.INFO,
                    EventScope.CONSENSUS,
                    "Set transaction appealed",
                    {
                        "hash": context.transaction.hash,
                    },
                ),
                log_to_terminal=False,
            )
            context.transactions_processor.set_transaction_appeal_processing_time(
                context.transaction.hash
            )
        else:
            # Appeal data member is used in the frontend for all types of appeals
            # Here the type is refined based on the status
            if transaction.status == TransactionStatus.VALIDATORS_TIMEOUT:
                context.transactions_processor.set_transaction_appeal(
                    context.transaction.hash, False
                )
                context.transaction.appealed = False
                transaction.appeal_validators_timeout = (
                    transactions_processor.set_transaction_appeal_validators_timeout(
                        transaction.hash, True
                    )
                )

            # Set up the context for the committing state
            context.num_validators = len(context.remaining_validators)
            context.votes = {}

            # Send events in rollup to communicate the appeal is started
            context.consensus_service.emit_transaction_event(
                "emitAppealStarted",
                context.remaining_validators[0],
                context.transaction.hash,
                context.remaining_validators[0]["address"],
                0,
                [v["address"] for v in context.remaining_validators],
            )

            # Begin state transitions starting from CommittingState
            state = CommittingState()
            while True:
                next_state = await state.handle(context)
                if next_state is None:
                    break
                elif next_state == ConsensusRound.VALIDATOR_APPEAL_SUCCESSFUL:
                    if context.transaction.appealed:
                        await self.rollback_transactions(context)

                        # Get the previous state of the contract
                        previous_contract_state = None
                        if context.transaction.contract_snapshot:
                            previous_contract_state = (
                                context.transaction.contract_snapshot.states["accepted"]
                            )
                        elif (
                            context.transaction.type == TransactionType.DEPLOY_CONTRACT
                        ):
                            # Rolling back a deploy: clear the contract state
                            previous_contract_state = {}
                        else:
                            # Defense in depth: the in-memory transaction may have
                            # been built without the stored contract_snapshot.
                            # Re-fetch it instead of clobbering the contract state
                            # with {} (which would wipe the code slot).
                            refetched = (
                                context.transactions_processor.get_transaction_by_hash(
                                    context.transaction.hash
                                )
                            )
                            refetched_snapshot = ContractSnapshot.from_dict(
                                (refetched or {}).get("contract_snapshot")
                            )
                            if refetched_snapshot:
                                previous_contract_state = refetched_snapshot.states[
                                    "accepted"
                                ]
                            else:
                                from loguru import logger

                                logger.error(
                                    f"Missing contract_snapshot for appealed "
                                    f"transaction {context.transaction.hash}; "
                                    f"skipping contract state restore"
                                )
                                # Surface to monitoring: the appeal succeeded but
                                # the contract kept the appealed transaction's
                                # state — recoverable, but needs operator eyes.
                                context.msg_handler.send_message(
                                    LogEvent(
                                        "consensus_event",
                                        EventType.ERROR,
                                        EventScope.CONSENSUS,
                                        "Missing contract_snapshot on successful "
                                        "validator appeal; contract state restore "
                                        "skipped",
                                        {
                                            "transaction_hash": context.transaction.hash,
                                            "contract_address": context.transaction.to_address,
                                        },
                                        transaction_hash=context.transaction.hash,
                                    )
                                )

                        # Restore the contract state
                        if previous_contract_state is not None:
                            context.contract_processor.update_contract_state(
                                context.transaction.to_address,
                                accepted_state=previous_contract_state,
                            )

                    # Always clear snapshot on successful appeal (including timeout appeals)
                    # so re-execution loads fresh state from DB
                    context.transactions_processor.set_transaction_contract_snapshot(
                        context.transaction.hash, None
                    )

                    # The successful review is followed by one terminal normal
                    # recomputation, not another validator-appeal pass.
                    context.transactions_processor.set_transaction_appeal(
                        context.transaction.hash, False
                    )
                    context.transaction.appealed = False
                    context.transactions_processor.set_transaction_appeal_validators_timeout(
                        context.transaction.hash, False
                    )
                    context.transaction.appeal_validators_timeout = False

                    await ConsensusAlgorithm.dispatch_transaction_status_update(
                        context.transactions_processor,
                        context.transaction.hash,
                        TransactionStatus.PENDING,
                        context.msg_handler,
                    )

                    break
                state = next_state

    async def rollback_transactions(self, context: TransactionContext):
        """
        Rollback newer transactions.
        In the simplified system, we just need to reset future transactions to PENDING.
        """
        # Set all transactions with higher created_at to PENDING
        future_transactions = context.transactions_processor.get_newer_transactions(
            context.transaction.hash
        )
        for future_transaction in future_transactions:
            await ConsensusAlgorithm.dispatch_transaction_status_update(
                context.transactions_processor,
                future_transaction["hash"],
                TransactionStatus.PENDING,
                context.msg_handler,
            )

            # Reset the contract snapshot for the transaction
            context.transactions_processor.set_transaction_contract_snapshot(
                future_transaction["hash"], None
            )

    @staticmethod
    def get_extra_validators(
        all_validators: List[dict],
        consensus_history: dict,
        consensus_data: ConsensusData,
        appeal_failed: int,
        required_extra_validators: int | None = None,
        allow_short: bool = False,
    ):
        """
        Select validators not already consumed by the transaction.

        Train callers pass ``required_extra_validators`` from the canonical
        round schedule. Validator appeals also pass ``allow_short=True`` so a
        pool with 1..K-1 fresh validators seats all of them; leader appeals
        retain the exact-size rule. The historical ``appeal_failed`` formulas
        remain only as a fallback for callers outside the train path.

        Args:
            all_validators (List[dict]): List of all validators.
            consensus_history (dict): Dictionary of consensus rounds results and status changes.
            consensus_data (ConsensusData): Data related to the consensus process.
            appeal_failed (int): Number of times the appeal has failed.
            required_extra_validators: Exact scheduled fresh jury/committee size.
            allow_short: Whether a non-empty capacity-limited selection is valid.

        Returns:
            list: List of current validators.
            list: List of extra validators.
        """
        # Get current validators and a dictionary mapping addresses to validators not used in the consensus process
        current_validators, validator_map = (
            ConsensusAlgorithm.get_validators_from_consensus_data(
                all_validators, consensus_data, False
            )
        )

        # Consensus excludes every consumed registry index from a fresh appeal
        # selection. Do not rely on consensus_data retaining every prior jury:
        # legacy rows and chained failures can leave receipts only in history.
        consumed_addresses = ConsensusAlgorithm.get_consumed_validator_addresses(
            consensus_history,
            consensus_data,
        )
        for address in list(validator_map):
            if address.lower() in consumed_addresses:
                validator_map.pop(address)

        # Set not_used_validators to the remaining validators in validator_map
        not_used_validators = list(validator_map.values())

        if len(not_used_validators) == 0:
            raise ValueError("No validators found")

        nb_current_validators = len(current_validators) + 1  # including the leader
        if required_extra_validators is not None:
            # v0.6 appeal juries are independent fresh selections. Every
            # address retained in consensus_data was consumed by an earlier
            # round; history leaders are excluded above. A validator appeal
            # may seat every remaining 1..K-1 validator, while a leader appeal
            # passes allow_short=False and therefore retains its exact K rule.
            requested = max(0, int(required_extra_validators))
            extra_validators = get_validators_for_transaction(
                not_used_validators, requested
            )
            if not allow_short and len(extra_validators) != requested:
                raise ValueError(
                    f"Not enough fresh validators: required {requested}, "
                    f"available {len(extra_validators)}"
                )
        elif appeal_failed == 0:
            # Legacy caller fallback; train callers pass the explicit schedule.
            extra_validators = get_validators_for_transaction(
                not_used_validators, nb_current_validators + 2
            )
        elif appeal_failed == 1:
            # Calculate extra validators when one appeal has failed
            n = (nb_current_validators - 2) // 2
            extra_validators = get_validators_for_transaction(
                not_used_validators, n + 1
            )
            extra_validators = current_validators[n - 1 :] + extra_validators
        else:
            # Calculate extra validators when more than one appeal has failed
            n = (nb_current_validators - 3) // (2 * appeal_failed - 1)
            extra_validators = get_validators_for_transaction(
                not_used_validators, 2 * n
            )
            extra_validators = current_validators[n - 1 :] + extra_validators

        return current_validators, extra_validators

    @staticmethod
    def get_validators_from_consensus_data(
        all_validators: List[dict], consensus_data: ConsensusData, include_leader: bool
    ):
        """
        Get validators from consensus data.

        Args:
            all_validators (List[dict]): List of all validators.
            consensus_data (ConsensusData): Data related to the consensus process.
            include_leader (bool): Whether to get the leader in the validator set.
        Returns:
            list: List of validators involved in the consensus process (can include the leader).
            dict: Dictionary mapping addresses to validators not used in the consensus process.
        """
        # Handle corrupted state where consensus_data is None
        if consensus_data is None:
            return [], {}

        # Create a dictionary to map addresses to a validator
        # Solidity addresses are values, not case-sensitive strings. Studio's
        # registry and serialized receipts can legitimately disagree only in
        # checksum casing, so every identity comparison uses one normalized
        # key.
        validator_map = {
            str(validator["address"]).lower(): validator for validator in all_validators
        }

        # Extract address of the leader from consensus data
        if include_leader:
            receipt_addresses = [
                consensus_data.leader_receipt[0].node_config["address"]
            ]
        else:
            receipt_addresses = []

        # Extract addresses of validators from consensus data
        receipt_addresses += [
            receipt.node_config["address"] for receipt in consensus_data.validators
        ]

        # Return validators whose addresses are in the receipt addresses
        validators = [
            validator_map.pop(str(receipt_address).lower())
            for receipt_address in receipt_addresses
            if str(receipt_address).lower() in validator_map
        ]

        return validators, validator_map

    @staticmethod
    def add_new_validator(
        all_validators: List[dict], validators: List[dict], leader_addresses: set[str]
    ):
        """
        Add a new validator to the list of validators.

        Args:
            all_validators (List[dict]): List of all validators.
            validators (list[dict]): List of validators.
            leader_addresses (set[str]): Set of leader addresses.

        Returns:
            list: List of validators.
        """
        # Check if there is a validator to be possibly selected
        if len(leader_addresses) + len(validators) >= len(all_validators):
            raise ValueError("No more validators found to add a new validator")

        # Extract a set of addresses of validators and leaders
        addresses = {str(validator["address"]).lower() for validator in validators}
        addresses.update(str(address).lower() for address in leader_addresses)

        # Get not used validators
        not_used_validators = [
            validator
            for validator in all_validators
            if str(validator["address"]).lower() not in addresses
        ]

        # Get new validator
        new_validator = get_validators_for_transaction(not_used_validators, 1)

        return new_validator + validators

    @staticmethod
    def get_terminal_replacement_validators(
        all_validators: List[dict], consensus_history: dict
    ) -> list[dict]:
        """Return the full terminal electorate minus prior normal leaders."""
        used_leaders = (
            ConsensusAlgorithm.get_used_leader_addresses_from_consensus_history(
                consensus_history
            )
        )
        return [
            validator
            for validator in all_validators
            if str(validator["address"]).lower() not in used_leaders
        ]

    @staticmethod
    def get_used_leader_addresses_from_consensus_history(
        consensus_history: dict, current_leader_receipt: Receipt | None = None
    ):
        """
        Get the used leader addresses from the consensus history.

        Args:
            consensus_history (dict): Dictionary of consensus rounds results and status changes.
            current_leader_receipt (Receipt | None): Current leader receipt.

        Returns:
            set[str]: Set of used leader addresses.
        """
        used_leader_addresses = set()
        if consensus_history is not None and "consensus_results" in consensus_history:
            for consensus_round in consensus_history["consensus_results"]:
                leader_receipt = consensus_round.get("leader_result") or []
                if not leader_receipt:
                    continue
                address = (
                    (leader_receipt[0].get("node_config") or {}).get("address")
                    if isinstance(leader_receipt[0], dict)
                    else None
                )
                if address:
                    used_leader_addresses.add(str(address).lower())

        # consensus_history does not contain the latest consensus_data
        if current_leader_receipt:
            used_leader_addresses.update(
                [str(current_leader_receipt.node_config["address"]).lower()]
            )

        return used_leader_addresses

    @staticmethod
    def get_consumed_validator_addresses(
        consensus_history: dict | None,
        current_consensus_data: ConsensusData | dict | None = None,
    ) -> set[str]:
        """Return every address consumed by the transaction's fresh pool."""

        consumed: set[str] = set()

        def add_receipts(value) -> None:
            receipts = value if isinstance(value, list) else [value]
            for receipt in receipts:
                if receipt is None:
                    continue
                node_config = (
                    receipt.get("node_config")
                    if isinstance(receipt, dict)
                    else getattr(receipt, "node_config", None)
                )
                if not isinstance(node_config, dict):
                    continue
                address = node_config.get("address")
                if address:
                    consumed.add(str(address).lower())

        results = (
            consensus_history.get("consensus_results")
            if isinstance(consensus_history, dict)
            else None
        )
        if isinstance(results, list):
            for entry in results:
                if not isinstance(entry, dict):
                    continue
                add_receipts(entry.get("leader_result"))
                add_receipts(entry.get("validator_results"))

        if isinstance(current_consensus_data, dict):
            add_receipts(current_consensus_data.get("leader_receipt"))
            add_receipts(current_consensus_data.get("validators"))
        elif current_consensus_data is not None:
            add_receipts(getattr(current_consensus_data, "leader_receipt", None))
            add_receipts(getattr(current_consensus_data, "validators", None))

        return consumed

    def set_finality_window_time(self, time: int):
        """
        Set the finality window time.

        Args:
            time (int): The finality window time.
        """
        self.finality_window_time = time

        # Send log event to update the frontend value
        self.msg_handler.send_message(
            LogEvent(
                name="finality_window_time_updated",
                type=EventType.INFO,
                scope=EventScope.RPC,
                message=f"Finality window time updated to {time}",
                data={"time": time},
            ),
            log_to_terminal=False,
        )


class TransactionState(ABC):
    """
    Abstract base class representing a state in the transaction process.
    """

    @abstractmethod
    async def handle(
        self, context: TransactionContext
    ) -> "TransactionState | ConsensusRound | None":
        """
        Handle the state transition.

        Args:
            context (TransactionContext): The context of the transaction.
        """


def _external_message_value_total(
    pending_transactions: Iterable[PendingTransaction],
) -> int:
    return sum(
        int(pending_transaction.value or 0)
        for pending_transaction in pending_transactions
        if pending_transaction.is_eth_send and int(pending_transaction.value or 0) > 0
    )


def _external_message_value_for_phase(
    pending_transactions: Iterable[PendingTransaction],
    on: Literal["accepted", "finalized"],
) -> int:
    return sum(
        int(pending_transaction.value or 0)
        for pending_transaction in pending_transactions
        if pending_transaction.is_eth_send
        and pending_transaction.on == on
        and int(pending_transaction.value or 0) > 0
    )


def _use_balance_obligation_total(
    pending_transactions: Iterable[PendingTransaction],
) -> tuple[int, int]:
    fee_budget = 0
    value = 0
    for pending_transaction in pending_transactions:
        if pending_transaction.is_eth_send or not pending_transaction.use_balance:
            continue
        fee_budget += max(0, int(pending_transaction.declared_budget or 0))
        value += max(0, int(pending_transaction.value or 0))
    return fee_budget, value


def _use_balance_obligation_for_phase(
    pending_transactions: Iterable[PendingTransaction],
    on: Literal["accepted", "finalized"],
) -> int:
    return sum(
        max(0, int(pending_transaction.declared_budget or 0))
        + max(0, int(pending_transaction.value or 0))
        for pending_transaction in pending_transactions
        if not pending_transaction.is_eth_send
        and pending_transaction.use_balance
        and pending_transaction.on == on
    )


def _apply_external_message_freeze_check(
    context: TransactionContext,
    leader_receipt: Receipt,
) -> None:
    if leader_receipt.execution_result != ExecutionResultStatus.SUCCESS:
        return

    pending_transactions = _novel_pending_transactions(
        context,
        leader_receipt.pending_transactions,
    )
    external_value = _external_message_value_total(pending_transactions)
    use_balance_fee, use_balance_value = _use_balance_obligation_total(
        pending_transactions
    )
    declared_value = external_value + use_balance_fee + use_balance_value
    if declared_value <= 0:
        return

    other_reserved = _external_message_pending_freeze_total(context)
    balance = context.accounts_manager.get_account_balance(
        context.transaction.to_address
    )
    available = max(balance - other_reserved, 0)
    if declared_value <= available:
        return

    error_name = (
        "ExternalMessageFreezeExceeded"
        if use_balance_fee + use_balance_value == 0
        else "ContractMessageFreezeExceeded"
    )
    error_message = (
        f"{error_name}: declaredValue={declared_value}, availableLimit={available}"
    )
    leader_receipt.execution_result = ExecutionResultStatus.ERROR
    leader_receipt.result = bytes([ResultCode.VM_ERROR]) + error_message.encode("utf-8")
    leader_receipt.contract_state = {}
    leader_receipt.contract_state_hash = None
    leader_receipt.pending_transactions = []
    leader_receipt.genvm_result = {
        **(leader_receipt.genvm_result or {}),
        "error_code": (
            "EXTERNAL_MESSAGE_FREEZE_EXCEEDED"
            if use_balance_fee + use_balance_value == 0
            else "CONTRACT_MESSAGE_FREEZE_EXCEEDED"
        ),
        "error_description": error_message,
        "external_message_freeze": {
            "declaredValue": declared_value,
            "availableLimit": available,
            "balance": balance,
            "reservedExternal": other_reserved,
            **(
                {
                    "externalValue": external_value,
                    "useBalanceFee": use_balance_fee,
                    "useBalanceValue": use_balance_value,
                }
                if use_balance_fee + use_balance_value > 0
                else {}
            ),
        },
    }


def _internal_message_value_for_phase(
    pending_transactions: Iterable[PendingTransaction],
    on: Literal["accepted", "finalized"],
) -> int:
    return sum(
        int(pending_transaction.value or 0)
        for pending_transaction in pending_transactions
        if not pending_transaction.is_eth_send
        and pending_transaction.on == on
        and int(pending_transaction.value or 0) > 0
    )


def _remaining_external_freeze_after_phase(
    context: TransactionContext,
    pending_transactions: Iterable[PendingTransaction],
    on: Literal["accepted", "finalized"],
) -> int:
    pending_freeze = _external_message_pending_freeze_total(context)
    if on == "finalized":
        return pending_freeze

    return (
        pending_freeze
        + _external_message_value_for_phase(pending_transactions, "finalized")
        + _use_balance_obligation_for_phase(pending_transactions, "finalized")
    )


def _external_message_pending_freeze_total(context: TransactionContext) -> int:
    contract_address = context.transaction.to_address
    if not contract_address or not hasattr(context.transactions_processor, "session"):
        return 0

    current_queue_order = (
        context.transactions_processor.session.query(
            Transactions.queue_order,
        )
        .filter(Transactions.hash == context.transaction.hash)
        .scalar()
    )
    filters = [
        Transactions.to_address == contract_address,
        Transactions.status == TransactionStatus.ACCEPTED,
        Transactions.hash != context.transaction.hash,
        Transactions.consensus_data.isnot(None),
    ]
    if current_queue_order is not None:
        filters.append(Transactions.queue_order < int(current_queue_order))

    rows = (
        context.transactions_processor.session.query(
            Transactions.hash,
            Transactions.consensus_data,
        )
        .filter(*filters)
        .all()
    )

    total = 0
    for row in rows:
        for receipt in _leader_receipts_from_consensus_data(row.consensus_data):
            if (
                _receipt_execution_result(receipt)
                != ExecutionResultStatus.SUCCESS.value
            ):
                continue
            total += _external_message_value_for_phase_from_raw(
                _receipt_pending_transactions(receipt),
                "finalized",
            )
            total += _use_balance_obligation_for_phase_from_raw(
                _receipt_pending_transactions(receipt),
                "finalized",
            )
    return total


def _leader_receipts_from_consensus_data(consensus_data: Any) -> list[Any]:
    if isinstance(consensus_data, ConsensusData):
        leader_receipt = consensus_data.leader_receipt
        if isinstance(leader_receipt, list):
            return leader_receipt[:1]
        if leader_receipt:
            return [leader_receipt]
        return []

    if not isinstance(consensus_data, dict):
        return []

    leader_receipt = consensus_data.get("leader_receipt")
    if isinstance(leader_receipt, list):
        return leader_receipt[:1]
    if isinstance(leader_receipt, dict):
        return [leader_receipt]
    return []


def _receipt_execution_result(receipt: Any) -> str | None:
    if isinstance(receipt, Receipt):
        return receipt.execution_result.value
    if isinstance(receipt, dict):
        return receipt.get("execution_result")
    return None


def _receipt_pending_transactions(receipt: Any) -> Iterable[Any]:
    if isinstance(receipt, Receipt):
        return receipt.pending_transactions
    if isinstance(receipt, dict):
        return receipt.get("pending_transactions") or []
    return []


def _external_message_value_for_phase_from_raw(
    pending_transactions: Iterable[Any],
    on: Literal["accepted", "finalized"],
) -> int:
    return sum(
        _pending_transaction_external_value(pending_transaction, on)
        for pending_transaction in pending_transactions
    )


def _pending_transaction_external_value(
    pending_transaction: Any,
    on: Literal["accepted", "finalized"],
) -> int:
    if isinstance(pending_transaction, PendingTransaction):
        if not pending_transaction.is_eth_send or pending_transaction.on != on:
            return 0
        return int(pending_transaction.value or 0)

    if not isinstance(pending_transaction, dict):
        return 0

    is_external = bool(
        pending_transaction.get("is_eth_send")
        or pending_transaction.get("isEthSend")
        or pending_transaction.get("messageType") in {0, "0", "External", "external"}
    )
    if not is_external:
        return 0

    pending_on = pending_transaction.get("on")
    if pending_on is None and "onAcceptance" in pending_transaction:
        pending_on = (
            "accepted" if pending_transaction.get("onAcceptance") else "finalized"
        )
    if pending_on != on:
        return 0

    return int(pending_transaction.get("value", 0) or 0)


def _use_balance_obligation_for_phase_from_raw(
    pending_transactions: Iterable[Any],
    on: Literal["accepted", "finalized"],
) -> int:
    total = 0
    for pending_transaction in pending_transactions:
        if isinstance(pending_transaction, PendingTransaction):
            if (
                pending_transaction.is_eth_send
                or not pending_transaction.use_balance
                or pending_transaction.on != on
            ):
                continue
            total += max(0, int(pending_transaction.declared_budget or 0))
            total += max(0, int(pending_transaction.value or 0))
            continue

        if not isinstance(pending_transaction, dict):
            continue
        message_type = pending_transaction.get(
            "message_type", pending_transaction.get("messageType")
        )
        if message_type in {0, "0", "External", "external"}:
            continue
        if not bool(
            pending_transaction.get(
                "use_balance", pending_transaction.get("useBalance", False)
            )
        ):
            continue
        pending_on = pending_transaction.get("on")
        if pending_on is None and "onAcceptance" in pending_transaction:
            pending_on = (
                "accepted" if pending_transaction.get("onAcceptance") else "finalized"
            )
        if pending_on != on:
            continue
        total += max(
            0,
            int(
                pending_transaction.get(
                    "declared_budget",
                    pending_transaction.get("declaredBudget", 0),
                )
                or 0
            ),
        )
        total += max(0, int(pending_transaction.get("value", 0) or 0))
    return total


def _pending_transaction_with_value(
    pending_transaction: PendingTransaction,
    value: int,
) -> PendingTransaction:
    adjusted = deepcopy(pending_transaction)
    adjusted.value = value
    return adjusted


def _debit_external_message_value_for_phase(
    context: TransactionContext,
    pending_transactions: list[PendingTransaction],
    on: Literal["accepted", "finalized"],
) -> bool:
    external_value = _external_message_value_for_phase(pending_transactions, on)
    if external_value <= 0:
        return True

    debited = context.accounts_manager.debit_account_balance(
        context.transaction.to_address, external_value
    )
    if not debited:
        _log_message_value_debit_failure(
            context,
            on,
            external_value,
            "external",
            "Skipping value-bearing external child emission.",
        )
    return debited


def _debit_internal_message_value_for_phase(
    context: TransactionContext,
    pending_transactions: list[PendingTransaction],
    on: Literal["accepted", "finalized"],
) -> bool:
    internal_value = sum(
        int(pending_transaction.value or 0)
        for pending_transaction in pending_transactions
        if not pending_transaction.is_eth_send
        and not pending_transaction.use_balance
        and pending_transaction.on == on
        and int(pending_transaction.value or 0) > 0
    )
    if internal_value <= 0:
        return True

    internal_cap = _internal_message_value_cap(context, pending_transactions, on)
    if internal_value > internal_cap:
        _log_internal_message_value_cap_failure(
            context,
            on,
            internal_value,
            internal_cap,
            pending_transactions,
        )
        return False

    debited = context.accounts_manager.debit_account_balance(
        context.transaction.to_address, internal_value
    )
    if not debited:
        _log_message_value_debit_failure(
            context,
            on,
            internal_value,
            "internal",
            "Emitting internal children with value=0.",
        )
    return debited


def _debit_use_balance_funding_for_phase(
    context: TransactionContext,
    pending_transactions: list[PendingTransaction],
    on: Literal["accepted", "finalized"],
) -> bool:
    obligation = _use_balance_obligation_for_phase(pending_transactions, on)
    if obligation <= 0:
        return True

    available = _internal_message_value_cap(context, pending_transactions, on)
    if obligation > available:
        _log_message_value_debit_failure(
            context,
            on,
            obligation,
            "useBalance internal",
            "Emitting the child without contract-funded value or fees.",
        )
        return False

    debited = context.accounts_manager.debit_account_balance(
        context.transaction.to_address, obligation
    )
    if not debited:
        _log_message_value_debit_failure(
            context,
            on,
            obligation,
            "useBalance internal",
            "Emitting the child without contract-funded value or fees.",
        )
    return debited


def _internal_message_value_cap(
    context: TransactionContext,
    pending_transactions: list[PendingTransaction],
    on: Literal["accepted", "finalized"],
) -> int:
    frozen_after_phase = _remaining_external_freeze_after_phase(
        context, pending_transactions, on
    )
    balance_after_external = context.accounts_manager.get_account_balance(
        context.transaction.to_address
    )
    return max(balance_after_external - frozen_after_phase, 0)


def _log_internal_message_value_cap_failure(
    context: TransactionContext,
    on: Literal["accepted", "finalized"],
    amount: int,
    available: int,
    pending_transactions: list[PendingTransaction],
) -> None:
    from loguru import logger

    reserved_external = _remaining_external_freeze_after_phase(
        context, pending_transactions, on
    )
    logger.error(
        f"Contract internal message value is not backed for {context.transaction.to_address}, "
        f"phase={on}, amount={amount}, available={available}, "
        f"reserved_external={reserved_external}, tx={context.transaction.hash}. "
        f"Emitting internal children with value=0."
    )


def _log_message_value_debit_failure(
    context: TransactionContext,
    on: Literal["accepted", "finalized"],
    amount: int,
    message_kind: str,
    consequence: str,
) -> None:
    from loguru import logger

    logger.error(
        f"Contract {message_kind} message debit failed for {context.transaction.to_address}, "
        f"phase={on}, amount={amount}, tx={context.transaction.hash}. {consequence}"
    )


def _adjust_unbacked_message_values(
    pending_transactions: list[PendingTransaction],
    on: Literal["accepted", "finalized"],
    *,
    external_value_backed: bool,
    internal_value_backed: bool,
    use_balance_funding_backed: bool,
) -> list[PendingTransaction]:
    adjusted_pending_transactions = []
    for pending_transaction in pending_transactions:
        if (
            pending_transaction.on == on
            and pending_transaction.use_balance
            and not use_balance_funding_backed
        ):
            unfunded = _pending_transaction_with_value(pending_transaction, 0)
            unfunded.declared_budget = 0
            adjusted_pending_transactions.append(unfunded)
            continue
        value = int(pending_transaction.value or 0)
        if pending_transaction.on == on and value > 0:
            if pending_transaction.is_eth_send and not external_value_backed:
                continue
            if not pending_transaction.is_eth_send and not internal_value_backed:
                adjusted_pending_transactions.append(
                    _pending_transaction_with_value(pending_transaction, 0)
                )
                continue

        adjusted_pending_transactions.append(pending_transaction)

    return adjusted_pending_transactions


def _apply_untracked_message_value_withdrawals_for_phase(
    context: TransactionContext,
    pending_transactions: Iterable[PendingTransaction],
    on: Literal["accepted", "finalized"],
) -> list[PendingTransaction]:
    pending_list = list(pending_transactions)
    external_value_backed = _debit_external_message_value_for_phase(
        context, pending_list, on
    )
    use_balance_funding_backed = _debit_use_balance_funding_for_phase(
        context, pending_list, on
    )
    internal_value_backed = _debit_internal_message_value_for_phase(
        context, pending_list, on
    )

    if external_value_backed and internal_value_backed and use_balance_funding_backed:
        return pending_list

    return _adjust_unbacked_message_values(
        pending_list,
        on,
        external_value_backed=external_value_backed,
        internal_value_backed=internal_value_backed,
        use_balance_funding_backed=use_balance_funding_backed,
    )


def _message_value_effect_record(
    pending_transaction: PendingTransaction,
    descriptor: str,
    on: Literal["accepted", "finalized"],
    *,
    external_value_backed: bool,
    internal_value_backed: bool,
    use_balance_funding_backed: bool,
) -> dict[str, Any]:
    value = int(pending_transaction.value or 0)
    include = True
    adjusted_value = value
    adjusted_budget = int(pending_transaction.declared_budget or 0)

    if pending_transaction.use_balance and not use_balance_funding_backed:
        adjusted_value = 0
        adjusted_budget = 0
    elif value > 0 and pending_transaction.is_eth_send and not external_value_backed:
        include = False
    elif (
        value > 0 and not pending_transaction.is_eth_send and not internal_value_backed
    ):
        adjusted_value = 0

    return {
        "descriptor": descriptor,
        "phase": on,
        "include": include,
        "value": adjusted_value,
        "declaredBudget": adjusted_budget,
    }


def _apply_recorded_message_value_effects(
    pending_transactions: list[PendingTransaction],
    identities: list[tuple[str, str]],
    records: dict[str, Any],
    on: Literal["accepted", "finalized"],
) -> list[PendingTransaction]:
    adjusted: list[PendingTransaction] = []
    for pending_transaction, (occurrence, descriptor) in zip(
        pending_transactions, identities
    ):
        if pending_transaction.on != on:
            adjusted.append(pending_transaction)
            continue

        record = records.get(occurrence)
        if not isinstance(record, dict):
            raise RuntimeError(f"MessageValueEffectMissing({occurrence})")
        if record.get("descriptor") != descriptor or record.get("phase") != on:
            raise RuntimeError(
                "MessageValueEffectDescriptorMismatch"
                f"({occurrence},{record.get('descriptor')},{descriptor})"
            )
        if not bool(record.get("include", False)):
            continue

        value = int(record.get("value", 0) or 0)
        declared_budget = int(record.get("declaredBudget", 0) or 0)
        if value == int(pending_transaction.value or 0) and declared_budget == int(
            pending_transaction.declared_budget or 0
        ):
            adjusted.append(pending_transaction)
            continue

        replayed = _pending_transaction_with_value(pending_transaction, value)
        replayed.declared_budget = declared_budget
        adjusted.append(replayed)
    return adjusted


def _apply_message_value_withdrawals_for_phase(
    context: TransactionContext,
    pending_transactions: Iterable[PendingTransaction],
    on: Literal["accepted", "finalized"],
) -> list[PendingTransaction]:
    """Reserve message value once and replay the exact result after a crash.

    Studio's account database and helper EVM cannot share one atomic
    transaction. The reservation therefore lives in the parent's locked fee
    accounting row: a worker may retry the helper call, but it cannot debit the
    contract a second time or change an earlier insufficient-balance outcome.
    """

    pending_list = list(pending_transactions)
    parent_fee_accounting = (getattr(context.transaction, "data", None) or {}).get(
        FEE_ACCOUNTING_KEY
    )
    mutate_accounting = getattr(
        context.transactions_processor,
        "mutate_transaction_fee_accounting",
        None,
    )
    if not isinstance(parent_fee_accounting, dict) or not callable(mutate_accounting):
        return _apply_untracked_message_value_withdrawals_for_phase(
            context, pending_list, on
        )

    def reserve_latest(current_fee_accounting: dict[str, Any]) -> dict[str, Any]:
        updated = deepcopy(current_fee_accounting)
        payloads = _reveal_message_fee_payloads(updated, pending_list)
        identities = message_effect_identities(context.transaction.hash, payloads)
        records = updated.setdefault("message_value_effects", {})

        unrecorded_indexes: set[int] = set()
        for index, (pending_transaction, (occurrence, descriptor)) in enumerate(
            zip(pending_list, identities)
        ):
            if pending_transaction.on != on:
                continue
            record = records.get(occurrence)
            if record is None:
                unrecorded_indexes.add(index)
                continue
            if (
                not isinstance(record, dict)
                or record.get("descriptor") != descriptor
                or record.get("phase") != on
            ):
                raise RuntimeError(
                    "MessageValueEffectDescriptorMismatch"
                    f"({occurrence},"
                    f"{record.get('descriptor') if isinstance(record, dict) else None},"
                    f"{descriptor})"
                )

        if unrecorded_indexes:
            debit_candidates = [
                pending_transaction
                for index, pending_transaction in enumerate(pending_list)
                if pending_transaction.on != on or index in unrecorded_indexes
            ]
            external_value_backed = _debit_external_message_value_for_phase(
                context, debit_candidates, on
            )
            use_balance_funding_backed = _debit_use_balance_funding_for_phase(
                context, debit_candidates, on
            )
            internal_value_backed = _debit_internal_message_value_for_phase(
                context, debit_candidates, on
            )

            for index in unrecorded_indexes:
                pending_transaction = pending_list[index]
                occurrence, descriptor = identities[index]
                records[occurrence] = _message_value_effect_record(
                    pending_transaction,
                    descriptor,
                    on,
                    external_value_backed=external_value_backed,
                    internal_value_backed=internal_value_backed,
                    use_balance_funding_backed=use_balance_funding_backed,
                )

        return updated

    updated_accounting = mutate_accounting(
        context.transaction.hash,
        reserve_latest,
        commit=False,
    )
    context.transaction.data = dict(context.transaction.data or {})
    context.transaction.data[FEE_ACCOUNTING_KEY] = updated_accounting
    payloads = _reveal_message_fee_payloads(updated_accounting, pending_list)
    identities = message_effect_identities(context.transaction.hash, payloads)
    return _apply_recorded_message_value_effects(
        pending_list,
        identities,
        updated_accounting.get("message_value_effects") or {},
        on,
    )


def _pending_valid_until_expired(
    transaction: Transaction,
    *,
    now: float | None = None,
) -> bool:
    # Appeals/recomputation reuse Studio's PENDING state after the transaction
    # has already activated. Consensus deliberately ignores validUntil on that
    # re-entry path (the original activation deadline is not a lifetime cap).
    if (
        bool(getattr(transaction, "consensus_history", None))
        or bool(getattr(transaction, "appealed", False))
        or bool(getattr(transaction, "appeal_undetermined", False))
        or bool(getattr(transaction, "appeal_leader_timeout", False))
        or bool(getattr(transaction, "appeal_validators_timeout", False))
    ):
        return False
    valid_until = int((transaction.data or {}).get("valid_until", 0) or 0)
    # EVM block timestamps are integer seconds; equality remains valid.
    return valid_until > 0 and int(time.time() if now is None else now) > valid_until


class PendingState(TransactionState):
    """
    Class representing the pending state of a transaction.
    """

    async def handle(self, context):
        # Refresh transaction from DB FIRST. The claim-path dict that built
        # context.transaction omits columns whose Transaction.from_dict
        # defaults are silently wrong for re-processed appeals (e.g.
        # appeal_undetermined/appeal_leader_timeout default to False) — the
        # same drift class as the PR #1724 state wipe. Nothing in this state
        # may read claim-built appeal fields before this refresh.
        context.transaction = Transaction.from_dict(
            context.transactions_processor.get_transaction_by_hash(
                context.transaction.hash
            )
        )
        context.active_fee_round = _active_execution_fee_round(context.transaction)

        # v0.6 validUntil is an activation deadline. Consensus leaves an
        # expired transaction queued until it reaches the pending head, then
        # terminalizes it without activating or executing it. Studio used to
        # decode and persist this field but ignored it entirely.
        if _pending_valid_until_expired(context.transaction):
            value_sender = context.transaction.from_address
            if value_sender:
                context.accounts_manager.refund_tx_value(
                    context.transaction.hash,
                    value_sender,
                )
            fee_accounting = (context.transaction.data or {}).get(FEE_ACCOUNTING_KEY)
            fee_sender = (
                fee_accounting.get("sender")
                if isinstance(fee_accounting, dict)
                else None
            ) or value_sender
            if fee_sender:
                context.accounts_manager.cancel_tx_fee_accounting_once(
                    context.transaction.hash,
                    fee_sender,
                    "valid_until_expired",
                )
            await ConsensusAlgorithm.dispatch_transaction_status_update(
                context.transactions_processor,
                context.transaction.hash,
                TransactionStatus.CANCELED,
                context.msg_handler,
            )
            return None

        # Pre-effects: timestamp + reset rotation count
        pre_effects = decide_pending_pre(
            tx_hash=context.transaction.hash,
            appeal_leader_timeout=context.transaction.appeal_leader_timeout,
            appeal_undetermined=context.transaction.appeal_undetermined,
        )
        await EffectExecutor(context).execute(pre_effects)

        # Consensus reserves the time-unit pool at the user's cap during
        # submission, then locks live execution prices only when the queued
        # transaction first activates. A live GEN price above the cap cancels
        # with a full value/fee refund; storage and receipt prices simply lock.
        fee_accounting = (context.transaction.data or {}).get(FEE_ACCOUNTING_KEY)
        if fee_accounting:
            activation_result: dict[str, bool] = {}

            def activate_latest(current_fee_accounting):
                updated, should_cancel = activate_fee_accounting(
                    current_fee_accounting,
                    StudioFeePolicy.from_env(),
                    selection_pool_count=(
                        len(context.validators_snapshot.nodes)
                        if context.validators_snapshot is not None
                        else None
                    ),
                    selection_pool_addresses=(
                        [
                            node.validator.address
                            for node in context.validators_snapshot.nodes
                        ]
                        if context.validators_snapshot is not None
                        else None
                    ),
                )
                activation_result["should_cancel"] = should_cancel
                return updated

            activated_accounting = (
                context.transactions_processor.mutate_transaction_fee_accounting(
                    context.transaction.hash,
                    activate_latest,
                )
            )
            should_cancel = activation_result["should_cancel"]
            context.transaction.data = {
                **(context.transaction.data or {}),
                FEE_ACCOUNTING_KEY: activated_accounting,
            }
            if should_cancel:
                value_sender = context.transaction.from_address
                if value_sender:
                    context.accounts_manager.refund_tx_value(
                        context.transaction.hash,
                        value_sender,
                    )
                fee_sender = activated_accounting.get("sender") or value_sender
                if fee_sender:
                    context.accounts_manager.cancel_tx_fee_accounting_once(
                        context.transaction.hash,
                        fee_sender,
                        "activation_gen_price_cap_exceeded",
                    )
                await ConsensusAlgorithm.dispatch_transaction_status_update(
                    context.transactions_processor,
                    context.transaction.hash,
                    TransactionStatus.CANCELED,
                    context.msg_handler,
                )
                return None

        # Log executing message (unless appeal)
        if (
            not context.transaction.appeal_leader_timeout
            and not context.transaction.appeal_undetermined
        ):
            context.msg_handler.send_message(
                LogEvent(
                    "consensus_event",
                    EventType.INFO,
                    EventScope.CONSENSUS,
                    "Executing transaction",
                    {
                        "transaction_hash": context.transaction.hash,
                        "transaction": _redact_transaction_for_log(
                            context.transaction.to_dict()
                        ),
                    },
                    transaction_hash=context.transaction.hash,
                )
            )

        # Transfer transactions
        if context.transaction.type == TransactionType.SEND:
            await ConsensusAlgorithm.execute_transfer(
                context.transaction,
                context.transactions_processor,
                context.accounts_manager,
                context.msg_handler,
            )
            return None

        # Get all validators BEFORE crediting — if no validators, tx will be
        # canceled and we must not credit (otherwise refund_tx_value can't refund)
        if context.validators_snapshot is None:
            all_validators = None
        else:
            all_validators = [
                n.validator.to_dict() for n in context.validators_snapshot.nodes
            ]

        # Consensus executes every later round against the activation-pinned
        # selection pool. Validators added afterwards are not eligible; a
        # removed validator is simply unavailable and can reduce capacity.
        fee_accounting = (context.transaction.data or {}).get(FEE_ACCOUNTING_KEY)
        if all_validators:
            all_validators = _validators_in_frozen_selection_pool(
                all_validators,
                fee_accounting,
            )

        if not all_validators:
            context.msg_handler.send_message(
                LogEvent(
                    "consensus_event",
                    EventType.ERROR,
                    EventScope.CONSENSUS,
                    "No validators found to process transaction",
                    {"transaction_hash": context.transaction.hash},
                    transaction_hash=context.transaction.hash,
                )
            )
            raise NoValidatorsAvailableError(
                f"No validators available for transaction {context.transaction.hash}"
            )

        # Validator selection (impure: VRF, DB reads, static methods)
        if (
            context.transaction.appealed
            or context.transaction.appeal_validators_timeout
        ):
            if context.transaction.consensus_data is not None:
                context.involved_validators, _ = (
                    ConsensusAlgorithm.get_validators_from_consensus_data(
                        all_validators, context.transaction.consensus_data, False
                    )
                )
            else:
                context.involved_validators = get_validators_for_transaction(
                    all_validators, context.transaction.num_of_initial_validators
                )

            context.transactions_processor.set_transaction_appeal(
                context.transaction.hash, False
            )
            context.transaction.appealed = False
            context.transaction.appeal_validators_timeout = context.transactions_processor.set_transaction_appeal_validators_timeout(
                context.transaction.hash, False
            )

        elif context.transaction.appeal_undetermined:
            if context.transaction.consensus_data is None:
                context.transactions_processor.set_transaction_appeal_undetermined(
                    context.transaction.hash, False
                )
                context.transaction.appeal_undetermined = False
                context.involved_validators = get_validators_for_transaction(
                    all_validators, context.transaction.num_of_initial_validators
                )
            else:
                current_validators, extra_validators = (
                    ConsensusAlgorithm.get_extra_validators(
                        all_validators,
                        context.transaction.consensus_history,
                        context.transaction.consensus_data,
                        0,
                        required_extra_validators=VALIDATORS_PER_ROUND[
                            min(
                                max(0, context.active_fee_round - 1),
                                len(VALIDATORS_PER_ROUND) - 1,
                            )
                        ],
                    )
                )
                context.involved_validators = current_validators + extra_validators

                context.consensus_service.emit_transaction_event(
                    "emitAppealStarted",
                    context.involved_validators[0],
                    context.transaction.hash,
                    context.involved_validators[0]["address"],
                    0,
                    [v["address"] for v in context.involved_validators],
                )

        elif context.transaction.appeal_leader_timeout:
            # Consensus removes the timed-out leader and replays with the
            # surviving committee; it does not select a replacement seat.
            context.involved_validators = list(
                context.transaction.leader_timeout_validators
            )

        else:
            if context.transaction.consensus_data:
                history_entries = logical_fee_round_entries(
                    context.transaction.consensus_history
                )
                last_outcome = (
                    str(history_entries[-1][1].get("consensus_round") or "")
                    if history_entries
                    else ""
                )
                if last_outcome in TERMINAL_VALIDATOR_APPEAL_ROUNDS:
                    # A successful validator review is followed by one
                    # terminal normal recomputation over the full frozen
                    # electorate, excluding only prior normal-round leaders.
                    # Prior validators and jurors are deliberately eligible
                    # again; this is not another fresh-jury selection.
                    context.involved_validators = (
                        ConsensusAlgorithm.get_terminal_replacement_validators(
                            all_validators,
                            context.transaction.consensus_history,
                        )
                    )
                else:
                    context.involved_validators, _ = (
                        ConsensusAlgorithm.get_validators_from_consensus_data(
                            all_validators, context.transaction.consensus_data, True
                        )
                    )
                if not context.involved_validators:
                    context.msg_handler.send_message(
                        LogEvent(
                            "consensus_event",
                            EventType.WARNING,
                            EventScope.CONSENSUS,
                            "Original validators not found for rolled-back transaction, selecting new validators",
                            {"transaction_hash": context.transaction.hash},
                            transaction_hash=context.transaction.hash,
                        )
                    )
                    context.transaction.consensus_data = None
                    context.involved_validators = get_validators_for_transaction(
                        all_validators,
                        context.transaction.num_of_initial_validators,
                    )
            else:
                context.involved_validators = get_validators_for_transaction(
                    all_validators, context.transaction.num_of_initial_validators
                )

        # Credit target contract on activation (value from transaction)
        # Placed AFTER validator check — if no validators, tx gets canceled
        # and refund_tx_value must be able to refund (requires value_credited=false)
        tx_value = int(context.transaction.value or 0)
        if tx_value > 0:
            credited = context.accounts_manager.credit_tx_value_once(
                context.transaction.hash,
                context.transaction.to_address,
                tx_value,
            )
            if credited and context.contract_snapshot is not None:
                context.contract_snapshot.balance = (
                    context.accounts_manager.get_account_balance(
                        context.transaction.to_address
                    )
                )

        activate = decide_pending_activate(
            appeal_undetermined=context.transaction.appeal_undetermined,
            appeal_leader_timeout=context.transaction.appeal_leader_timeout,
        )
        return ProposingState(activate=activate)


class ProposingState(TransactionState):
    """
    Class representing the proposing state of a transaction.
    """

    def __init__(self, activate: bool = False):
        self.activate = activate

    async def handle(self, context):
        # The leader is elected randomly
        random.shuffle(context.involved_validators)

        # Unpack the leader and validators
        [context.leader, *context.remaining_validators] = context.involved_validators

        # Determine execution mode and handle validator selection accordingly
        execution_mode = TransactionExecutionMode(
            context.transaction.execution_mode.value
            if isinstance(context.transaction.execution_mode, TransactionExecutionMode)
            else context.transaction.execution_mode
        )

        # For non-NORMAL modes, clear validators (leader handles everything)
        if execution_mode != TransactionExecutionMode.NORMAL:
            context.remaining_validators = []

        # Pre-execution effects (timestamp, status, optional activation event)
        pre_effects = prepare_proposing(
            tx_hash=context.transaction.hash,
            activate=self.activate,
            leader=context.leader,
            remaining_validators=context.remaining_validators,
        )
        executor = EffectExecutor(context)
        await executor.execute(pre_effects)

        context.transactions_processor.add_state_timestamp(
            context.transaction.hash, "PROPOSING.VALIDATORS_SELECTED"
        )

        assert context.validators_snapshot is not None

        # Create timing callback for leader execution
        def leader_timing_callback(step_name: str):
            context.transactions_processor.add_state_timestamp(
                context.transaction.hash, f"PROPOSING.LEADER.{step_name}"
            )

        # Execute leader with one wall-clock slot budget. Fatal internal
        # failures can still use replacements while budget remains.
        leader_budget_seconds = _slot_budget_seconds(context.transaction, "leader")
        leader_deadline = asyncio.get_running_loop().time() + leader_budget_seconds

        def _build_leader_timeout_receipt(
            leader_dict: dict,
            *,
            detail: str | None = None,
        ) -> Receipt:
            timeout_ms = int(leader_budget_seconds * 1000)
            return Receipt(
                result=bytes([ResultCode.VM_ERROR]) + b"timeout",
                calldata=b"",
                gas_used=0,
                mode=ExecutionMode.LEADER,
                contract_state={},
                node_config=leader_dict,
                execution_result=ExecutionResultStatus.ERROR,
                vote=None,
                genvm_result={
                    "stdout": "",
                    "stderr": detail
                    or f"Leader execution exceeded {leader_budget_seconds:.3f}s",
                    "error_code": "CONSENSUS_LEADER_EXEC_TIMEOUT",
                    "raw_error": {
                        "causes": ["LEADER_EXEC_TIMEOUT"],
                        "fatal": False,
                    },
                },
                processing_time=timeout_ms,
            )

        for attempt in range(MAX_IDLE_REPLACEMENTS + 1):
            remaining = leader_deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                context.consensus_data.leader_receipt = [
                    _build_leader_timeout_receipt(context.leader)
                ]
                break

            leader_node = context.node_factory(
                context.leader,
                ExecutionMode.LEADER,
                deepcopy(context.contract_snapshot),
                None,
                context.msg_handler,
                context.contract_snapshot_factory,
                context.validators_snapshot,
                leader_timing_callback,
                context.genvm_manager,
                context.shared_decoded_value_cache,
                context.shared_contract_snapshot_cache,
            )

            context.transactions_processor.add_state_timestamp(
                context.transaction.hash,
                f"PROPOSING.LEADER_NODE_CREATED.attempt_{attempt}",
            )
            exec_task = asyncio.create_task(
                leader_node.exec_transaction(context.transaction)
            )
            try:
                done, _ = await asyncio.wait(
                    {exec_task},
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if exec_task not in done:
                    exec_task.cancel()
                    try:
                        await asyncio.wait_for(exec_task, timeout=0.1)
                    except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                        pass
                    context.consensus_data.leader_receipt = [
                        _build_leader_timeout_receipt(context.leader)
                    ]
                    break

                context.consensus_data.leader_receipt = [await exec_task]
                break  # success
            except GenVMInternalError as e:
                if not e.is_fatal:
                    raise  # non-fatal → propagate immediately
                if not context.remaining_validators:
                    raise  # pool empty → propagate
                # Replace leader with next validator
                from loguru import logger

                logger.error(
                    f"Leader GenVM internal error for {context.transaction.hash}, "
                    f"replacing leader (attempt {attempt + 1}/{MAX_IDLE_REPLACEMENTS}): "
                    f"code={e.error_code}, causes={e.causes}, ctx={e.ctx}"
                )
                context.leader = context.remaining_validators.pop(0)
        else:
            # All replacement attempts exhausted
            raise GenVMInternalError(
                message="Leader idle: all replacements exhausted",
                error_code=GenVMErrorCode.LLM_NO_PROVIDER,
                causes=["ALL_LEADERS_IDLE"],
                is_fatal=True,
                is_leader=True,
            )

        context.transactions_processor.add_state_timestamp(
            context.transaction.hash, "PROPOSING.TRANSACTION_EXECUTED"
        )

        # Update the consensus data with the leader's vote and receipt
        context.consensus_data.votes = {}
        context.votes = {}
        context.consensus_data.validators = []
        context.num_validators = len(context.remaining_validators) + 1

        # Check if the leader timed out
        leader_receipt_timed_out = (
            context.consensus_data.leader_receipt[0].result[0] == ResultCode.VM_ERROR
            and context.consensus_data.leader_receipt[0].result[1:] == b"timeout"
        )
        if not leader_receipt_timed_out:
            try:
                proposal_policy = stamp_receipt_execution_policy(
                    context.consensus_data.leader_receipt[0],
                    (context.transaction.data or {}).get(FEE_ACCOUNTING_KEY),
                    StudioFeePolicy.from_env(),
                )
                validate_receipt_admission_caps(
                    context.consensus_data.leader_receipt[0],
                    proposal_policy,
                )
            except FeeValidationError as exc:
                # The on-chain proposal/reveal call reverts on these caps.
                # Studio advances to the equivalent leader-timeout path
                # instead of accepting a receipt Consensus would never store.
                context.consensus_data.leader_receipt = [
                    _build_leader_timeout_receipt(
                        context.leader,
                        detail=str(exc),
                    )
                ]
                leader_receipt_timed_out = True

        # Post-execution decision
        next_state, post_effects = decide_post_proposal(
            tx_hash=context.transaction.hash,
            leader_receipt_result=context.consensus_data.leader_receipt[0].result,
            leader_receipt_timed_out=leader_receipt_timed_out,
            execution_mode_leader_only=(
                execution_mode == TransactionExecutionMode.LEADER_ONLY
            ),
            appeal_leader_timeout=context.transaction.appeal_leader_timeout,
            leader_address=context.leader["address"],
            leader=context.leader,
            remaining_validators=context.remaining_validators,
            consensus_data_dict=context.consensus_data.to_dict(
                strip_contract_state=True
            ),
        )

        await executor.execute(post_effects)

        if next_state == "leader_timeout":
            return LeaderTimeoutState()

        if context.transaction.appeal_leader_timeout:
            context.transaction.timestamp_appeal = None

        if next_state == "accepted_leader_only":
            # LEADER_ONLY: set leader vote as AGREE, skip validation
            context.consensus_data.votes = {context.leader["address"]: Vote.AGREE.value}
            context.votes = {context.leader["address"]: Vote.AGREE.value}
            context.consensus_data.validators = []
            context.validation_results = []
            context.transactions_processor.set_transaction_result(
                context.transaction.hash,
                context.consensus_data.to_dict(strip_contract_state=True),
            )
            return AcceptedState()

        return CommittingState()


class CommittingState(TransactionState):
    """
    Class representing the committing state of a transaction.
    """

    async def handle(self, context):
        # Pre-execution effects (timestamp + status update)
        pre_effects = prepare_committing(tx_hash=context.transaction.hash)
        executor = EffectExecutor(context)
        await executor.execute(pre_effects)

        def create_validator_node(
            context: TransactionContext, validator: dict, validator_index: int
        ):
            assert context.validators_snapshot is not None

            # Create timing callback for this validator
            def validator_timing_callback(step_name: str):
                context.transactions_processor.add_state_timestamp(
                    context.transaction.hash,
                    f"COMMITTING.VALIDATOR_{validator_index}.{step_name}",
                )

            return context.node_factory(
                validator,
                ExecutionMode.VALIDATOR,
                deepcopy(context.contract_snapshot),
                (
                    context.consensus_data.leader_receipt[0]
                    if context.consensus_data.leader_receipt
                    else None
                ),
                context.msg_handler,
                context.contract_snapshot_factory,
                context.validators_snapshot,
                validator_timing_callback,
                context.genvm_manager,
                context.shared_decoded_value_cache,
                context.shared_contract_snapshot_cache,
            )

        validator_slot_budget_seconds = _slot_budget_seconds(
            context.transaction, "validator"
        )

        # Execute the transaction with a semaphore to limit the number of concurrent validators
        sem = asyncio.Semaphore(VALIDATOR_MAX_CONCURRENT)

        # Build replacement pool: all validators minus those already assigned
        assigned_addresses: set[str] = set()
        if context.leader.get("address"):
            assigned_addresses.add(context.leader["address"])
        assigned_addresses.update(v["address"] for v in context.remaining_validators)
        replacement_pool: list[dict] = [
            n.validator.to_dict()
            for n in context.validators_snapshot.nodes
            if n.validator.to_dict()["address"] not in assigned_addresses
        ]
        pool_lock = asyncio.Lock()

        async def pop_replacement() -> dict | None:
            async with pool_lock:
                return replacement_pool.pop(0) if replacement_pool else None

        def _is_fatal_error(receipt: Receipt) -> bool:
            raw_error = (receipt.genvm_result or {}).get("raw_error")
            return isinstance(raw_error, dict) and raw_error.get("fatal") is True

        def _build_timeout_receipt(validator_dict: dict) -> Receipt:
            timeout_ms = int(validator_slot_budget_seconds * 1000)
            return Receipt(
                result=bytes([ResultCode.VM_ERROR]) + b"timeout",
                calldata=b"",
                gas_used=0,
                mode=ExecutionMode.VALIDATOR,
                contract_state={},
                node_config=validator_dict,
                execution_result=ExecutionResultStatus.ERROR,
                vote=Vote.TIMEOUT,
                genvm_result={
                    "stdout": "",
                    "stderr": (
                        "Validator execution exceeded "
                        f"{validator_slot_budget_seconds:.3f}s"
                    ),
                    "error_code": "CONSENSUS_VALIDATOR_EXEC_TIMEOUT",
                    "raw_error": {
                        "causes": ["VALIDATOR_EXEC_TIMEOUT"],
                        # Slot budget expiry is terminal for this attempt; timeout
                        # replacement is driven after votes are collected.
                        "fatal": False,
                    },
                },
                processing_time=timeout_ms,
            )

        def _build_synthetic_idle_receipt(validator_dict: dict) -> Receipt:
            return Receipt(
                result=bytes([ResultCode.VM_ERROR]) + b"idle",
                calldata=b"",
                gas_used=0,
                mode=ExecutionMode.VALIDATOR,
                contract_state={},
                node_config=validator_dict,
                execution_result=ExecutionResultStatus.ERROR,
                vote=Vote.IDLE,
                genvm_result={
                    "stdout": "",
                    "stderr": "Validator execution cancelled after quorum",
                    "error_code": "CONSENSUS_VALIDATOR_QUORUM_REACHED",
                    "raw_error": {
                        "causes": ["VALIDATOR_QUORUM_REACHED"],
                        "fatal": False,
                    },
                },
                processing_time=0,
            )

        def _build_internal_error_receipt(
            validator_dict: dict, e: GenVMInternalError
        ) -> Receipt:
            raw_error = {"causes": e.causes, "fatal": e.is_fatal}
            if e.ctx:
                raw_error["ctx"] = e.ctx
            return Receipt(
                result=bytes([ResultCode.VM_ERROR])
                + f"GenVM internal error: {e}".encode("utf-8"),
                calldata=b"",
                gas_used=0,
                mode=ExecutionMode.VALIDATOR,
                contract_state={},
                node_config=validator_dict,
                execution_result=ExecutionResultStatus.ERROR,
                vote=Vote.TIMEOUT,
                genvm_result={
                    "stdout": "",
                    "stderr": str(e),
                    "error_code": e.error_code,
                    "raw_error": raw_error,
                },
                processing_time=0,
            )

        async def run_single_validator(validator_dict: dict, index: int) -> Receipt:
            async with sem:
                current = validator_dict
                slot_deadline = (
                    asyncio.get_running_loop().time() + validator_slot_budget_seconds
                )
                for attempt in range(MAX_IDLE_REPLACEMENTS + 1):
                    remaining = slot_deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        result = _build_timeout_receipt(current)
                        break

                    node = create_validator_node(context, current, index)
                    context.transactions_processor.add_state_timestamp(
                        context.transaction.hash,
                        f"COMMITTING.VALIDATOR_{index}_START" f".attempt_{attempt}",
                    )
                    exec_task = asyncio.create_task(
                        node.exec_transaction(context.transaction)
                    )
                    done, _ = await asyncio.wait(
                        {exec_task},
                        timeout=remaining,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if exec_task in done:
                        try:
                            result = await exec_task
                        except GenVMInternalError as e:
                            from loguru import logger

                            logger.error(
                                f"Validator {index} GenVM internal error for "
                                f"{context.transaction.hash}: {e}, ctx={e.ctx}"
                            )
                            result = _build_internal_error_receipt(current, e)
                    else:
                        exec_task.cancel()
                        try:
                            await asyncio.wait_for(exec_task, timeout=0.1)
                        except (
                            asyncio.CancelledError,
                            asyncio.TimeoutError,
                            Exception,
                        ):
                            pass
                        context.transactions_processor.add_state_timestamp(
                            context.transaction.hash,
                            f"COMMITTING.VALIDATOR_{index}_TIMEOUT"
                            f".attempt_{attempt}",
                        )
                        result = _build_timeout_receipt(current)
                    context.transactions_processor.add_state_timestamp(
                        context.transaction.hash,
                        f"COMMITTING.VALIDATOR_{index}_END" f".attempt_{attempt}",
                    )

                    if _is_fatal_error(result) and attempt < MAX_IDLE_REPLACEMENTS:
                        replacement = await pop_replacement()
                        if replacement is not None:
                            current = replacement
                            continue
                    break

                if _is_fatal_error(result):
                    result.vote = Vote.IDLE
                return result

        # Leader evaluates validation function
        validation_by_leader = (
            context.consensus_data.leader_receipt
            and len(context.consensus_data.leader_receipt) == 1
        )

        # Build list of validator dicts to run
        if validation_by_leader:
            validators_to_run = [context.leader] + context.remaining_validators
        else:
            validators_to_run = list(context.remaining_validators)

        context.transactions_processor.add_state_timestamp(
            context.transaction.hash, "COMMITTING.VALIDATORS_PREPARED"
        )

        # Execute the transaction on each validator and gather the results
        context.transactions_processor.add_state_timestamp(
            context.transaction.hash, "COMMITTING.VALIDATORS_EXECUTION_START"
        )

        def _is_quorum_reached(votes_so_far: list[str], total_votes: int) -> bool:
            pending = total_votes - len(votes_so_far)
            if pending < 0:
                return False
            possible_pending_votes = (
                Vote.AGREE.value,
                Vote.DISAGREE.value,
                Vote.TIMEOUT.value,
                Vote.DETERMINISTIC_VIOLATION.value,
                Vote.IDLE.value,
            )
            outcomes = {
                determine_consensus_from_votes(
                    votes_so_far + [pending_vote] * pending,
                    _terminal_decision_electorate_size(context),
                )
                for pending_vote in possible_pending_votes
            }
            if len(outcomes) != 1:
                return False
            return next(iter(outcomes)) != ConsensusResult.NO_MAJORITY

        async def _cancel_pending_validator_tasks(
            pending: Iterable[asyncio.Task],
        ) -> None:
            for task in pending:
                task.cancel()
            for task in pending:
                try:
                    await task
                except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                    pass

        async def _run_validator_wave(
            results: list[Receipt | None],
            slot_validators: list[dict],
            indexes_to_run: list[int],
        ) -> bool:
            if not indexes_to_run:
                return False

            tasks_by_task = {
                asyncio.create_task(
                    run_single_validator(slot_validators[index], index)
                ): index
                for index in indexes_to_run
            }
            pending = set(tasks_by_task)
            while pending:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    index = tasks_by_task[task]
                    results[index] = task.result()

                context.validation_results = list(results)
                votes_so_far = [
                    result.vote.value
                    for result in results
                    if result is not None and result.vote is not None
                ]
                if _is_quorum_reached(votes_so_far, len(slot_validators)):
                    await _cancel_pending_validator_tasks(pending)
                    for task in pending:
                        index = tasks_by_task[task]
                        results[index] = _build_synthetic_idle_receipt(
                            slot_validators[index]
                        )
                    context.validation_results = list(results)
                    return True

            return False

        slot_validators = list(validators_to_run)
        results: list[Receipt | None] = [None] * len(slot_validators)
        replacement_cycles = 0

        while True:
            indexes_to_run = [
                index for index, result in enumerate(results) if result is None
            ]
            quorum_reached = await _run_validator_wave(
                results, slot_validators, indexes_to_run
            )
            if quorum_reached:
                break

            timeout_indexes = [
                index
                for index, result in enumerate(results)
                if result is not None and result.vote == Vote.TIMEOUT
            ]
            if not timeout_indexes or replacement_cycles >= MAX_IDLE_REPLACEMENTS:
                break

            replacement_indexes: list[int] = []
            for index in timeout_indexes:
                replacement = await pop_replacement()
                if replacement is None:
                    continue
                slot_validators[index] = replacement
                results[index] = None
                replacement_indexes.append(index)

            if not replacement_indexes:
                break
            replacement_cycles += 1

        context.validation_results = [
            result for result in results if result is not None
        ]

        # If all validators voted IDLE, infrastructure is systemically broken
        if all(r.vote == Vote.IDLE for r in context.validation_results):
            raise GenVMInternalError(
                message="All validators idle after replacements",
                error_code=GenVMErrorCode.LLM_NO_PROVIDER,
                causes=["ALL_VALIDATORS_IDLE"],
                is_fatal=True,
                is_leader=True,
            )

        context.transactions_processor.add_state_timestamp(
            context.transaction.hash, "COMMITTING.VALIDATORS_EXECUTION_END"
        )
        context.transactions_processor.add_state_timestamp(
            context.transaction.hash, "COMMITTING.VALIDATION_RESULTS_GATHERED"
        )

        # Post-execution effects (vote committed events + timestamp)
        validators_to_emit = slot_validators

        post_effects = decide_post_committing(
            tx_hash=context.transaction.hash,
            validators_to_emit=validators_to_emit,
        )
        await executor.execute(post_effects)

        # Transition to the RevealingState
        return RevealingState()


class RevealingState(TransactionState):
    """
    Class representing the revealing state of a transaction.
    """

    async def handle(self, context):
        # Process votes
        for validation_result in context.validation_results:
            context.votes[validation_result.node_config["address"]] = (
                validation_result.vote.value
            )

        # Determine consensus result
        consensus_result = determine_consensus_from_votes(
            list(context.votes.values()),
            _terminal_decision_electorate_size(context),
        )

        # Build vote reveal entries with canonical v0.6 VoteType ordinals.
        vote_reveal_entries = []
        for validation_result in context.validation_results:
            vote_reveal_entries.append(
                (
                    validation_result.node_config,
                    consensus_vote_type_code(
                        validation_result.vote,
                        validation_result.execution_result,
                    ),
                )
            )

        # Leader receipt split
        if (
            context.consensus_data.leader_receipt
            and len(context.consensus_data.leader_receipt) == 1
        ):
            context.consensus_data.leader_receipt.append(context.validation_results[0])
            context.validation_results = context.validation_results[1:]

        # Vote merging
        if (
            context.transaction.appealed
            or context.transaction.appeal_validators_timeout
        ):
            merged_votes, merged_validators = merge_appeal_validators(
                existing_votes=context.transaction.consensus_data.votes,
                current_votes=context.votes,
                existing_validators=context.transaction.consensus_data.validators,
                current_validation_results=context.validation_results,
                appeal_failed=context.transaction.appeal_failed,
            )
            context.consensus_data.votes = merged_votes
            context.consensus_data.validators = merged_validators
        else:
            context.consensus_data.votes = context.votes
            context.consensus_data.validators = context.validation_results

        # Pure decision
        next_state, effects = decide_revealing(
            tx_hash=context.transaction.hash,
            consensus_result=consensus_result,
            appealed=context.transaction.appealed,
            appeal_validators_timeout=context.transaction.appeal_validators_timeout,
            appeal_undetermined=context.transaction.appeal_undetermined,
            rotation_count=context.rotation_count,
            config_rotation_rounds=_runtime_rotation_limit(
                context.transaction,
                context.active_fee_round,
            ),
            vote_reveal_entries=vote_reveal_entries,
            consensus_data_dict=context.consensus_data.to_dict(
                strip_contract_state=True
            ),
            leader_receipt=context.consensus_data.leader_receipt,
            validation_results=context.validation_results,
        )

        executor = EffectExecutor(context)

        if next_state == "accepted":
            await executor.execute(effects)
            return AcceptedState()

        elif next_state == "validators_timeout":
            await executor.execute(effects)
            return ValidatorsTimeoutState()

        elif next_state == "undetermined":
            # Clear appeal_leader_timeout before transitioning
            if context.transaction.appeal_leader_timeout:
                context.transactions_processor.set_transaction_appeal_leader_timeout(
                    context.transaction.hash, False
                )
                context.transaction.appeal_leader_timeout = False
            await executor.execute(effects)
            return UndeterminedState()

        elif next_state == "rotate":
            # Clear appeal_leader_timeout before rotation attempt
            if context.transaction.appeal_leader_timeout:
                context.transactions_processor.set_transaction_appeal_leader_timeout(
                    context.transaction.hash, False
                )
                context.transaction.appeal_leader_timeout = False

            # Impure: try to add a new validator for rotation
            try:
                used_leader_addresses = (
                    ConsensusAlgorithm.get_used_leader_addresses_from_consensus_history(
                        context.transactions_processor.get_transaction_by_hash(
                            context.transaction.hash
                        )["consensus_history"],
                        context.consensus_data.leader_receipt[0],
                    )
                )
                assert context.validators_snapshot is not None
                old_validators = [
                    x.validator.to_dict() for x in context.validators_snapshot.nodes
                ]
                context.involved_validators = ConsensusAlgorithm.add_new_validator(
                    old_validators,
                    context.remaining_validators,
                    used_leader_addresses,
                )
            except ValueError as e:
                context.msg_handler.send_message(
                    LogEvent(
                        "consensus_event",
                        EventType.ERROR,
                        EventScope.CONSENSUS,
                        str(e),
                        {"transaction_hash": context.transaction.hash},
                        transaction_hash=context.transaction.hash,
                    )
                )
                return UndeterminedState()

            # Rotation succeeded
            context.rotation_count += 1
            await executor.execute(effects)

            # Emit leader rotated (needs new leader address from add_new_validator)
            context.consensus_service.emit_transaction_event(
                "emitTransactionLeaderRotated",
                context.consensus_data.leader_receipt[0].node_config,
                context.transaction.hash,
                context.involved_validators[0]["address"],
            )

            return ProposingState()

        elif isinstance(next_state, ConsensusRound):
            await executor.execute(effects)
            return next_state

        else:
            raise ValueError(f"Invalid next state: {next_state}")


class AcceptedState(TransactionState):
    """
    Class representing the accepted state of a transaction.
    """

    async def handle(self, context):
        leader_receipt = context.consensus_data.leader_receipt[0]
        _apply_external_message_freeze_check(context, leader_receipt)
        _sync_reveal_message_fee_accounting(context, leader_receipt)
        accepted_contract_state = leader_receipt.contract_state
        execution_success = (
            leader_receipt.execution_result == ExecutionResultStatus.SUCCESS
        )
        is_deploy = context.transaction.type == TransactionType.DEPLOY_CONTRACT

        pre_effects, post_effects, consensus_round, return_value = decide_accepted(
            tx_hash=context.transaction.hash,
            appeal_undetermined=context.transaction.appeal_undetermined,
            appealed=context.transaction.appealed,
            appeal_leader_timeout=context.transaction.appeal_leader_timeout,
            appeal_failed=context.transaction.appeal_failed,
            consensus_data_dict=context.consensus_data.to_dict(
                strip_contract_state=True
            ),
            leader_receipt_list=context.consensus_data.leader_receipt,
            validation_results=context.validation_results,
            redacted_consensus_data=_redact_consensus_data_for_log(
                context.consensus_data.to_dict()
            ),
            has_contract_snapshot=bool(context.transaction.contract_snapshot),
            contract_snapshot_dict=(
                context.contract_snapshot.to_dict()
                if not context.transaction.contract_snapshot
                else None
            ),
            execution_result_success=execution_success,
            tx_type_deploy=is_deploy,
            accepted_contract_state=accepted_contract_state,
            contract_address=(
                context.transaction.data.get("contract_address") if is_deploy else None
            ),
            code_slot_b64=(
                base64.b64encode(get_code_slot()).decode("ascii") if is_deploy else None
            ),
            to_address=context.transaction.to_address,
            leader_node_config=leader_receipt.node_config,
            genvm_executor_selector=(
                transaction_genvm_executor_selector(context.transaction)
                if is_deploy
                else None
            ),
        )

        # Execute pre-effects (includes contract registration/update via executor)
        executor = EffectExecutor(context)
        await executor.execute(pre_effects)

        # Consensus tombstones each tx-scoped logical message occurrence. The
        # Studio bridge additionally makes the helper call, value reservation,
        # and local child insertion replay-safe across worker/process failure.
        if execution_success:
            _dispatch_messages_for_phase(
                context,
                leader_receipt,
                "accepted",
                force_phase_event=True,
            )

        # Execute post-effects (status update + appeal cleanup)
        await executor.execute(post_effects)

        # Context mutations
        if context.transaction.appeal_undetermined:
            context.transaction.appeal_undetermined = False
            context.transaction.timestamp_appeal = None
        elif context.transaction.appealed:
            context.transaction.appealed = False
        if context.transaction.appeal_leader_timeout:
            context.transaction.appeal_leader_timeout = False

        return return_value


class UndeterminedState(TransactionState):
    """
    Class representing the undetermined state of a transaction.
    """

    async def handle(self, context):
        effects, _ = decide_undetermined(
            tx_hash=context.transaction.hash,
            appeal_undetermined=context.transaction.appeal_undetermined,
            appeal_failed=context.transaction.appeal_failed,
            has_contract_snapshot=bool(context.transaction.contract_snapshot),
            contract_snapshot_dict=(
                context.contract_snapshot.to_dict()
                if not context.transaction.contract_snapshot
                else None
            ),
            consensus_data_dict=context.consensus_data.to_dict(
                strip_contract_state=True
            ),
            timestamp_appeal=context.transaction.timestamp_appeal,
            leader_receipt=context.consensus_data.leader_receipt,
            validators=context.consensus_data.validators,
            redacted_consensus_data=_redact_consensus_data_for_log(
                context.consensus_data.to_dict()
            ),
        )

        await EffectExecutor(context).execute(effects)

        # Context mutation: clear appeal_undetermined flag
        if context.transaction.appeal_undetermined:
            context.transaction.appeal_undetermined = False

        return None


class LeaderTimeoutState(TransactionState):
    """
    Class representing the leader timeout state of a transaction.
    """

    async def handle(self, context):
        effects, _ = decide_leader_timeout(
            tx_hash=context.transaction.hash,
            appeal_undetermined=context.transaction.appeal_undetermined,
            appeal_leader_timeout=context.transaction.appeal_leader_timeout,
            has_contract_snapshot=bool(context.transaction.contract_snapshot),
            contract_snapshot_dict=(
                context.contract_snapshot.to_dict()
                if not context.transaction.contract_snapshot
                else None
            ),
            leader_receipt=context.consensus_data.leader_receipt,
            remaining_validators=context.remaining_validators,
            leader=context.leader,
        )

        await EffectExecutor(context).execute(effects)

        return None


class ValidatorsTimeoutState(TransactionState):
    """
    Class representing the validators timeout state of a transaction.
    """

    async def handle(self, context):
        effects, _ = decide_validators_timeout(
            tx_hash=context.transaction.hash,
            appeal_undetermined=context.transaction.appeal_undetermined,
            appeal_validators_timeout=context.transaction.appeal_validators_timeout,
            appeal_leader_timeout=context.transaction.appeal_leader_timeout,
            appeal_failed=context.transaction.appeal_failed,
            has_contract_snapshot=bool(context.transaction.contract_snapshot),
            contract_snapshot_dict=(
                context.contract_snapshot.to_dict()
                if not context.transaction.contract_snapshot
                else None
            ),
            consensus_data_dict=context.consensus_data.to_dict(
                strip_contract_state=True
            ),
            leader_receipt=context.consensus_data.leader_receipt,
            validation_results=context.validation_results,
        )

        await EffectExecutor(context).execute(effects)

        # Context mutation: clear appeal_leader_timeout flag
        if context.transaction.appeal_leader_timeout:
            context.transaction.appeal_leader_timeout = False

        return None


class FinalizingState(TransactionState):
    """
    Class representing the finalizing state of a transaction.
    """

    async def handle(self, context):
        leader_receipt = context.transaction.consensus_data.leader_receipt[0]

        # Acceptance is a separate message lifecycle phase. If the helper EVM
        # or worker died after the agreed decision was committed, repair that
        # phase before allowing finalization to overtake it.
        _dispatch_messages_for_phase(context, leader_receipt, "accepted")

        pre_effects, post_effects, should_finalize_contract = decide_finalizing(
            tx_hash=context.transaction.hash,
            tx_status_accepted=(
                context.transaction.status == TransactionStatus.ACCEPTED
            ),
            execution_result_success=(
                leader_receipt.execution_result == ExecutionResultStatus.SUCCESS
            ),
            leader_node_config=leader_receipt.node_config,
        )

        executor = EffectExecutor(context)
        await executor.execute(pre_effects)

        # Impure: contract finalization + triggered transactions (needs DB reads)
        if should_finalize_contract:
            snapshot = context.contract_snapshot_factory(context.transaction.to_address)
            if snapshot is None:
                raise RuntimeError(
                    "Missing contract snapshot while finalizing a transaction"
                )

            accepted_state = snapshot.states.get("accepted")
            if not accepted_state:
                raise RuntimeError(
                    "Missing accepted contract state prior to finalization"
                )

            context.contract_processor.update_contract_state(
                context.transaction.to_address,
                finalized_state=accepted_state,
            )

            _dispatch_messages_for_phase(
                context,
                leader_receipt,
                "finalized",
                force_phase_event=True,
            )

        await executor.execute(post_effects)

        refund_recipient = (
            context.transaction.origin_address or context.transaction.from_address
        )
        if refund_recipient:
            context.accounts_manager.settle_tx_fee_accounting_once(
                context.transaction.hash,
                refund_recipient,
                receipt=leader_receipt,
                reason="finalized",
            )
            context.accounts_manager.session.commit()


def _get_messages_data(
    context: TransactionContext,
    pending_transactions: Iterable[PendingTransaction],
    on: Literal["accepted", "finalized"],
):
    insert_transactions_data = []
    internal_messages_data = []
    message_fee_payloads = []
    parent_fee_accounting = (getattr(context.transaction, "data", None) or {}).get(
        FEE_ACCOUNTING_KEY
    )
    reveal_recorded = bool(
        parent_fee_accounting
        and parent_fee_accounting.get("message_fees_recorded_at_reveal")
    )
    base_nonce = context.transactions_processor.get_genlayer_transaction_count(
        context.transaction.to_address
    )
    phase_pending_transactions = list(
        _pending_transactions_for_phase(pending_transactions, on)
    )
    identity_payloads = (
        _reveal_message_fee_payloads(parent_fee_accounting, phase_pending_transactions)
        if parent_fee_accounting
        else [
            _pending_transaction_fee_payload(pending_transaction, on)
            for pending_transaction in phase_pending_transactions
        ]
    )
    effect_identities = message_effect_identities(
        context.transaction.hash,
        identity_payloads,
    )
    for nonce_offset, pending_transaction in enumerate(phase_pending_transactions):
        nonce = base_nonce + nonce_offset
        transaction_type, data = _child_transaction_payload(
            context, pending_transaction
        )

        _append_message_fee_payload(
            context,
            pending_transaction,
            parent_fee_accounting,
            message_fee_payloads,
            data,
            on,
        )

        insert_transactions_data.append(
            [
                (
                    ZERO_ADDRESS
                    if pending_transaction.is_deploy()
                    else pending_transaction.address
                ),
                data,
                transaction_type.value,
                nonce,
                pending_transaction.value,
                effect_identities[nonce_offset][0],
                identity_payloads[nonce_offset],
            ]
        )

        if not pending_transaction.is_eth_send:
            internal_messages_data.append(
                _internal_message_event_data(context, pending_transaction, data)
            )

    _record_parent_message_fee_consumption(
        context,
        parent_fee_accounting,
        message_fee_payloads,
        reveal_recorded,
    )

    return internal_messages_data, insert_transactions_data


def _pending_transactions_for_phase(
    pending_transactions: Iterable[PendingTransaction],
    on: Literal["accepted", "finalized"],
) -> Iterable[PendingTransaction]:
    return (
        pending_transaction
        for pending_transaction in pending_transactions
        if pending_transaction.on == on
    )


def _child_transaction_payload(
    context: TransactionContext,
    pending_transaction: PendingTransaction,
) -> tuple[TransactionType, dict]:
    if pending_transaction.is_eth_send:
        return TransactionType.SEND, {}
    if pending_transaction.is_deploy():
        return _deploy_child_transaction_payload(context, pending_transaction)
    return TransactionType.RUN_CONTRACT, {"calldata": pending_transaction.calldata}


def _deploy_child_transaction_payload(
    context: TransactionContext,
    pending_transaction: PendingTransaction,
) -> tuple[TransactionType, dict]:
    return (
        TransactionType.DEPLOY_CONTRACT,
        {
            # Consensus is the address authority for internal deployments.
            # Keep the payload stable across retries and bind the local child
            # to the returned CREATE/CREATE2 recipient after helper delivery.
            "contract_address": ZERO_ADDRESS,
            "contract_code": pending_transaction.code,
            "calldata": pending_transaction.calldata,
        },
    )


def _append_message_fee_payload(
    context: TransactionContext,
    pending_transaction: PendingTransaction,
    parent_fee_accounting: dict[str, Any] | None,
    message_fee_payloads: list[dict[str, Any]],
    data: dict,
    on: Literal["accepted", "finalized"],
) -> None:
    if not parent_fee_accounting:
        return

    message_payload = _parent_message_fee_payload(
        parent_fee_accounting,
        pending_transaction,
        on,
    )
    message_fee_payloads.append(message_payload)
    if pending_transaction.is_eth_send:
        return

    _attach_child_fee_accounting(
        context,
        parent_fee_accounting,
        message_payload,
        pending_transaction,
        data,
    )


def _parent_message_fee_payload(
    parent_fee_accounting: dict[str, Any],
    pending_transaction: PendingTransaction,
    on: Literal["accepted", "finalized"],
) -> dict[str, Any]:
    payload = _pending_transaction_fee_payload(pending_transaction, on)
    if pending_transaction.is_eth_send or pending_transaction.use_balance:
        return payload

    try:
        return fill_message_fee_payload_from_allocation(parent_fee_accounting, payload)
    except FeeValidationError as exc:
        raise RuntimeError(str(exc)) from exc


def _attach_child_fee_accounting(
    context: TransactionContext,
    parent_fee_accounting: dict[str, Any],
    message_payload: dict[str, Any],
    pending_transaction: PendingTransaction,
    data: dict,
) -> None:
    if int(message_payload.get("declaredBudget", 0) or 0) <= 0:
        return

    try:
        child_fees, child_fee_accounting = create_child_fee_accounting(
            message=message_payload,
            parent_fees_distribution=parent_fee_accounting.get("fees_distribution"),
            message_allocations=(
                message_payload.get("_studioResolvedAllocationSubtree")
                or message_payload.get("allocationSubtree")
                or []
            ),
            sender=(
                context.transaction.to_address
                if pending_transaction.use_balance
                else context.transaction.origin_address
                or context.transaction.from_address
            ),
            policy=StudioFeePolicy.from_env(),
        )
    except FeeValidationError as exc:
        raise RuntimeError(str(exc)) from exc

    data.update(
        {
            "fee_value": int(message_payload["declaredBudget"]),
            "user_value": pending_transaction.value,
            "fees_distribution": child_fees,
            "message_allocations_count": len(
                child_fee_accounting.get("message_allocations") or []
            ),
            FEE_ACCOUNTING_KEY: child_fee_accounting,
        }
    )


def _internal_message_event_data(
    context: TransactionContext,
    pending_transaction: PendingTransaction,
    data: dict,
) -> dict[str, Any]:
    return {
        "sender": context.transaction.to_address,
        "recipient": (
            ZERO_ADDRESS
            if pending_transaction.is_deploy()
            else pending_transaction.address
        ),
        "saltNonce": pending_transaction.salt_nonce,
        "data": json.dumps(_serializable_message_data(data)).encode(),
    }


def _serializable_message_data(data: dict) -> dict:
    serializable_data = data.copy()
    if "contract_code" in serializable_data:
        serializable_data["contract_code"] = serializable_data["contract_code"].decode()
    if "calldata" in serializable_data:
        serializable_data["calldata"] = base64.b64encode(
            serializable_data["calldata"]
        ).decode("utf-8")
    return serializable_data


def _record_parent_message_fee_consumption(
    context: TransactionContext,
    parent_fee_accounting: dict[str, Any] | None,
    message_fee_payloads: list[dict[str, Any]],
    reveal_recorded: bool,
) -> None:
    if not parent_fee_accounting or not message_fee_payloads:
        return

    updated_accounting = (
        context.transactions_processor.mutate_transaction_fee_accounting(
            context.transaction.hash,
            lambda current: _consume_parent_message_fee_payloads(
                current,
                message_fee_payloads,
                reveal_recorded,
                _message_execution_fee_recipient(context),
            ),
            commit=False,
        )
    )
    context.transaction.data = dict(context.transaction.data or {})
    context.transaction.data[FEE_ACCOUNTING_KEY] = updated_accounting


def _message_execution_fee_recipient(context: TransactionContext) -> str | None:
    """Best Studio analogue of Consensus' tx.origin execution recipient.

    Acceptance processing happens inside the leader's reveal transaction on
    chain. Studio does not expose a separate public external-message flush EOA,
    so finalization uses the leader attached to the accepted receipt as the
    observable executor instead of incorrectly reimbursing the transaction
    sender.
    """

    receipt_sources = [
        getattr(context, "consensus_data", None),
        getattr(context.transaction, "consensus_data", None),
    ]
    for source in receipt_sources:
        if source is None:
            continue
        leader_receipts = (
            source.get("leader_receipt", [])
            if isinstance(source, dict)
            else getattr(source, "leader_receipt", [])
        )
        if isinstance(leader_receipts, dict):
            leader_receipts = [leader_receipts]
        if not leader_receipts:
            continue
        receipt = leader_receipts[0]
        node_config = (
            receipt.get("node_config", {})
            if isinstance(receipt, dict)
            else getattr(receipt, "node_config", {})
        )
        address = (
            node_config.get("address")
            if isinstance(node_config, dict)
            else getattr(node_config, "address", None)
        )
        if address:
            return str(address)
    return None


def _consume_parent_message_fee_payloads(
    parent_fee_accounting: dict[str, Any],
    message_fee_payloads: list[dict[str, Any]],
    reveal_recorded: bool,
    executor: str | None = None,
) -> dict[str, Any]:
    try:
        if reveal_recorded:
            return record_external_message_execution_fees(
                parent_fee_accounting,
                message_fee_payloads,
                executor=executor,
            )
        return consume_message_fees(
            parent_fee_accounting,
            message_fee_payloads,
            external_executor=executor,
        )
    except FeeValidationError as exc:
        raise RuntimeError(str(exc)) from exc


def _sync_reveal_message_fee_accounting(
    context: TransactionContext,
    leader_receipt: Receipt,
) -> None:
    if leader_receipt.execution_result != ExecutionResultStatus.SUCCESS:
        _unwind_discarded_reveal_message_fee_accounting(context)
        return

    parent_fee_accounting = (context.transaction.data or {}).get(FEE_ACCOUNTING_KEY)
    if not parent_fee_accounting:
        return

    def prepare_latest(current_fee_accounting):
        message_fee_payloads = _reveal_message_fee_payloads(
            current_fee_accounting,
            leader_receipt.pending_transactions,
        )
        try:
            return prepare_reveal_message_generation(
                current_fee_accounting,
                context.transaction.hash,
                message_fee_payloads,
            )
        except FeeValidationError as exc:
            raise RuntimeError(str(exc)) from exc

    updated_accounting = (
        context.transactions_processor.mutate_transaction_fee_accounting(
            context.transaction.hash,
            prepare_latest,
            commit=False,
        )
    )

    context.transaction.data = dict(context.transaction.data or {})
    context.transaction.data[FEE_ACCOUNTING_KEY] = updated_accounting


def _reveal_message_fee_payloads(
    parent_fee_accounting: dict[str, Any],
    pending_transactions: Iterable[Any],
) -> list[dict[str, Any]]:
    message_fee_payloads = []
    for raw_pending_transaction in pending_transactions:
        pending_transaction = _coerce_pending_transaction(raw_pending_transaction)
        message_payload = _pending_transaction_fee_payload(
            pending_transaction,
            pending_transaction.on,
        )
        if not pending_transaction.is_eth_send and not pending_transaction.use_balance:
            message_payload = fill_message_fee_payload_from_allocation(
                parent_fee_accounting,
                message_payload,
            )
        message_fee_payloads.append(message_payload)
    return message_fee_payloads


def _novel_pending_transactions(
    context: TransactionContext,
    pending_transactions: Iterable[Any],
) -> list[PendingTransaction]:
    pending_list = [
        _coerce_pending_transaction(pending_transaction)
        for pending_transaction in pending_transactions
    ]
    parent_fee_accounting = (getattr(context.transaction, "data", None) or {}).get(
        FEE_ACCOUNTING_KEY
    )
    if not parent_fee_accounting or not pending_list:
        return pending_list

    payloads = _reveal_message_fee_payloads(parent_fee_accounting, pending_list)
    try:
        novelty = message_novelty_mask(
            parent_fee_accounting,
            context.transaction.hash,
            payloads,
        )
    except FeeValidationError as exc:
        raise RuntimeError(str(exc)) from exc
    return [
        pending_transaction
        for pending_transaction, is_novel in zip(pending_list, novelty)
        if is_novel
    ]


def _mark_message_phase_delivered(
    context: TransactionContext,
    pending_transactions: Iterable[Any],
    on: Literal["accepted", "finalized"],
) -> None:
    parent_fee_accounting = (context.transaction.data or {}).get(FEE_ACCOUNTING_KEY)
    if not parent_fee_accounting:
        return
    pending_list = list(pending_transactions)

    def mark_latest(current_fee_accounting):
        payloads = _reveal_message_fee_payloads(
            current_fee_accounting,
            pending_list,
        )
        try:
            return mark_message_effects_delivered(
                current_fee_accounting,
                context.transaction.hash,
                payloads,
                on,
            )
        except FeeValidationError as exc:
            raise RuntimeError(str(exc)) from exc

    updated_accounting = (
        context.transactions_processor.mutate_transaction_fee_accounting(
            context.transaction.hash,
            mark_latest,
            commit=False,
        )
    )
    context.transaction.data = dict(context.transaction.data or {})
    context.transaction.data[FEE_ACCOUNTING_KEY] = updated_accounting


def _message_phase_delivery_required(
    context: TransactionContext,
    pending_transactions: Iterable[Any],
    on: Literal["accepted", "finalized"],
    *,
    force_phase_event: bool = False,
) -> bool:
    if force_phase_event:
        return True

    parent_fee_accounting = (context.transaction.data or {}).get(FEE_ACCOUNTING_KEY)
    if not isinstance(parent_fee_accounting, dict):
        return False

    pending_list = [
        _coerce_pending_transaction(pending_transaction)
        for pending_transaction in pending_transactions
    ]
    if pending_list:
        payloads = _reveal_message_fee_payloads(parent_fee_accounting, pending_list)
        novelty = message_novelty_mask(
            parent_fee_accounting,
            context.transaction.hash,
            payloads,
        )
        if any(
            is_novel and pending_transaction.on == on
            for pending_transaction, is_novel in zip(pending_list, novelty)
        ):
            return True

    if on == "accepted":
        generation = parent_fee_accounting.get("active_message_generation")
        if isinstance(generation, dict):
            pending_generation = bool(
                generation.get("acceptanceDispatchRequired", False)
            ) and not bool(generation.get("acceptanceDispatched", False))
            if pending_generation:
                return True
    if bool((parent_fee_accounting.get("message_phase_emitted") or {}).get(on, False)):
        return False
    return False


def _dispatch_messages_for_phase(
    context: TransactionContext,
    leader_receipt: Receipt,
    on: Literal["accepted", "finalized"],
    *,
    force_phase_event: bool = False,
) -> bool:
    if leader_receipt.execution_result != ExecutionResultStatus.SUCCESS:
        return False
    if not _message_phase_delivery_required(
        context,
        leader_receipt.pending_transactions,
        on,
        force_phase_event=force_phase_event,
    ):
        return False

    novel_pending_transactions = _novel_pending_transactions(
        context,
        leader_receipt.pending_transactions,
    )
    _internal_messages_data, insert_transactions_data = _get_messages_data(
        context,
        _apply_message_value_withdrawals_for_phase(
            context,
            novel_pending_transactions,
            on,
        ),
        on,
    )
    # Studio's normal topology intentionally has no Hardhat node.  Make the DB
    # transaction the sole authority for child ids, CREATE/CREATE2 recipients,
    # and per-child admission failure instead of allowing an optional, older
    # shadow deployment to decide protocol state differently across replicas.
    rollup_receipt = _author_message_phase_locally(
        context,
        insert_transactions_data,
        on,
    )
    _emit_messages(
        context,
        insert_transactions_data,
        rollup_receipt,
        on,
    )
    _mark_message_phase_delivered(
        context,
        leader_receipt.pending_transactions,
        on,
    )
    return True


def _author_message_phase_locally(
    context: TransactionContext,
    insert_transactions_data: list,
    on: Literal["accepted", "finalized"],
) -> dict[str, list[str]]:
    """Mirror MessagePayments + CreationPhase in the request DB transaction."""

    context.transactions_processor.lock_ghost_factory()
    factory = GhostFactoryConfig.from_env()
    successful_deployments = (
        context.transactions_processor.get_successful_ghost_creation_count()
    )
    batch_deployments: set[str] = set()
    tx_ids: list[str] = []
    recipients: list[str] = []
    zero_tx_id = "0x" + ("00" * 32)
    prepared_children: list[tuple[list, int, str, bool]] = []

    try:
        pending_cap = int(os.environ.get("MAX_PENDING_PER_CONTRACT_DEFAULT", "20"))
    except (TypeError, ValueError):
        pending_cap = 20
    if pending_cap <= 0:
        pending_cap = 20

    for insert_transaction_data in insert_transactions_data:
        transaction_type = insert_transaction_data[2]
        if transaction_type == TransactionType.SEND.value:
            continue

        actual_recipient = insert_transaction_data[0]
        message_payload = insert_transaction_data[6]
        # MessagePayments rejects an empty internal message before calling
        # CreationPhase. Keep it a per-child contained failure: no ghost,
        # queue slot, child id, or factory nonce is consumed.
        payload_data = message_payload.get("data")
        creation_failed = payload_data in {b"", "", "0x", None}
        if (
            not creation_failed
            and transaction_type == TransactionType.DEPLOY_CONTRACT.value
        ):
            salt_nonce = int(message_payload.get("saltNonce", 0) or 0)
            actual_recipient = factory.address_for(
                salt_nonce,
                successful_deployments,
            )
            normalized = actual_recipient.lower()
            if (
                normalized in batch_deployments
                or context.transactions_processor.is_genvm_contract_address(
                    actual_recipient
                )
            ):
                creation_failed = True
            else:
                batch_deployments.add(normalized)
                successful_deployments += 1
        elif (
            not creation_failed
            and not context.transactions_processor.is_genvm_contract_address(
                actual_recipient
            )
        ):
            creation_failed = True

        prepared_children.append(
            (
                insert_transaction_data,
                transaction_type,
                actual_recipient,
                creation_failed,
            )
        )

    # A single Consensus transaction serializes every Queues.enqueueNewPending
    # call. Studio workers processing unrelated parents do not, so lock all
    # recipient queues in deterministic order before observing their depths.
    context.transactions_processor.lock_pending_recipients(
        actual_recipient
        for _data, _type, actual_recipient, creation_failed in prepared_children
        if not creation_failed
    )

    batch_pending: dict[str, int] = {}
    for (
        insert_transaction_data,
        transaction_type,
        actual_recipient,
        creation_failed,
    ) in prepared_children:

        normalized_recipient = str(actual_recipient).lower()
        if not creation_failed:
            pending = context.transactions_processor.get_pending_transaction_count_for_address(
                actual_recipient
            ) + batch_pending.get(
                normalized_recipient, 0
            )
            if pending >= pending_cap:
                creation_failed = True

        if creation_failed:
            tx_ids.append(zero_tx_id)
            recipients.append(
                ZERO_ADDRESS
                if transaction_type == TransactionType.DEPLOY_CONTRACT.value
                else to_checksum_address(actual_recipient)
            )
            continue

        batch_pending[normalized_recipient] = (
            batch_pending.get(normalized_recipient, 0) + 1
        )
        occurrence = insert_transaction_data[5]
        tx_ids.append(
            _studio_child_transaction_id(
                context.transaction.hash,
                on,
                occurrence,
            )
        )
        recipients.append(to_checksum_address(actual_recipient))

    return {"tx_ids_hex": tx_ids, "recipients": recipients}


def _studio_child_transaction_id(
    parent_tx_id: str,
    on: Literal["accepted", "finalized"],
    occurrence: str,
) -> str:
    """Stable Studio tx id for one Consensus message occurrence."""

    domain = keccak(text="GenLayer/Studio/child-tx/v1")
    phase = b"\x01" if on == "accepted" else b"\x00"
    return (
        "0x"
        + keccak(
            domain
            + to_bytes(hexstr=parent_tx_id).rjust(32, b"\x00")
            + phase
            + to_bytes(hexstr=occurrence).rjust(32, b"\x00")
        ).hex()
    )


def _unwind_discarded_reveal_message_fee_accounting(
    context: TransactionContext,
) -> None:
    parent_fee_accounting = (context.transaction.data or {}).get(FEE_ACCOUNTING_KEY)
    if not parent_fee_accounting:
        return

    prior_receipts = _leader_receipts_from_consensus_data(
        context.transaction.consensus_data
    )
    if not prior_receipts:
        prior_receipts = _leader_receipts_from_consensus_history(
            context.transaction.consensus_history
        )
    if (
        not isinstance(parent_fee_accounting.get("active_message_generation"), dict)
        and not prior_receipts
    ):
        return

    def unwind_latest(current_fee_accounting):
        if isinstance(current_fee_accounting.get("active_message_generation"), dict):
            return discard_active_message_generation(current_fee_accounting)
        if not prior_receipts:
            return current_fee_accounting
        message_fee_payloads = _reveal_message_fee_payloads(
            current_fee_accounting,
            _receipt_pending_transactions(prior_receipts[0]),
        )
        if not message_fee_payloads:
            return current_fee_accounting
        updated = unwind_reveal_message_fees(
            current_fee_accounting,
            message_fee_payloads,
            acceptance_dispatched=(
                context.transaction.status == TransactionStatus.ACCEPTED
            ),
        )
        updated["message_fees_recorded_at_reveal"] = True
        return updated

    updated_accounting = (
        context.transactions_processor.mutate_transaction_fee_accounting(
            context.transaction.hash,
            unwind_latest,
            commit=False,
        )
    )
    context.transaction.data = dict(context.transaction.data or {})
    context.transaction.data[FEE_ACCOUNTING_KEY] = updated_accounting


def _coerce_pending_transaction(raw: Any) -> PendingTransaction:
    if isinstance(raw, PendingTransaction):
        return raw
    if isinstance(raw, dict):
        return PendingTransaction.from_dict(raw)
    raise TypeError(f"Unsupported pending transaction type: {type(raw).__name__}")


def _leader_receipts_from_consensus_history(consensus_history: Any) -> list[Any]:
    if not isinstance(consensus_history, dict):
        return []

    consensus_results = consensus_history.get("consensus_results")
    if not isinstance(consensus_results, list):
        return []

    for consensus_round in reversed(consensus_results):
        if not isinstance(consensus_round, dict):
            continue
        leader_result = consensus_round.get("leader_result")
        if isinstance(leader_result, list):
            return leader_result[:1]
        if isinstance(leader_result, dict):
            return [leader_result]
    return []


def _pending_transaction_fee_payload(
    pending_transaction: PendingTransaction,
    on: Literal["accepted", "finalized"],
) -> dict[str, Any]:
    message_type = 0 if pending_transaction.is_eth_send else 1
    call_key = pending_transaction.call_key
    if message_type == 0:
        call_key = derive_external_message_call_key(
            call_key,
            pending_transaction.calldata,
        )
    return {
        "messageType": message_type,
        "recipient": pending_transaction.address,
        "value": pending_transaction.value,
        "data": pending_transaction.calldata,
        "onAcceptance": on == "accepted",
        "saltNonce": pending_transaction.salt_nonce,
        "feeParams": pending_transaction.fee_params,
        "declaredBudget": pending_transaction.declared_budget,
        "allocationSubtree": pending_transaction.allocation_subtree,
        "callKey": call_key,
        "useBalance": pending_transaction.use_balance,
        "gasUsed": pending_transaction.gas_used,
    }


def _child_config_rotation_rounds(
    parent_rotations: int | None, data: dict[str, Any]
) -> int:
    """Clamp a triggered child's runtime rotations to its funded schedule.

    ``transactions.config_rotation_rounds`` is nullable, so a parent may carry
    no explicit schedule at all — rows written before the column was claimed,
    or any row that legitimately stored NULL. Resolve that to the same default
    Transaction itself uses rather than raising, which would fail every child
    insert and retry the parent until it was cancelled.
    """

    if parent_rotations is None:
        parent_rotations = int(os.getenv("VITE_MAX_ROTATIONS", 3))
    fees = data.get("fees_distribution")
    if not isinstance(fees, dict):
        return max(0, int(parent_rotations))
    rotations = fees.get("rotations")
    if not isinstance(rotations, list):
        rotations = fees.get("rotationsList")
    if not isinstance(rotations, list):
        return max(0, int(parent_rotations))
    if not rotations:
        return 0
    return min(max(0, int(parent_rotations)), max(max(0, int(v)) for v in rotations))


def _active_execution_fee_round(transaction: Transaction) -> int:
    """Resolve the raw round once, before appeal flags mutate during retries."""

    entries = logical_fee_round_entries(transaction.consensus_history)
    last_round = entries[-1][0] if entries else 0
    if transaction.appeal_undetermined or transaction.appeal_leader_timeout:
        return last_round + 2
    if entries and last_round % 2 == 1:
        # Successful validator review is followed by one terminal normal round.
        return last_round + 1
    return last_round


def _terminal_decision_electorate_size(context: TransactionContext) -> int | None:
    """Frozen threshold authority for a terminal normal recomputation."""

    entries = logical_fee_round_entries(
        getattr(context.transaction, "consensus_history", None)
    )
    if not entries:
        return None
    last_outcome = str(entries[-1][1].get("consensus_round") or "")
    if last_outcome not in TERMINAL_VALIDATOR_APPEAL_ROUNDS:
        return None

    accounting = (context.transaction.data or {}).get(FEE_ACCOUNTING_KEY) or {}
    frozen_count = accounting.get("selection_pool_count")
    if frozen_count is not None:
        return max(0, int(frozen_count))
    if context.validators_snapshot is not None:
        return len(context.validators_snapshot.nodes)
    return None


def _runtime_rotation_limit(
    transaction: Transaction,
    raw_round: int | None = None,
) -> int:
    """Mirror Consensus' per-normal-round funded rotation allowance."""

    if transaction.appealed or transaction.appeal_validators_timeout:
        return 0
    if raw_round is None:
        raw_round = _active_execution_fee_round(transaction)

    accounting = (transaction.data or {}).get(FEE_ACCOUNTING_KEY) or {}
    fees_distribution = accounting.get("fees_distribution")
    if not isinstance(fees_distribution, dict):
        return max(0, int(transaction.config_rotation_rounds or 0))
    return runtime_rotations_for_round(
        fees_distribution,
        transaction.config_rotation_rounds,
        raw_round,
    )


def _is_zero_transaction_hash(transaction_hash: Any) -> bool:
    if not isinstance(transaction_hash, str):
        return False
    try:
        return int(transaction_hash, 16) == 0
    except ValueError:
        return False


def _refund_skipped_internal_message(
    context: TransactionContext,
    insert_transaction_data: list,
    triggered_on: Literal["accepted", "finalized"],
) -> None:
    occurrence = insert_transaction_data[5]
    message_payload = insert_transaction_data[6]
    refund_amount = int(insert_transaction_data[4] or 0)
    if bool(message_payload.get("useBalance", False)):
        refund_amount += int(message_payload.get("declaredBudget", 0) or 0)

    parent_fee_accounting = (context.transaction.data or {}).get(FEE_ACCOUNTING_KEY)
    mutate_accounting = getattr(
        context.transactions_processor,
        "mutate_transaction_fee_accounting",
        None,
    )
    if not isinstance(parent_fee_accounting, dict) or not callable(mutate_accounting):
        if refund_amount > 0:
            context.accounts_manager.credit_account_balance(
                context.transaction.to_address,
                refund_amount,
            )
        return

    expected_descriptor = message_effect_identities(
        context.transaction.hash,
        [message_payload],
    )[0][1]

    def refund_latest(current_fee_accounting: dict[str, Any]) -> dict[str, Any]:
        current_record = (
            current_fee_accounting.get("message_value_effects") or {}
        ).get(occurrence)
        if not isinstance(current_record, dict):
            raise RuntimeError(f"MessageValueEffectMissing({occurrence})")
        if (
            current_record.get("descriptor") != expected_descriptor
            or current_record.get("phase") != triggered_on
        ):
            raise RuntimeError(
                "MessageValueEffectDescriptorMismatch"
                f"({occurrence},{current_record.get('descriptor')},"
                f"{expected_descriptor})"
            )
        if bool(current_record.get("skippedRefunded", False)):
            return current_fee_accounting

        updated = refund_failed_internal_message_fee(
            current_fee_accounting,
            message_payload,
        )
        updated_record = updated.setdefault("message_value_effects", {})[occurrence]
        updated_record["skipped"] = True
        updated_record["skippedRefunded"] = True
        updated_record["skippedRefundAmount"] = refund_amount
        if refund_amount > 0:
            context.accounts_manager.credit_account_balance(
                context.transaction.to_address,
                refund_amount,
            )
        return updated

    updated_accounting = mutate_accounting(
        context.transaction.hash,
        refund_latest,
        commit=False,
    )
    context.transaction.data = dict(context.transaction.data or {})
    context.transaction.data[FEE_ACCOUNTING_KEY] = updated_accounting


def _emit_messages(
    context: TransactionContext,
    insert_transactions_data: list,
    receipt: dict,
    triggered_on: Literal["accepted", "finalized"],
    *,
    rollup_skipped: bool = False,
):
    helper_transaction_count = sum(
        insert_transaction_data[2] != TransactionType.SEND.value
        for insert_transaction_data in insert_transactions_data
    )
    if rollup_skipped:
        # No helper chain exists to mint child ids or deployment recipients.
        # Preserve the pre-rollup fallback: the local insertion layer derives
        # both, while external effects keep their durable occurrence id.
        tx_ids = [None] * helper_transaction_count
        recipients = [
            insert_transaction_data[0]
            for insert_transaction_data in insert_transactions_data
            if insert_transaction_data[2] != TransactionType.SEND.value
        ]
    else:
        if not isinstance(receipt, dict):
            raise InternalMessageEmissionError(
                f"InternalMessageEmissionFailed({context.transaction.hash},{triggered_on})"
            )
        tx_ids = receipt.get("tx_ids_hex")
        if not isinstance(tx_ids, list) or len(tx_ids) != helper_transaction_count:
            raise InternalMessageEmissionError(
                "InternalMessageEmissionCountMismatch"
                f"({context.transaction.hash},{triggered_on},"
                f"{helper_transaction_count},"
                f"{len(tx_ids) if isinstance(tx_ids, list) else 'missing'})"
            )
        recipients = receipt.get("recipients")
        if (
            not isinstance(recipients, list)
            or len(recipients) != helper_transaction_count
        ):
            raise InternalMessageEmissionError(
                "InternalMessageEmissionRecipientCountMismatch"
                f"({context.transaction.hash},{triggered_on},"
                f"{helper_transaction_count},"
                f"{len(recipients) if isinstance(recipients, list) else 'missing'})"
            )

    helper_index = 0
    for i, insert_transaction_data in enumerate(insert_transactions_data):
        is_external = insert_transaction_data[2] == TransactionType.SEND.value
        if is_external:
            # Consensus external messages are effects, not child consensus
            # transactions. The logical occurrence is their durable local id.
            transaction_hash = insert_transaction_data[5]
            actual_recipient = insert_transaction_data[0]
        else:
            transaction_hash = tx_ids[helper_index]
            actual_recipient = recipients[helper_index]
            helper_index += 1
        is_deploy = insert_transaction_data[2] == TransactionType.DEPLOY_CONTRACT.value
        if not (rollup_skipped and is_deploy) and (
            not isinstance(actual_recipient, str) or not is_address(actual_recipient)
        ):
            raise InternalMessageEmissionError(
                "InternalMessageEmissionInvalidRecipient"
                f"({context.transaction.hash},{triggered_on},{i})"
            )
        if not (rollup_skipped and is_deploy):
            actual_recipient = to_checksum_address(actual_recipient)
        if not is_external and _is_zero_transaction_hash(transaction_hash):
            _refund_skipped_internal_message(
                context,
                insert_transaction_data,
                triggered_on,
            )
            continue
        if is_deploy and not rollup_skipped:
            if actual_recipient == to_checksum_address(ZERO_ADDRESS):
                raise InternalMessageEmissionError(
                    "InternalMessageEmissionZeroDeploymentRecipient"
                    f"({context.transaction.hash},{triggered_on},{i})"
                )
            insert_transaction_data[1]["contract_address"] = actual_recipient
            context.accounts_manager.create_new_account_with_address(
                actual_recipient,
                commit=False,
            )
        elif (
            not is_deploy
            and actual_recipient.lower() != str(insert_transaction_data[0]).lower()
        ):
            raise InternalMessageEmissionError(
                "InternalMessageEmissionRecipientMismatch"
                f"({context.transaction.hash},{triggered_on},{i},"
                f"{insert_transaction_data[0]},{actual_recipient})"
            )
        # Determine execution_mode to cascade from parent transaction
        execution_mode_str = (
            context.transaction.execution_mode.value
            if isinstance(context.transaction.execution_mode, TransactionExecutionMode)
            else context.transaction.execution_mode
        )
        # Compute leader_only for backward compatibility
        leader_only = execution_mode_str != "NORMAL"

        context.transactions_processor.insert_transaction(
            context.transaction.to_address,  # new calls are done by the contract
            actual_recipient,
            insert_transaction_data[1],
            value=insert_transaction_data[4],
            type=insert_transaction_data[2],
            nonce=insert_transaction_data[3],
            leader_only=leader_only,  # Backward compat
            # Consensus creates every internal child at the protocol's
            # round-0 committee size. The child's minimum fee floor is priced
            # on the same basis, independent of the parent's current size.
            num_of_initial_validators=VALIDATORS_PER_ROUND[0],
            triggered_by_hash=context.transaction.hash,
            transaction_hash=transaction_hash,
            config_rotation_rounds=_child_config_rotation_rounds(
                context.transaction.config_rotation_rounds,
                insert_transaction_data[1],
            ),
            sim_config=(
                context.transaction.sim_config.to_dict()
                if context.transaction.sim_config
                else None
            ),
            triggered_on=triggered_on,
            execution_mode=execution_mode_str,  # Cascade execution mode
            origin_address=context.transaction.origin_address,
            # MessagePayments creates the whole batch in one EVM transaction.
            # Do not commit each Studio child independently; the worker commits
            # the parent phase, child rows, accounts, values, and fees together.
            commit=False,
        )
