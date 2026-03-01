# backend/consensus/base.py

DEFAULT_VALIDATORS_COUNT = 5
ACTIVATED_TRANSACTION_TIMEOUT = 900
MAX_IDLE_REPLACEMENTS = 5
DEFAULT_VALIDATOR_EXEC_TIMEOUT_SECONDS = ACTIVATED_TRANSACTION_TIMEOUT

import os
import asyncio
from typing import Callable, List, Iterable, Literal
import time
from abc import ABC, abstractmethod
import random
from copy import deepcopy
import json
import base64

from sqlalchemy.orm import Session
from backend.consensus.vrf import get_validators_for_transaction
from backend.database_handler.chain_snapshot import ChainSnapshot
from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.database_handler.contract_processor import ContractProcessor
from backend.database_handler.transactions_processor import (
    TransactionsProcessor,
    TransactionStatus,
)
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
from backend.node.create_nodes.providers import (
    get_default_provider_for,
    validate_provider,
)
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
from backend.rollup.consensus_service import ConsensusService

import backend.validators as validators
from backend.database_handler.validators_registry import ValidatorsRegistry
from backend.node.genvm.origin.public_abi import ResultCode
from backend.consensus.types import ConsensusResult, ConsensusRound
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
    has_appeal_capacity,
)
from backend.consensus.effect_executor import EffectExecutor
from backend.node.genvm import get_code_slot
from backend.node.genvm.error_codes import GenVMInternalError, GenVMErrorCode
from backend.node.base import Manager as GenVMManager

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

    pass


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

    # Remove data.state and truncate data.code if present
    data = redacted.get("data")
    if isinstance(data, dict):
        # Remove state entirely
        data.pop("state", None)

        # Truncate code to first 100 chars with indicator
        if "code" in data and isinstance(data["code"], str):
            code_len = len(data["code"])
            if code_len > 100:
                data["code"] = (
                    f"{data['code'][:100]}... [truncated, total length: {code_len}]"
                )

    return redacted


def _validator_exec_timeout_seconds() -> float:
    raw_timeout = os.getenv("CONSENSUS_VALIDATOR_EXEC_TIMEOUT_SECONDS")
    if raw_timeout is None:
        return float(DEFAULT_VALIDATOR_EXEC_TIMEOUT_SECONDS)
    try:
        timeout = float(raw_timeout)
    except ValueError:
        return float(DEFAULT_VALIDATOR_EXEC_TIMEOUT_SECONDS)
    if timeout <= 0:
        return float(DEFAULT_VALIDATOR_EXEC_TIMEOUT_SECONDS)
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
        self.consensus_service = consensus_service
        self.leader: dict = {}
        # Shared for the lifetime of this transaction context (leader + validators).
        self.shared_decoded_value_cache: dict[str, bytes] = {}
        self.shared_contract_snapshot_cache: dict[str, ContractSnapshot] = {}

        if self.transaction.type != TransactionType.SEND:
            if self.transaction.contract_snapshot:
                self.contract_snapshot = self.transaction.contract_snapshot
            else:
                self.contract_snapshot = self.contract_snapshot_factory(
                    self.transaction.to_address
                )

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

        # Check if the transaction is a fund_account call
        if not transaction.from_address is None:
            # Get the balance of the sender account
            from_balance = accounts_manager.get_account_balance(
                transaction.from_address
            )

            # Check if the sender has enough balance
            if from_balance < transaction.value:
                # Set the transaction status to UNDETERMINED if balance is insufficient
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

        # Check if the transaction is a burn call
        if not transaction.to_address is None:
            # Get the balance of the recipient account
            to_balance = accounts_manager.get_account_balance(transaction.to_address)

            # Update the balance of the recipient account
            accounts_manager.update_account_balance(
                transaction.to_address, to_balance + transaction.value
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
        time_based_finalization = (
            time.time()
            - transaction.timestamp_awaiting_finalization
            - transaction.appeal_processing_time
        ) > self.finality_window_time * (
            (1 - self.finality_window_appeal_failed_reduction)
            ** transaction.appeal_failed
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

        transactions_processor.set_transaction_appeal(transaction.hash, False)
        transaction.appealed = False

        used_leader_addresses = (
            ConsensusAlgorithm.get_used_leader_addresses_from_consensus_history(
                context.transactions_processor.get_transaction_by_hash(
                    context.transaction.hash
                )["consensus_history"]
            )
        )

        if not has_appeal_capacity(
            num_involved_validators=len(transaction.consensus_data.validators),
            num_used_leader_addresses=len(used_leader_addresses),
            num_total_validators=len(validators_snapshot.nodes),
        ):
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

        transactions_processor.set_transaction_appeal(transaction.hash, False)
        transaction.appealed = False

        if context.transaction.appeal_undetermined:
            context.transactions_processor.set_transaction_appeal_undetermined(
                context.transaction.hash, False
            )
            context.transaction.appeal_undetermined = False

        used_leader_addresses = (
            ConsensusAlgorithm.get_used_leader_addresses_from_consensus_history(
                context.transactions_processor.get_transaction_by_hash(
                    context.transaction.hash
                )["consensus_history"]
            )
        )

        if not has_appeal_capacity(
            num_involved_validators=len(transaction.leader_timeout_validators),
            num_used_leader_addresses=len(used_leader_addresses),
            num_total_validators=len(validators_snapshot.nodes),
        ):
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
        try:
            # Attempt to get extra validators for the appeal process
            _, context.remaining_validators = ConsensusAlgorithm.get_extra_validators(
                [x.validator.to_dict() for x in validators_snapshot.nodes],
                transaction.consensus_history,
                transaction.consensus_data,
                transaction.appeal_failed,
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
                    },
                    transaction_hash=context.transaction.hash,
                )
            )
            context.transactions_processor.set_transaction_appeal(
                context.transaction.hash, False
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
                        if context.transaction.contract_snapshot:
                            previous_contact_state = (
                                context.transaction.contract_snapshot.states["accepted"]
                            )
                        else:
                            previous_contact_state = {}

                        # Restore the contract state
                        context.contract_processor.update_contract_state(
                            context.transaction.to_address,
                            accepted_state=previous_contact_state,
                        )

                        # Reset the contract snapshot for the transaction
                        context.transactions_processor.set_transaction_contract_snapshot(
                            context.transaction.hash, None
                        )

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
    ):
        """
        Get extra validators for the appeal process according to the following formula:
        - when appeal_failed = 0, add n + 2 validators
        - when appeal_failed > 0, add (2 * appeal_failed * n + 1) + 2 validators
        Note that for appeal_failed > 0, the returned set contains the old validators
        from the previous appeal round and new validators.

        Selection of the extra validators:
        appeal_failed | PendingState | Reused validators | Extra selected     | Total
                      | validators   | from the previous | validators for the | validators
                      |              | appeal round      | appeal             |
        ----------------------------------------------------------------------------------
               0      |       n      |          0        |        n+2         |    2n+2
               1      |       n      |        n+2        |        n+1         |    3n+3
               2      |       n      |       2n+3        |         2n         |    5n+3
               3      |       n      |       4n+3        |         2n         |    7n+3
                              └───────┬──────┘  └─────────┬────────┘
                                      │                   |
        Validators after the ◄────────┘                   └──► Validators during the appeal
        appeal. This equals                                    for appeal_failed > 0
        the Total validators                                   = (2*appeal_failed*n+1)+2
        of the row above,                                      This is the formula from
        and are in consensus_data.                             above and it is what is
        For appeal_failed > 0                                  returned by this function
        = (2*appeal_failed-1)*n+3
        This is used to calculate n

        Args:
            all_validators (List[dict]): List of all validators.
            consensus_history (dict): Dictionary of consensus rounds results and status changes.
            consensus_data (ConsensusData): Data related to the consensus process.
            appeal_failed (int): Number of times the appeal has failed.

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

        # Remove used leaders from validator_map
        used_leader_addresses = (
            ConsensusAlgorithm.get_used_leader_addresses_from_consensus_history(
                consensus_history
            )
        )
        for used_leader_address in used_leader_addresses:
            if used_leader_address in validator_map:
                validator_map.pop(used_leader_address)

        # Set not_used_validators to the remaining validators in validator_map
        not_used_validators = list(validator_map.values())

        if len(not_used_validators) == 0:
            raise ValueError("No validators found")

        nb_current_validators = len(current_validators) + 1  # including the leader
        if appeal_failed == 0:
            # Calculate extra validators when no appeal has failed
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
        validator_map = {
            validator["address"]: validator for validator in all_validators
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
            validator_map.pop(receipt_address)
            for receipt_address in receipt_addresses
            if receipt_address in validator_map
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
        addresses = {validator["address"] for validator in validators}
        addresses.update(leader_addresses)

        # Get not used validators
        not_used_validators = [
            validator
            for validator in all_validators
            if validator["address"] not in addresses
        ]

        # Get new validator
        new_validator = get_validators_for_transaction(not_used_validators, 1)

        return new_validator + validators

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
                leader_receipt = consensus_round["leader_result"]
                if leader_receipt:
                    used_leader_addresses.update(
                        [leader_receipt[0]["node_config"]["address"]]
                    )

        # consensus_history does not contain the latest consensus_data
        if current_leader_receipt:
            used_leader_addresses.update(
                [current_leader_receipt.node_config["address"]]
            )

        return used_leader_addresses

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
        pass


class PendingState(TransactionState):
    """
    Class representing the pending state of a transaction.
    """

    async def handle(self, context):
        # Pre-effects: timestamp + reset rotation count
        pre_effects = decide_pending_pre(
            tx_hash=context.transaction.hash,
            appeal_leader_timeout=context.transaction.appeal_leader_timeout,
            appeal_undetermined=context.transaction.appeal_undetermined,
        )
        await EffectExecutor(context).execute(pre_effects)

        # Refresh transaction from DB
        context.transaction = Transaction.from_dict(
            context.transactions_processor.get_transaction_by_hash(
                context.transaction.hash
            )
        )

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

        # Get all validators
        if context.validators_snapshot is None:
            all_validators = None
        else:
            all_validators = [
                n.validator.to_dict() for n in context.validators_snapshot.nodes
            ]

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
            used_leader_addresses = (
                ConsensusAlgorithm.get_used_leader_addresses_from_consensus_history(
                    context.transaction.consensus_history
                )
            )
            assert context.validators_snapshot is not None
            old_validators = [
                x.validator.to_dict() for x in context.validators_snapshot.nodes
            ]
            context.involved_validators = ConsensusAlgorithm.add_new_validator(
                old_validators,
                context.transaction.leader_timeout_validators,
                used_leader_addresses,
            )

        else:
            if context.transaction.consensus_data:
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

        # Execute leader with replacement on fatal infrastructure failures
        for attempt in range(MAX_IDLE_REPLACEMENTS + 1):
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
            try:
                context.consensus_data.leader_receipt = [
                    await leader_node.exec_transaction(context.transaction)
                ]
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

        validator_exec_timeout_seconds = _validator_exec_timeout_seconds()

        # Execute the transaction with a semaphore to limit the number of concurrent validators
        sem = asyncio.Semaphore(8)

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
            timeout_ms = int(validator_exec_timeout_seconds * 1000)
            return Receipt(
                result=bytes([ResultCode.VM_ERROR]) + b"timeout",
                calldata=b"",
                gas_used=0,
                mode=ExecutionMode.VALIDATOR,
                contract_state={},
                node_config=validator_dict,
                eq_outputs={},
                execution_result=ExecutionResultStatus.ERROR,
                vote=None,
                genvm_result={
                    "stdout": "",
                    "stderr": (
                        "Validator execution exceeded "
                        f"{validator_exec_timeout_seconds:.3f}s"
                    ),
                    "error_code": "CONSENSUS_VALIDATOR_EXEC_TIMEOUT",
                    "raw_error": {
                        "causes": ["VALIDATOR_EXEC_TIMEOUT"],
                        # Mark as fatal so replacement validators are attempted.
                        "fatal": True,
                    },
                },
                processing_time=timeout_ms,
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
                eq_outputs={},
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
                for attempt in range(MAX_IDLE_REPLACEMENTS + 1):
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
                        timeout=validator_exec_timeout_seconds,
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

        validation_tasks = [
            run_single_validator(validator_dict, index)
            for index, validator_dict in enumerate(validators_to_run)
        ]
        context.validation_results = await asyncio.gather(*validation_tasks)

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
        if validation_by_leader:
            validators_to_emit = [context.leader] + context.remaining_validators
        else:
            validators_to_emit = list(context.remaining_validators)

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
        consensus_result = determine_consensus_from_votes(list(context.votes.values()))

        # Build vote reveal entries (IDLE→TIMEOUT for on-chain events)
        vote_reveal_entries = []
        for validation_result in context.validation_results:
            chain_vote = (
                Vote.TIMEOUT
                if validation_result.vote == Vote.IDLE
                else validation_result.vote
            )
            vote_reveal_entries.append((validation_result.node_config, int(chain_vote)))

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
            config_rotation_rounds=context.transaction.config_rotation_rounds,
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
            contract_code=(
                context.transaction.data.get("contract_code") if is_deploy else None
            ),
            code_slot_b64=(
                base64.b64encode(get_code_slot()).decode("ascii") if is_deploy else None
            ),
            to_address=context.transaction.to_address,
            leader_node_config=leader_receipt.node_config,
        )

        # Execute pre-effects (includes contract registration/update via executor)
        executor = EffectExecutor(context)
        await executor.execute(pre_effects)

        # Impure: triggered transaction processing (needs DB reads for nonce/accounts)
        if not context.transaction.appealed and execution_success:
            internal_messages_data, insert_transactions_data = _get_messages_data(
                context,
                leader_receipt.pending_transactions,
                "accepted",
            )

            rollup_receipt = context.consensus_service.emit_transaction_event(
                "emitTransactionAccepted",
                leader_receipt.node_config,
                context.transaction.hash,
                internal_messages_data,
            )

            _emit_messages(
                context, insert_transactions_data, rollup_receipt, "accepted"
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

            internal_messages_data, insert_transactions_data = _get_messages_data(
                context,
                leader_receipt.pending_transactions,
                "finalized",
            )

            rollup_receipt = context.consensus_service.emit_transaction_event(
                "emitTransactionFinalized",
                leader_receipt.node_config,
                context.transaction.hash,
                internal_messages_data,
            )

            _emit_messages(
                context, insert_transactions_data, rollup_receipt, "finalized"
            )

        await executor.execute(post_effects)


def _get_messages_data(
    context: TransactionContext,
    pending_transactions: Iterable[PendingTransaction],
    on: Literal["accepted", "finalized"],
):
    insert_transactions_data = []
    internal_messages_data = []
    for pending_transaction in filter(lambda t: t.on == on, pending_transactions):
        nonce = context.transactions_processor.get_transaction_count(
            context.transaction.to_address
        )
        data: dict
        transaction_type: TransactionType
        if pending_transaction.is_deploy():
            transaction_type = TransactionType.DEPLOY_CONTRACT
            new_contract_address: str
            if pending_transaction.salt_nonce == 0:
                # NOTE: this address is random, which doesn't 100% align with consensus spec
                new_contract_address = (
                    context.accounts_manager.create_new_account().address
                )
            else:
                from eth_utils.crypto import keccak
                from backend.node.types import Address
                from backend.node.base import get_simulator_chain_id

                arr = bytearray()
                arr.append(1)
                arr.extend(Address(context.transaction.to_address).as_bytes)
                arr.extend(
                    pending_transaction.salt_nonce.to_bytes(32, "big", signed=False)
                )
                arr.extend(get_simulator_chain_id().to_bytes(32, "big", signed=False))
                new_contract_address = Address(keccak(arr)[:20]).as_hex
                context.accounts_manager.create_new_account_with_address(
                    new_contract_address
                )
            pending_transaction.address = new_contract_address
            data = {
                "contract_address": new_contract_address,
                "contract_code": pending_transaction.code,
                "calldata": pending_transaction.calldata,
            }
        else:
            transaction_type = TransactionType.RUN_CONTRACT
            data = {
                "calldata": pending_transaction.calldata,
            }

        insert_transactions_data.append(
            [pending_transaction.address, data, transaction_type.value, nonce]
        )

        serializable_data = data.copy()
        if "contract_code" in serializable_data:
            serializable_data["contract_code"] = serializable_data[
                "contract_code"
            ].decode()
        # Encode binary calldata as base64 instead of trying to decode as UTF-8
        serializable_data["calldata"] = base64.b64encode(
            serializable_data["calldata"]
        ).decode("utf-8")

        internal_messages_data.append(
            {
                "sender": context.transaction.to_address,
                "recipient": pending_transaction.address,
                "data": json.dumps(serializable_data).encode(),
            }
        )

    return internal_messages_data, insert_transactions_data


def _emit_messages(
    context: TransactionContext,
    insert_transactions_data: list,
    receipt: dict,
    triggered_on: Literal["accepted", "finalized"],
):
    for i, insert_transaction_data in enumerate(insert_transactions_data):
        transaction_hash = (
            receipt["tx_ids_hex"][i] if receipt and "tx_ids_hex" in receipt else None
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
            insert_transaction_data[0],
            insert_transaction_data[1],
            value=0,  # we only handle EOA transfers at the moment, so no value gets transferred
            type=insert_transaction_data[2],
            nonce=insert_transaction_data[3],
            leader_only=leader_only,  # Backward compat
            num_of_initial_validators=context.transaction.num_of_initial_validators,
            triggered_by_hash=context.transaction.hash,
            transaction_hash=transaction_hash,
            config_rotation_rounds=context.transaction.config_rotation_rounds,
            sim_config=(
                context.transaction.sim_config.to_dict()
                if context.transaction.sim_config
                else None
            ),
            triggered_on=triggered_on,
            execution_mode=execution_mode_str,  # Cascade execution mode
        )
