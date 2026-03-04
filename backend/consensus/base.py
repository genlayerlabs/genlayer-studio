# backend/consensus/base.py

DEFAULT_VALIDATORS_COUNT = 5
DEFAULT_CONSENSUS_SLEEP_TIME = 5
ACTIVATED_TRANSACTION_TIMEOUT = 900
MAX_IDLE_REPLACEMENTS = 5
DEFAULT_VALIDATOR_EXEC_TIMEOUT_SECONDS = ACTIVATED_TRANSACTION_TIMEOUT

import os
import asyncio
import traceback
from typing import Callable, List, Iterable, Literal
import time
from abc import ABC, abstractmethod
import threading
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

    # Remove data.state if present
    data = redacted.get("data")
    if isinstance(data, dict):
        data.pop("state", None)

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
        consensus_sleep_time (int): Time in seconds for the consensus sleep time.
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
        self.consensus_sleep_time = DEFAULT_CONSENSUS_SLEEP_TIME
        # Simple tracking of what's currently being processed per contract
        self.processing_transactions: dict[str, str] = {}  # {contract_address: tx_hash}
        self.validators_manager = validators_manager
        self.genvm_manager = genvm_manager

    async def run_crawl_snapshot_loop(
        self,
        chain_snapshot_factory: Callable[
            [Session], ChainSnapshot
        ] = chain_snapshot_factory,
        transactions_processor_factory: Callable[
            [Session], TransactionsProcessor
        ] = transactions_processor_factory,
        stop_event: threading.Event = threading.Event(),
    ):
        """
        Run the loop to crawl snapshots.

        Args:
            chain_snapshot_factory (Callable[[Session], ChainSnapshot]): Creates snapshots of the blockchain state at specific points in time.
            transactions_processor_factory (Callable[[Session], TransactionsProcessor]): Creates processors to modify transactions.
            stop_event (threading.Event): Control signal to terminate the loop.
        """
        # Create a new event loop for crawling snapshots

        try:
            await self._crawl_snapshot(
                chain_snapshot_factory, transactions_processor_factory, stop_event
            )
        except BaseException as e:
            import traceback

            traceback.print_exception(e)
            raise

    async def _crawl_snapshot(
        self,
        chain_snapshot_factory: Callable[[Session], ChainSnapshot],
        transactions_processor_factory: Callable[[Session], TransactionsProcessor],
        stop_event: threading.Event,
    ):
        """
        Periodically check for stuck transactions and reset them to PENDING.
        This is now just a recovery mechanism, not for queuing transactions.
        Also cleans up orphaned entries in the processing_transactions tracker.

        Args:
            chain_snapshot_factory (Callable[[Session], ChainSnapshot]): Creates snapshots of the blockchain state at specific points in time.
            transactions_processor_factory (Callable[[Session], TransactionsProcessor]): Creates processors to modify transactions.
            stop_event (threading.Event): Control signal to terminate the loop.
        """
        from backend.consensus.monitoring import (
            monitored_task,
            get_monitor,
            OperationTimer,
        )
        from loguru import logger

        monitor = get_monitor()
        logger.debug("[CONSENSUS] Starting _crawl_snapshot recovery loop")

        async with monitored_task("crawl_snapshot_recovery") as task_id:
            iteration = 0
            total_reset = 0
            total_orphaned = 0

            while not stop_event.is_set():
                iteration += 1
                monitor.heartbeat(task_id, iteration, f"checking stuck transactions")

                try:
                    with OperationTimer(
                        "crawl_snapshot_iteration", warn_threshold=10.0
                    ):
                        with self.get_session() as session:
                            transactions_processor = transactions_processor_factory(
                                session
                            )

                            # DISABLED: Reset stuck transactions that have been processing for too long
                            # BUG: Uses created_at instead of status change time, resets old txs that waited in PENDING
                            # TODO: Add status_updated_at field or use proper timestamp before re-enabling
                            # # This handles ACTIVATED, PROPOSING, COMMITTING, REVEALING states
                            # with OperationTimer(
                            #     "reset_stuck_transactions", warn_threshold=5.0
                            # ):
                            #     reset_count = (
                            #         transactions_processor.reset_stuck_transactions(
                            #             timeout_seconds=ACTIVATED_TRANSACTION_TIMEOUT
                            #         )
                            #     )

                            # if reset_count > 0:
                            #     total_reset += reset_count
                            #     logger.warning(
                            #         f"[RECOVERY] Reset {reset_count} stuck transactions to PENDING "
                            #         f"(total this session: {total_reset})"
                            #     )
                            #     self.msg_handler.send_message(
                            #         LogEvent(
                            #             "consensus_event",
                            #             EventType.INFO,
                            #             EventScope.CONSENSUS,
                            #             f"Reset {reset_count} stuck transactions to PENDING",
                            #             {
                            #                 "count": reset_count,
                            #                 "total_reset": total_reset,
                            #             },
                            #         )
                            #     )

                            # Clean up orphaned entries in processing_transactions
                            # Check for transactions that are in terminal states but still tracked
                            orphaned_addresses = []
                            tracker_size = len(self.processing_transactions)

                            if tracker_size > 0:
                                logger.debug(
                                    f"[RECOVERY] Checking {tracker_size} tracked transactions for orphans"
                                )

                            for contract_address, tx_hash in list(
                                self.processing_transactions.items()
                            ):
                                tx = transactions_processor.get_transaction_by_hash(
                                    tx_hash
                                )
                                if tx:
                                    # If transaction is in a terminal state, remove from processing
                                    if tx["status"] in [
                                        TransactionStatus.ACCEPTED.value,
                                        TransactionStatus.FINALIZED.value,
                                        TransactionStatus.UNDETERMINED.value,
                                        TransactionStatus.CANCELED.value,
                                        TransactionStatus.LEADER_TIMEOUT.value,
                                        TransactionStatus.VALIDATORS_TIMEOUT.value,
                                        TransactionStatus.PENDING.value,  # If reset to PENDING
                                    ]:
                                        orphaned_addresses.append(contract_address)
                                        logger.debug(
                                            f"[RECOVERY] Found orphaned tx {tx_hash} in status {tx['status']} "
                                            f"for contract {contract_address}"
                                        )
                                else:
                                    # Transaction doesn't exist anymore, remove from processing
                                    orphaned_addresses.append(contract_address)
                                    logger.warning(
                                        f"[RECOVERY] Transaction {tx_hash} no longer exists for contract {contract_address}"
                                    )

                            # Remove orphaned entries
                            for contract_address in orphaned_addresses:
                                if contract_address in self.processing_transactions:
                                    del self.processing_transactions[contract_address]
                                    total_orphaned += 1
                                    monitor.track_processing(
                                        contract_address, None
                                    )  # Clear tracking
                                    self.msg_handler.send_message(
                                        LogEvent(
                                            "processing_tracker_cleaned",
                                            EventType.DEBUG,
                                            EventScope.CONSENSUS,
                                            f"Cleaned up processing tracker for contract address {contract_address}",
                                            {
                                                "contract_address": contract_address,
                                                "total_orphaned": total_orphaned,
                                            },
                                        )
                                    )

                            if iteration % 10 == 0:  # Log summary every 10 iterations
                                logger.debug(
                                    f"[RECOVERY] Crawl snapshot summary - "
                                    f"Iteration: {iteration}, "
                                    f"Total reset: {total_reset}, "
                                    f"Total orphaned: {total_orphaned}, "
                                    f"Currently tracking: {len(self.processing_transactions)}"
                                )

                except Exception as e:
                    logger.error(
                        f"[RECOVERY] Error in recovery loop iteration {iteration}: {e}"
                    )
                    logger.exception("Full traceback:")
                    monitor.record_error(task_id, str(e))

                await asyncio.sleep(
                    self.consensus_sleep_time * 10
                )  # Run recovery less frequently

            logger.debug(
                f"[RECOVERY] Crawl snapshot recovery loop stopped after {iteration} iterations"
            )

    async def run_process_pending_transactions_loop(
        self,
        chain_snapshot_factory: Callable[
            [Session], ChainSnapshot
        ] = chain_snapshot_factory,
        transactions_processor_factory: Callable[
            [Session], TransactionsProcessor
        ] = transactions_processor_factory,
        accounts_manager_factory: Callable[
            [Session], AccountsManager
        ] = accounts_manager_factory,
        contract_snapshot_factory: Callable[
            [str, Session, Transaction], ContractSnapshot
        ] = contract_snapshot_factory,
        contract_processor_factory: Callable[
            [Session], ContractProcessor
        ] = contract_processor_factory,
        node_factory: NodeFactory = node_factory,
        stop_event: threading.Event = threading.Event(),
    ):
        """
        Run the process pending transactions loop.

        Args:
            chain_snapshot_factory (Callable[[Session], ChainSnapshot]): Creates snapshots of the blockchain state at specific points in time.
            transactions_processor_factory (Callable[[Session], TransactionsProcessor]): Creates processors to modify transactions.
            accounts_manager_factory (Callable[[Session], AccountsManager]): Creates managers to handle account state.
            contract_snapshot_factory (Callable[[str, Session, Transaction], ContractSnapshot]): Creates snapshots of contract states.
            node_factory (Callable[[dict, ExecutionMode, ContractSnapshot, Receipt | None, MessageHandler, Callable[[str], ContractSnapshot]], Node]): Creates node instances that can execute contracts and process transactions.
            stop_event (threading.Event): Control signal to terminate the pending transactions process.
        """

        try:
            await self._process_pending_transactions(
                chain_snapshot_factory,
                transactions_processor_factory,
                accounts_manager_factory,
                contract_snapshot_factory,
                contract_processor_factory,
                node_factory,
                stop_event,
            )
        except BaseException as e:
            import traceback

            traceback.print_exception(e)
            raise

    async def _process_pending_transactions(
        self,
        chain_snapshot_factory: Callable[[Session], ChainSnapshot],
        transactions_processor_factory: Callable[[Session], TransactionsProcessor],
        accounts_manager_factory: Callable[[Session], AccountsManager],
        contract_snapshot_factory: Callable[
            [str, Session, Transaction], ContractSnapshot
        ],
        contract_processor_factory: Callable[[Session], ContractProcessor],
        node_factory: NodeFactory,
        stop_event: threading.Event,
    ):
        """
        Process pending transactions using direct database queries.
        Each contract gets its own continuous processing task.

        Args:
            chain_snapshot_factory (Callable[[Session], ChainSnapshot]): Creates snapshots of the blockchain state at specific points in time.
            transactions_processor_factory (Callable[[Session], TransactionsProcessor]): Creates processors to modify transactions.
            accounts_manager_factory (Callable[[Session], AccountsManager]): Creates managers to handle account state.
            contract_snapshot_factory (Callable[[str, Session, Transaction], ContractSnapshot]): Creates snapshots of contract states.
            node_factory (Callable[[dict, ExecutionMode, ContractSnapshot, Receipt | None, MessageHandler, Callable[[str], ContractSnapshot]], Node]): Creates node instances that can execute contracts and process transactions.
            stop_event (threading.Event): Control signal to terminate the pending transactions process.
        """
        from backend.consensus.monitoring import (
            monitored_task,
            get_monitor,
            OperationTimer,
        )
        from loguru import logger

        monitor = get_monitor()
        logger.debug("[CONSENSUS] Starting _process_pending_transactions loop")

        # Track active processing tasks per contract
        contract_tasks = {}  # {contract_address: Task}

        async def process_contract_continuously(contract_address):
            """Process all pending transactions for a single contract continuously."""
            from backend.consensus.monitoring import (
                monitored_task,
                get_monitor,
                OperationTimer,
            )
            from loguru import logger

            monitor = get_monitor()

            async with monitored_task(
                f"contract_processor", contract_address
            ) as task_id:
                logger.debug(
                    f"[TX_PROCESSOR] Starting continuous processing for contract {contract_address}"
                )

                # Handle special marker for burn transactions (to_address=None)
                actual_address = (
                    None if contract_address == "__zero_address__" else contract_address
                )

                tx_count = 0
                try:
                    while not stop_event.is_set():
                        monitor.heartbeat(
                            task_id, tx_count, f"checking for pending transactions"
                        )

                        # Check if there's already a transaction being processed
                        with self.get_session() as session:
                            transactions_processor = transactions_processor_factory(
                                session
                            )
                            from backend.database_handler.models import Transactions

                            # Handle None addresses (burn transactions) specially
                            if actual_address is None:
                                # Check for processing transactions with None to_address
                                processing_tx = (
                                    session.query(Transactions)
                                    .filter(
                                        Transactions.to_address.is_(None),
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

                                if processing_tx:
                                    logger.debug(
                                        f"[TX_PROCESSOR] Burn transaction already processing, waiting..."
                                    )
                                    await asyncio.sleep(self.consensus_sleep_time)
                                    continue

                                # Get oldest pending with None to_address
                                next_tx = (
                                    session.query(Transactions)
                                    .filter(
                                        Transactions.to_address.is_(None),
                                        Transactions.status
                                        == TransactionStatus.PENDING,
                                    )
                                    .order_by(Transactions.created_at)
                                    .first()
                                )

                                next_tx_data = (
                                    transactions_processor._parse_transaction_data(
                                        next_tx
                                    )
                                    if next_tx
                                    else None
                                )
                            else:
                                # Normal contract processing
                                processing_tx = transactions_processor.get_processing_transaction_for_contract(
                                    actual_address
                                )

                                if processing_tx:
                                    logger.debug(
                                        f"[TX_PROCESSOR] Contract {contract_address} has transaction in progress, waiting..."
                                    )
                                    await asyncio.sleep(self.consensus_sleep_time)
                                    continue

                                # Get the next pending transaction
                                next_tx_data = transactions_processor.get_oldest_pending_for_contract(
                                    actual_address
                                )

                            if not next_tx_data:
                                # No more pending transactions for this contract
                                logger.debug(
                                    f"[TX_PROCESSOR] No more pending transactions for contract {contract_address}, stopping processor"
                                )
                                break

                            # Mark as ACTIVATED
                            logger.debug(
                                f"[TX_PROCESSOR] Activating transaction {next_tx_data['hash']} for contract {contract_address}"
                            )
                            transactions_processor.update_transaction_status(
                                next_tx_data["hash"],
                                TransactionStatus.ACTIVATED,
                            )
                            session.commit()

                            # Track it using the marker for None addresses
                            self.processing_transactions[contract_address] = (
                                next_tx_data["hash"]
                            )
                            monitor.track_processing(
                                contract_address, next_tx_data["hash"]
                            )

                        # Process the transaction
                        tx_count += 1
                        try:
                            logger.debug(
                                f"[TX_PROCESSOR] Processing transaction {next_tx_data['hash']} (#{tx_count} for {contract_address})"
                            )
                            await self._process_single_transaction(
                                next_tx_data,
                                actual_address,  # Pass actual address (None for burn)
                                chain_snapshot_factory,
                                transactions_processor_factory,
                                accounts_manager_factory,
                                contract_snapshot_factory,
                                contract_processor_factory,
                                node_factory,
                            )
                            logger.info(
                                f"[TX_PROCESSOR] Successfully processed transaction {next_tx_data['hash']}"
                            )
                        except Exception as e:
                            logger.error(
                                f"[TX_PROCESSOR] Error processing transaction {next_tx_data['hash']}: {e}"
                            )
                            logger.exception("Full traceback:")
                            monitor.record_error(task_id, str(e))

                        # Small delay before checking for next transaction
                        await asyncio.sleep(self.consensus_sleep_time)

                finally:
                    # Clean up when done
                    if contract_address in contract_tasks:
                        del contract_tasks[contract_address]
                    logger.debug(
                        f"[TX_PROCESSOR] Stopped continuous processing for contract {contract_address} after {tx_count} transactions"
                    )

        # Monitor the main loop
        async with monitored_task("pending_tx_main_loop") as main_task_id:
            iteration = 0
            total_spawned = 0

            # Main loop to spawn contract processing tasks
            while not stop_event.is_set():
                iteration += 1
                monitor.heartbeat(
                    main_task_id, iteration, f"active tasks: {len(contract_tasks)}"
                )

                try:
                    with OperationTimer("check_pending_contracts", warn_threshold=2.0):
                        # Get contracts with pending transactions
                        with self.get_session() as session:
                            transactions_processor = transactions_processor_factory(
                                session
                            )
                            contracts_with_pending = (
                                transactions_processor.get_contracts_with_pending()
                            )

                        # Log active tasks status periodically
                        if iteration % 20 == 0:  # Every 10 seconds (0.5s * 20)
                            logger.debug(
                                f"[TX_MAIN] Status - Iteration: {iteration}, "
                                f"Active tasks: {len(contract_tasks)}, "
                                f"Total spawned: {total_spawned}, "
                                f"Contracts with pending: {len(contracts_with_pending)}"
                            )

                        # Spawn new tasks for contracts that don't have one
                        for contract_address in contracts_with_pending:
                            if contract_address not in contract_tasks:
                                # Create a new continuous processing task for this contract
                                total_spawned += 1
                                logger.debug(
                                    f"[TX_MAIN] Spawning processor #{total_spawned} for contract {contract_address} "
                                    f"(active tasks: {len(contract_tasks) + 1})"
                                )
                                task = asyncio.create_task(
                                    process_contract_continuously(contract_address)
                                )
                                contract_tasks[contract_address] = task

                        # Clean up completed tasks
                        completed_contracts = []
                        for address, task in contract_tasks.items():
                            if task.done():
                                completed_contracts.append(address)
                                # Check if task raised an exception
                                try:
                                    task.result()  # This will raise if the task failed
                                except Exception as e:
                                    logger.error(
                                        f"[TX_MAIN] Task for contract {address} failed: {e}"
                                    )

                        for address in completed_contracts:
                            del contract_tasks[address]
                            logger.debug(
                                f"[TX_MAIN] Cleaned up completed task for contract {address}"
                            )

                except Exception as e:
                    logger.error(
                        f"[TX_MAIN] Error in main loop iteration {iteration}: {e}"
                    )
                    logger.exception("Full traceback:")
                    monitor.record_error(main_task_id, str(e))

                # Check for new contracts periodically
                await asyncio.sleep(
                    0.5
                )  # Check every 0.5 seconds for new contracts to improve responsiveness

            # Clean up remaining tasks on shutdown
            logger.debug(
                f"[TX_MAIN] Shutting down, cancelling {len(contract_tasks)} active tasks"
            )
            for address, task in contract_tasks.items():
                task.cancel()
                logger.debug(f"[TX_MAIN] Cancelled task for contract {address}")

    async def _process_single_transaction(
        self,
        transaction: dict,
        address: str,
        chain_snapshot_factory,
        transactions_processor_factory,
        accounts_manager_factory,
        contract_snapshot_factory,
        contract_processor_factory,
        node_factory,
    ):
        """
        Process a single transaction through the consensus system.

        Args:
            transaction (dict): The transaction dictionary
            address (str): The contract address
            chain_snapshot_factory: Factory for creating chain snapshots
            transactions_processor_factory: Factory for creating transaction processors
            accounts_manager_factory: Factory for creating account managers
            contract_snapshot_factory: Factory for creating contract snapshots
            contract_processor_factory: Factory for creating contract processors
            node_factory: Factory for creating nodes
        """
        from backend.consensus.monitoring import (
            get_monitor,
            OperationTimer,
            monitored_session,
        )
        from loguru import logger

        monitor = get_monitor()
        tx_hash = transaction.get("hash", "unknown")
        tx_type = transaction.get("type", "unknown")
        start_time = time.time()

        logger.debug(
            f"[TX_EXEC] Starting execution of transaction {tx_hash} (type: {tx_type}, address: {address})"
        )

        try:
            # Convert dict to Transaction object
            current_transaction = Transaction.from_dict(transaction)

            # Create session for this transaction execution
            with self.get_session() as session:
                with monitored_session(session):
                    logger.debug(
                        f"[TX_EXEC] Created database session for transaction {tx_hash}"
                    )

                    transactions_processor = transactions_processor_factory(session)
                    chain_snapshot = chain_snapshot_factory(session)
                    accounts_manager = accounts_manager_factory(session)
                    contract_processor = contract_processor_factory(session)

                    # Build virtual validators if sim_config exists
                    virtual_validators = []
                    if (
                        current_transaction.sim_config
                        and current_transaction.sim_config.validators
                    ):
                        logger.debug(
                            f"[TX_EXEC] Building {len(current_transaction.sim_config.validators)} virtual validators"
                        )
                        with OperationTimer(
                            "virtual_validators_setup",
                            warn_threshold=3.0,
                            context={"tx_hash": tx_hash},
                        ):
                            for validator in current_transaction.sim_config.validators:
                                provider = validator.provider
                                model = validator.model
                                config = validator.config
                                plugin = validator.plugin
                                plugin_config = validator.plugin_config

                                if (
                                    config is None
                                    or plugin is None
                                    or plugin_config is None
                                ):
                                    llm_provider = get_default_provider_for(
                                        provider, model
                                    )
                                else:
                                    llm_provider = LLMProvider(
                                        provider=provider,
                                        model=model,
                                        config=config,
                                        plugin=plugin,
                                        plugin_config=plugin_config,
                                    )
                                    validate_provider(llm_provider)

                                account = accounts_manager.create_new_account()
                                virtual_validators.append(
                                    Validator(
                                        address=account.address,
                                        private_key=account.key.to_0x_hex(),
                                        stake=validator.stake,
                                        llmprovider=llm_provider,
                                    )
                                )

                    # Choose snapshot function based on virtual validators
                    if len(virtual_validators) > 0:
                        snapshot_func = self.validators_manager.temporal_snapshot
                        args = [virtual_validators]
                        logger.debug(
                            f"[TX_EXEC] Using temporal snapshot with {len(virtual_validators)} virtual validators"
                        )
                    else:
                        snapshot_func = self.validators_manager.snapshot
                        args = []

                    # Get validators snapshot for consensus (virtual or regular)
                    async with snapshot_func(*args) as validators_snapshot:
                        logger.debug(
                            f"[TX_EXEC] Executing consensus for transaction {tx_hash}"
                        )

                        # Execute the transaction through the consensus system
                        with OperationTimer(
                            "consensus_execution",
                            warn_threshold=60.0,
                            context={"tx_hash": tx_hash, "address": address},
                        ):
                            await self.exec_transaction(
                                current_transaction,
                                transactions_processor,
                                chain_snapshot,
                                accounts_manager,
                                lambda contract_address: contract_snapshot_factory(
                                    contract_address,
                                    session,
                                    current_transaction,
                                ),
                                contract_processor,
                                node_factory,
                                validators_snapshot,
                            )

                    # Commit the session after successful execution
                    session.commit()

            execution_time = time.time() - start_time
            logger.info(
                f"[TX_EXEC] Successfully completed transaction {tx_hash} in {execution_time:.2f}s"
            )

        except Exception as e:
            execution_time = time.time() - start_time
            logger.error(
                f"[TX_EXEC] Failed to execute transaction {tx_hash} after {execution_time:.2f}s: {str(e)}"
            )
            logger.exception("Full traceback:")

            self.msg_handler.send_message(
                LogEvent(
                    "transaction_execution_failed",
                    EventType.ERROR,
                    EventScope.CONSENSUS,
                    f"Failed to execute transaction {tx_hash}: {str(e)}",
                    {
                        "hash": tx_hash,
                        "error": str(e),
                        "address": address,
                        "execution_time": execution_time,
                    },
                    transaction_hash=tx_hash,
                )
            )
            try:
                with self.get_session() as recovery_session:
                    recovery_processor = transactions_processor_factory(
                        recovery_session
                    )
                    recovery_processor.update_transaction_status(
                        tx_hash,
                        TransactionStatus.PENDING,
                        update_current_status_changes=False,
                    )
            except Exception:
                logger.exception(
                    "[TX_EXEC] Failed to reset status after execution error",
                    tx_hash=tx_hash,
                )
        finally:
            # Always remove from processing_transactions when done
            tracker_key = address if address is not None else "__zero_address__"
            if tracker_key in self.processing_transactions:
                del self.processing_transactions[tracker_key]
                monitor.track_processing(tracker_key, None)  # Clear tracking
                logger.debug(
                    f"[TX_EXEC] Cleared processing tracker for address {tracker_key}"
                )

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
                    if (
                        (context.transaction.consensus_history is not None)
                        and (
                            "consensus_results" in context.transaction.consensus_history
                        )
                        and (
                            len(
                                context.transaction.consensus_history[
                                    "consensus_results"
                                ]
                            )
                            >= 1
                        )
                        and (
                            context.transaction.consensus_history["consensus_results"][
                                -1
                            ]["consensus_round"]
                            == ConsensusRound.VALIDATOR_TIMEOUT_APPEAL_SUCCESSFUL.value
                        )
                    ):
                        await self.rollback_transactions(
                            context, False
                        )  # Put on False because this happens in the pending queue, so we don't need to stop it
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

    async def run_appeal_window_loop(
        self,
        chain_snapshot_factory: Callable[
            [Session], ChainSnapshot
        ] = chain_snapshot_factory,
        transactions_processor_factory: Callable[
            [Session], TransactionsProcessor
        ] = transactions_processor_factory,
        accounts_manager_factory: Callable[
            [Session], AccountsManager
        ] = accounts_manager_factory,
        contract_snapshot_factory: Callable[
            [str, Session, Transaction], ContractSnapshot
        ] = contract_snapshot_factory,
        contract_processor_factory: Callable[
            [Session], ContractProcessor
        ] = contract_processor_factory,
        node_factory: NodeFactory = node_factory,
        stop_event: threading.Event = threading.Event(),
    ):
        """
        Run the loop to handle the appeal window.

        Args:
            chain_snapshot_factory (Callable[[Session], ChainSnapshot]): Creates snapshots of the blockchain state at specific points in time.
            transactions_processor_factory (Callable[[Session], TransactionsProcessor]): Creates processors to modify transactions.
            accounts_manager_factory (Callable[[Session], AccountsManager]): Creates managers to handle account state.
            contract_snapshot_factory (Callable[[str, Session, Transaction], ContractSnapshot]): Creates snapshots of contract states.
            node_factory (Callable[[dict, ExecutionMode, ContractSnapshot, Receipt | None, MessageHandler, Callable[[str], ContractSnapshot]], Node]): Creates node instances that can execute contracts and process transactions.
            stop_event (threading.Event): Control signal to terminate the appeal window process.
        """
        try:
            await self._appeal_window(
                chain_snapshot_factory,
                transactions_processor_factory,
                accounts_manager_factory,
                contract_snapshot_factory,
                contract_processor_factory,
                node_factory,
                stop_event,
            )
        except BaseException as e:
            import traceback

            traceback.print_exception(e)
            raise

    async def _appeal_window(
        self,
        chain_snapshot_factory: Callable[[Session], ChainSnapshot],
        transactions_processor_factory: Callable[[Session], TransactionsProcessor],
        accounts_manager_factory: Callable[[Session], AccountsManager],
        contract_snapshot_factory: Callable[
            [str, Session, Transaction], ContractSnapshot
        ],
        contract_processor_factory: Callable[[Session], ContractProcessor],
        node_factory: NodeFactory,
        stop_event: threading.Event,
    ):
        """
        Handle the appeal window for transactions, during which EOAs can challenge transaction results.

        Args:
            chain_snapshot_factory (Callable[[Session], ChainSnapshot]): Creates snapshots of the blockchain state at specific points in time.
            transactions_processor_factory (Callable[[Session], TransactionsProcessor]): Creates processors to modify transactions.
            accounts_manager_factory (Callable[[Session], AccountsManager]): Creates managers to handle account state.
            contract_snapshot_factory (Callable[[str, Session, Transaction], ContractSnapshot]): Creates snapshots of contract states.
            node_factory (Callable[[dict, ExecutionMode, ContractSnapshot, Receipt | None, MessageHandler, Callable[[str], ContractSnapshot]], Node]): Creates node instances that can execute contracts and process transactions.
            stop_event (threading.Event): Control signal to terminate the appeal window process.
        """
        from backend.consensus.monitoring import (
            monitored_task,
            get_monitor,
            OperationTimer,
        )
        from loguru import logger

        monitor = get_monitor()
        logger.debug("[CONSENSUS] Starting _appeal_window loop")

        async with monitored_task("appeal_window") as task_id:
            iteration = 0
            total_appeals = 0
            total_finalizations = 0

            while not stop_event.is_set():
                iteration += 1
                monitor.heartbeat(
                    task_id, iteration, "checking for appeals and finalizations"
                )

                try:
                    with OperationTimer("appeal_window_iteration", warn_threshold=15.0):
                        async with asyncio.TaskGroup() as tg:
                            with self.get_session() as session:
                                # Get the accepted and undetermined transactions per contract address
                                chain_snapshot = chain_snapshot_factory(session)
                                awaiting_finalization_transactions = (
                                    chain_snapshot.get_awaiting_finalization_transactions()
                                )

                                num_contracts = len(awaiting_finalization_transactions)
                                total_txs = sum(
                                    len(q)
                                    for q in awaiting_finalization_transactions.values()
                                )

                                if total_txs > 0:
                                    logger.debug(
                                        f"[APPEAL] Processing {total_txs} transactions across {num_contracts} contracts"
                                    )

                                # Iterate over the contracts
                                for (
                                    awaiting_finalization_queue
                                ) in awaiting_finalization_transactions.values():

                                    # Create a new session for each task so tasks can be run concurrently
                                    async def exec_appeal_window_with_session_handling(
                                        awaiting_finalization_queue: list[dict],
                                        captured_chain_snapshot: ChainSnapshot = chain_snapshot,
                                    ):
                                        with self.get_session() as task_session:
                                            transactions_processor = (
                                                transactions_processor_factory(
                                                    task_session
                                                )
                                            )

                                            # Go through the whole queue to check for appeals and finalizations
                                            for index, transaction in enumerate(
                                                awaiting_finalization_queue
                                            ):
                                                current_transaction = (
                                                    Transaction.from_dict(transaction)
                                                )

                                                # Check if the transaction is appealed
                                                if not current_transaction.appealed:

                                                    # Check if the transaction can be finalized
                                                    if self.can_finalize_transaction(
                                                        transactions_processor,
                                                        current_transaction,
                                                        index,
                                                        awaiting_finalization_queue,
                                                    ):

                                                        # Handle transactions that need to be finalized
                                                        await self.process_finalization(
                                                            current_transaction,
                                                            transactions_processor,
                                                            captured_chain_snapshot,
                                                            accounts_manager_factory(
                                                                task_session
                                                            ),
                                                            lambda contract_address: contract_snapshot_factory(
                                                                contract_address,
                                                                task_session,
                                                                current_transaction,
                                                            ),
                                                            contract_processor_factory(
                                                                task_session
                                                            ),
                                                            node_factory,
                                                        )
                                                        task_session.commit()

                                                else:
                                                    async with (
                                                        self.validators_manager.snapshot() as validators_snapshot
                                                    ):
                                                        # Handle transactions that are appealed
                                                        if (
                                                            current_transaction.status
                                                            == TransactionStatus.UNDETERMINED
                                                        ):
                                                            # Leader appeal
                                                            await self.process_leader_appeal(
                                                                current_transaction,
                                                                transactions_processor,
                                                                captured_chain_snapshot,
                                                                accounts_manager_factory(
                                                                    task_session
                                                                ),
                                                                lambda contract_address: contract_snapshot_factory(
                                                                    contract_address,
                                                                    task_session,
                                                                    current_transaction,
                                                                ),
                                                                contract_processor_factory(
                                                                    task_session
                                                                ),
                                                                node_factory,
                                                                validators_snapshot,
                                                            )
                                                            task_session.commit()
                                                        elif (
                                                            current_transaction.status
                                                            == TransactionStatus.LEADER_TIMEOUT
                                                        ):
                                                            # Leader timeout
                                                            await self.process_leader_timeout_appeal(
                                                                current_transaction,
                                                                transactions_processor,
                                                                captured_chain_snapshot,
                                                                accounts_manager_factory(
                                                                    task_session
                                                                ),
                                                                lambda contract_address: contract_snapshot_factory(
                                                                    contract_address,
                                                                    task_session,
                                                                    current_transaction,
                                                                ),
                                                                contract_processor_factory(
                                                                    task_session
                                                                ),
                                                                node_factory,
                                                                validators_snapshot,
                                                            )
                                                            task_session.commit()
                                                        else:
                                                            # Validator appeal
                                                            await self.process_validator_appeal(
                                                                current_transaction,
                                                                transactions_processor,
                                                                captured_chain_snapshot,
                                                                accounts_manager_factory(
                                                                    task_session
                                                                ),
                                                                lambda contract_address: contract_snapshot_factory(
                                                                    contract_address,
                                                                    task_session,
                                                                    current_transaction,
                                                                ),
                                                                contract_processor_factory(
                                                                    task_session
                                                                ),
                                                                node_factory,
                                                                validators_snapshot,
                                                            )
                                                            task_session.commit()

                                    tg.create_task(
                                        exec_appeal_window_with_session_handling(
                                            awaiting_finalization_queue
                                        )
                                    )

                        # Log periodic summary
                    if iteration % 20 == 0:  # Every 100 seconds (5s * 20)
                        logger.debug(
                            f"[APPEAL] Summary - Iteration: {iteration}, "
                            f"Total appeals: {total_appeals}, "
                            f"Total finalizations: {total_finalizations}"
                        )

                except Exception as e:
                    logger.error(f"[APPEAL] Error in iteration {iteration}: {e}")
                    logger.exception("Full traceback:")
                    monitor.record_error(task_id, str(e))

                await asyncio.sleep(self.consensus_sleep_time)

            logger.debug(
                f"[APPEAL] Appeal window loop stopped after {iteration} iterations"
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

        if len(transaction.consensus_data.validators) + len(
            used_leader_addresses
        ) >= len(validators_snapshot.nodes):
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
                    await self.rollback_transactions(context, True)
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

        if len(transaction.leader_timeout_validators) + len(
            used_leader_addresses
        ) >= len(validators_snapshot.nodes):
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
                    await self.rollback_transactions(context, True)
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
                        await self.rollback_transactions(context, True)

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

                    # Transaction will be picked up by _crawl_snapshot
                    break
                state = next_state

    async def rollback_transactions(
        self, context: TransactionContext, stop_pending_queue
    ):
        """
        Rollback newer transactions.
        In the simplified system, we just need to reset future transactions to PENDING.
        """
        # Rollback all future transactions for the current contract
        address = context.transaction.to_address

        # Clear the processing tracker for this address if it exists
        # This allows the next pending transaction to be picked up
        tracker_key = address if address is not None else "__zero_address__"
        if tracker_key in self.processing_transactions:
            del self.processing_transactions[tracker_key]

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
        """
        Handle the pending state transition.

        Args:
            context (TransactionContext): The context of the transaction.

        Returns:
            TransactionState | None: The ProposingState or None if the transaction is already in process, when it is a transaction or when there are no validators.
        """
        # Record timestamp for entering PENDING state
        context.transactions_processor.add_state_timestamp(
            context.transaction.hash, "PENDING"
        )

        context.transactions_processor.reset_transaction_rotation_count(
            context.transaction.hash
        )

        # Transactions that are put back to pending are processed again, so we need to get the latest data of the transaction
        context.transaction = Transaction.from_dict(
            context.transactions_processor.get_transaction_by_hash(
                context.transaction.hash
            )
        )

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

        # If transaction is a transfer, execute it
        # TODO: consider when the transfer involves a contract account, bridging, etc.
        if context.transaction.type == TransactionType.SEND:
            await ConsensusAlgorithm.execute_transfer(
                context.transaction,
                context.transactions_processor,
                context.accounts_manager,
                context.msg_handler,
            )
            return None

        # Retrieve all validators from the snapshot
        if context.validators_snapshot is None:
            all_validators = None
        else:
            all_validators = [
                n.validator.to_dict() for n in context.validators_snapshot.nodes
            ]

        # Check if there are validators available
        if not all_validators:
            context.msg_handler.send_message(
                LogEvent(
                    "consensus_event",
                    EventType.ERROR,
                    EventScope.CONSENSUS,
                    "No validators found to process transaction",
                    {
                        "transaction_hash": context.transaction.hash,
                    },
                    transaction_hash=context.transaction.hash,
                )
            )
            raise NoValidatorsAvailableError(
                f"No validators available for transaction {context.transaction.hash}"
            )

        # Determine the involved validators based on whether the transaction is appealed
        if (
            context.transaction.appealed
            or context.transaction.appeal_validators_timeout
        ):
            # If the transaction is appealed and has consensus_data, remove the old leader
            if context.transaction.consensus_data is not None:
                context.involved_validators, _ = (
                    ConsensusAlgorithm.get_validators_from_consensus_data(
                        all_validators, context.transaction.consensus_data, False
                    )
                )
            else:
                # Corrupted state (appealed but no consensus_data)
                # Select new validators since we can't reuse old ones
                context.involved_validators = get_validators_for_transaction(
                    all_validators, context.transaction.num_of_initial_validators
                )

            # Reset the transaction appeal status
            context.transactions_processor.set_transaction_appeal(
                context.transaction.hash, False
            )
            context.transaction.appealed = False

            context.transaction.appeal_validators_timeout = context.transactions_processor.set_transaction_appeal_validators_timeout(
                context.transaction.hash, False
            )

        elif context.transaction.appeal_undetermined:
            # Guard against corrupted state
            if context.transaction.consensus_data is None:
                context.transactions_processor.set_transaction_appeal_undetermined(
                    context.transaction.hash, False
                )
                context.transaction.appeal_undetermined = False
                # Select new validators since we can't reuse old ones
                context.involved_validators = get_validators_for_transaction(
                    all_validators, context.transaction.num_of_initial_validators
                )
            else:
                # Add n+2 validators, remove the old leader
                current_validators, extra_validators = (
                    ConsensusAlgorithm.get_extra_validators(
                        all_validators,
                        context.transaction.consensus_history,
                        context.transaction.consensus_data,
                        0,
                    )
                )
                context.involved_validators = current_validators + extra_validators

                # Send events in rollup to communicate the appeal is started
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
            # If there was no validator appeal or leader appeal
            if context.transaction.consensus_data:
                # Transaction was rolled back, try to reuse the validators and leader
                context.involved_validators, _ = (
                    ConsensusAlgorithm.get_validators_from_consensus_data(
                        all_validators, context.transaction.consensus_data, True
                    )
                )

                # If original validators no longer exist, select new ones
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
                    # Clear the old consensus data since validators changed
                    context.transaction.consensus_data = None
                    # Select new validators
                    context.involved_validators = get_validators_for_transaction(
                        all_validators, context.transaction.num_of_initial_validators
                    )

            else:
                # Transaction was never executed, get the default number of validators for the transaction
                context.involved_validators = get_validators_for_transaction(
                    all_validators, context.transaction.num_of_initial_validators
                )

        # Transition to the ProposingState
        return ProposingState(
            activate=(
                False
                if context.transaction.appeal_undetermined
                or context.transaction.appeal_leader_timeout
                else True
            )
        )


class ProposingState(TransactionState):
    """
    Class representing the proposing state of a transaction.
    """

    def __init__(self, activate: bool = False):
        self.activate = activate

    async def handle(self, context):
        """
        Handle the proposing state transition.

        Args:
            context (TransactionContext): The context of the transaction.

        Returns:
            TransactionState: The CommittingState or UndeterminedState if all rotations are done.
        """
        # Record timestamp for entering PROPOSING state
        context.transactions_processor.add_state_timestamp(
            context.transaction.hash, "PROPOSING"
        )

        # Dispatch a transaction status update to PROPOSING
        await ConsensusAlgorithm.dispatch_transaction_status_update(
            context.transactions_processor,
            context.transaction.hash,
            TransactionStatus.PROPOSING,
            context.msg_handler,
        )

        # The leader is elected randomly
        random.shuffle(context.involved_validators)

        # Unpack the leader and validators
        [context.leader, *context.remaining_validators] = context.involved_validators

        context.transactions_processor.add_state_timestamp(
            context.transaction.hash, "PROPOSING.VALIDATORS_SELECTED"
        )
        # Determine execution mode and handle validator selection accordingly
        execution_mode = TransactionExecutionMode(
            context.transaction.execution_mode.value
            if isinstance(context.transaction.execution_mode, TransactionExecutionMode)
            else context.transaction.execution_mode
        )

        # For non-NORMAL modes, clear validators (leader handles everything)
        if execution_mode != TransactionExecutionMode.NORMAL:
            context.remaining_validators = []

        # Send event in rollup to communicate the transaction is activated
        if self.activate:
            context.consensus_service.emit_transaction_event(
                "emitTransactionActivated",
                context.leader,
                context.transaction.hash,
                context.leader["address"],
                [context.leader["address"]]
                + [v["address"] for v in context.remaining_validators],
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
        context.transactions_processor.set_transaction_result(
            context.transaction.hash,
            context.consensus_data.to_dict(strip_contract_state=True),
        )

        # Set the validators and other context attributes
        context.num_validators = len(context.remaining_validators) + 1

        # Check if the leader timed out
        if (
            context.consensus_data.leader_receipt[0].result[0] == ResultCode.VM_ERROR
        ) and (context.consensus_data.leader_receipt[0].result[1:] == b"timeout"):
            return LeaderTimeoutState()

        if context.transaction.appeal_leader_timeout:
            # Successful leader timeout appeal
            context.transactions_processor.set_transaction_timestamp_awaiting_finalization(
                context.transaction.hash
            )
            context.transactions_processor.reset_transaction_appeal_processing_time(
                context.transaction.hash
            )
            context.transactions_processor.set_transaction_timestamp_appeal(
                context.transaction.hash, None
            )
            context.transaction.timestamp_appeal = None

        context.transactions_processor.set_leader_timeout_validators(
            context.transaction.hash, []
        )

        # Send event in rollup to communicate the receipt proposed
        context.consensus_service.emit_transaction_event(
            "emitTransactionReceiptProposed",
            context.leader,
            context.transaction.hash,
        )

        # Handle execution mode-specific transitions
        if execution_mode == TransactionExecutionMode.LEADER_ONLY:
            # LEADER_ONLY: Skip ALL validation, go directly to AcceptedState
            # Set the leader's vote as AGREE (no validation needed)
            context.consensus_data.votes = {context.leader["address"]: Vote.AGREE.value}
            context.votes = {context.leader["address"]: Vote.AGREE.value}
            context.consensus_data.validators = []
            context.validation_results = []

            # Update transaction with consensus data
            context.transactions_processor.set_transaction_result(
                context.transaction.hash,
                context.consensus_data.to_dict(strip_contract_state=True),
            )

            # Skip CommittingState/RevealingState entirely
            return AcceptedState()

        # LEADER_SELF_VALIDATOR or NORMAL mode: continue to CommittingState
        # LEADER_SELF_VALIDATOR will have the leader validate themselves
        # NORMAL will have multiple validators validate
        return CommittingState()


class CommittingState(TransactionState):
    """
    Class representing the committing state of a transaction.
    """

    async def handle(self, context):
        """
        Handle the committing state transition. There are no encrypted votes.

        Args:
            context (TransactionContext): The context of the transaction.

        Returns:
            TransactionState: The RevealingState.
        """
        # Record timestamp for entering COMMITTING state
        context.transactions_processor.add_state_timestamp(
            context.transaction.hash, "COMMITTING"
        )

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

        # Dispatch a transaction status update to COMMITTING
        await ConsensusAlgorithm.dispatch_transaction_status_update(
            context.transactions_processor,
            context.transaction.hash,
            TransactionStatus.COMMITTING,
            context.msg_handler,
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

        # Send events in rollup to communicate the votes are committed
        if validation_by_leader:
            validators_to_emit = [context.leader] + context.remaining_validators
        else:
            validators_to_emit = context.remaining_validators
        for i, validator in enumerate(validators_to_emit):
            context.consensus_service.emit_transaction_event(
                "emitVoteCommitted",
                validator,
                context.transaction.hash,
                validator["address"],
                True if i == len(validators_to_emit) - 1 else False,
            )
        context.transactions_processor.set_transaction_timestamp_last_vote(
            context.transaction.hash
        )

        # Transition to the RevealingState
        return RevealingState()


class RevealingState(TransactionState):
    """
    Class representing the revealing state of a transaction.
    """

    async def handle(self, context):
        """
        Handle the revealing state transition.

        Args:
            context (TransactionContext): The context of the transaction.

        Returns:
            TransactionState | None: The AcceptedState or ProposingState or None if the transaction is successfully appealed.
        """
        # Record timestamp for entering REVEALING state
        context.transactions_processor.add_state_timestamp(
            context.transaction.hash, "REVEALING"
        )

        # Update the transaction status to REVEALING and await Redis publish completion
        await ConsensusAlgorithm.dispatch_transaction_status_update(
            context.transactions_processor,
            context.transaction.hash,
            TransactionStatus.REVEALING,
            context.msg_handler,
        )

        # Process each validation result and update the context
        for i, validation_result in enumerate(context.validation_results):
            # Store the vote from each validator node
            context.votes[validation_result.node_config["address"]] = (
                validation_result.vote.value
            )

        # Determine the consensus result
        votes_list = list(context.votes.values())
        consensus_result = determine_consensus_from_votes(votes_list)

        # Send event in rollup to communicate the votes are revealed
        # Vote.IDLE maps to Vote.TIMEOUT for on-chain events (Solidity
        # VoteType enum doesn't have IDLE)
        for i, validation_result in enumerate(context.validation_results):
            last_vote = i == len(context.validation_results) - 1
            chain_vote = (
                Vote.TIMEOUT
                if validation_result.vote == Vote.IDLE
                else validation_result.vote
            )
            context.consensus_service.emit_transaction_event(
                "emitVoteRevealed",
                validation_result.node_config,
                context.transaction.hash,
                validation_result.node_config["address"],
                int(chain_vote),
                last_vote,
                int(consensus_result) if last_vote else int(ConsensusResult.IDLE),
            )
        context.transactions_processor.set_transaction_timestamp_last_vote(
            context.transaction.hash
        )

        # Set the leader's validation receipt
        if (
            context.consensus_data.leader_receipt
            and len(context.consensus_data.leader_receipt) == 1
        ):
            context.consensus_data.leader_receipt.append(context.validation_results[0])
            context.validation_results = context.validation_results[1:]

        if (
            context.transaction.appealed
            or context.transaction.appeal_validators_timeout
        ):

            # Update the consensus results with all new votes and validators
            context.consensus_data.votes = (
                context.transaction.consensus_data.votes | context.votes
            )

            # Overwrite old validator results based on the number of appeal failures
            if context.transaction.appeal_failed == 0:
                context.consensus_data.validators = (
                    context.transaction.consensus_data.validators
                    + context.validation_results
                )

            elif context.transaction.appeal_failed == 1:
                n = (len(context.transaction.consensus_data.validators) - 1) // 2
                context.consensus_data.validators = (
                    context.transaction.consensus_data.validators[: n - 1]
                    + context.validation_results
                )

            else:
                n = len(context.validation_results) - (
                    len(context.transaction.consensus_data.validators) + 1
                )
                context.consensus_data.validators = (
                    context.transaction.consensus_data.validators[: n - 1]
                    + context.validation_results
                )

            if (context.transaction.appealed) and (
                consensus_result == ConsensusResult.MAJORITY_AGREE
            ):
                return AcceptedState()

            elif (context.transaction.appeal_validators_timeout) and (
                consensus_result == ConsensusResult.TIMEOUT
            ):
                return ValidatorsTimeoutState()

            else:
                # Appeal succeeded, set the status to PENDING and reset the appeal_failed counter
                context.transactions_processor.set_transaction_result(
                    context.transaction.hash,
                    context.consensus_data.to_dict(strip_contract_state=True),
                )

                context.transactions_processor.set_transaction_appeal_failed(
                    context.transaction.hash,
                    0,
                )
                context.transactions_processor.update_consensus_history(
                    context.transaction.hash,
                    (
                        ConsensusRound.VALIDATOR_APPEAL_SUCCESSFUL
                        if context.transaction.appealed
                        else ConsensusRound.VALIDATOR_TIMEOUT_APPEAL_SUCCESSFUL
                    ),
                    None,
                    context.validation_results,
                )

                # Reset the appeal processing time
                context.transactions_processor.reset_transaction_appeal_processing_time(
                    context.transaction.hash
                )
                context.transactions_processor.set_transaction_timestamp_appeal(
                    context.transaction.hash, None
                )

                return ConsensusRound.VALIDATOR_APPEAL_SUCCESSFUL

        else:
            # Not appealed, update consensus data with current votes and validators
            context.consensus_data.votes = context.votes
            context.consensus_data.validators = context.validation_results

            if consensus_result == ConsensusResult.MAJORITY_AGREE:
                return AcceptedState()

            elif consensus_result == ConsensusResult.TIMEOUT:
                return ValidatorsTimeoutState()

            elif consensus_result in [
                ConsensusResult.MAJORITY_DISAGREE,
                ConsensusResult.NO_MAJORITY,
            ]:
                # If all rotations are done and no consensus is reached, transition to UndeterminedState
                if context.rotation_count >= context.transaction.config_rotation_rounds:
                    if context.transaction.appeal_leader_timeout:
                        context.transaction.appeal_leader_timeout = context.transactions_processor.set_transaction_appeal_leader_timeout(
                            context.transaction.hash, False
                        )
                    return UndeterminedState()

                else:
                    if context.transaction.appeal_leader_timeout:
                        context.transaction.appeal_leader_timeout = context.transactions_processor.set_transaction_appeal_leader_timeout(
                            context.transaction.hash, False
                        )
                    used_leader_addresses = ConsensusAlgorithm.get_used_leader_addresses_from_consensus_history(
                        context.transactions_processor.get_transaction_by_hash(
                            context.transaction.hash
                        )["consensus_history"],
                        context.consensus_data.leader_receipt[0],
                    )
                    # Add a new validator to the list of current validators when a rotation happens
                    try:
                        assert context.validators_snapshot is not None
                        old_validators = [
                            x.validator.to_dict()
                            for x in context.validators_snapshot.nodes
                        ]

                        context.involved_validators = (
                            ConsensusAlgorithm.add_new_validator(
                                old_validators,
                                context.remaining_validators,
                                used_leader_addresses,
                            )
                        )
                    except ValueError as e:
                        # No more validators
                        context.msg_handler.send_message(
                            LogEvent(
                                "consensus_event",
                                EventType.ERROR,
                                EventScope.CONSENSUS,
                                str(e),
                                {
                                    "transaction_hash": context.transaction.hash,
                                },
                                transaction_hash=context.transaction.hash,
                            )
                        )
                        return UndeterminedState()

                    context.rotation_count += 1
                    context.transactions_processor.increase_transaction_rotation_count(
                        context.transaction.hash
                    )

                    # Log the failure to reach consensus and transition to ProposingState
                    context.msg_handler.send_message(
                        LogEvent(
                            "consensus_event",
                            EventType.INFO,
                            EventScope.CONSENSUS,
                            "Majority disagreement, rotating the leader",
                            {
                                "transaction_hash": context.transaction.hash,
                            },
                            transaction_hash=context.transaction.hash,
                        )
                    )

                    # Send events in rollup to communicate the leader rotation
                    context.consensus_service.emit_transaction_event(
                        "emitTransactionLeaderRotated",
                        context.consensus_data.leader_receipt[0].node_config,
                        context.transaction.hash,
                        context.involved_validators[0]["address"],
                    )

                    # Update the consensus history
                    if context.transaction.appeal_undetermined:
                        consensus_round = ConsensusRound.LEADER_ROTATION_APPEAL
                    else:
                        consensus_round = ConsensusRound.LEADER_ROTATION
                    context.transactions_processor.update_consensus_history(
                        context.transaction.hash,
                        consensus_round,
                        context.consensus_data.leader_receipt,
                        context.validation_results,
                    )
                    return ProposingState()

            else:
                raise ValueError("Invalid consensus result")


class AcceptedState(TransactionState):
    """
    Class representing the accepted state of a transaction.
    """

    async def handle(self, context):
        """
        Handle the accepted state transition.

        Args:
            context (TransactionContext): The context of the transaction.

        Returns:
            None: The transaction is accepted.
        """
        # Record timestamp for entering ACCEPTED state
        context.transactions_processor.add_state_timestamp(
            context.transaction.hash, "ACCEPTED"
        )

        # When appeal fails, the appeal window is not reset
        if context.transaction.appeal_undetermined:
            consensus_round = ConsensusRound.LEADER_APPEAL_SUCCESSFUL
            context.transactions_processor.set_transaction_timestamp_awaiting_finalization(
                context.transaction.hash
            )
            context.transactions_processor.reset_transaction_appeal_processing_time(
                context.transaction.hash
            )
            context.transactions_processor.set_transaction_timestamp_appeal(
                context.transaction.hash, None
            )
            context.transaction.timestamp_appeal = None
            context.transactions_processor.set_transaction_appeal_failed(
                context.transaction.hash,
                0,
            )
        elif not context.transaction.appealed:
            consensus_round = ConsensusRound.ACCEPTED
            context.transactions_processor.set_transaction_timestamp_awaiting_finalization(
                context.transaction.hash
            )
        else:
            consensus_round = ConsensusRound.VALIDATOR_APPEAL_FAILED
            # Set the transaction appeal status to False
            context.transactions_processor.set_transaction_appeal(
                context.transaction.hash, False
            )

            # Increment the appeal processing time when the transaction was appealed
            context.transactions_processor.set_transaction_appeal_processing_time(
                context.transaction.hash
            )

            # Appeal failed, increment the appeal_failed counter
            context.transactions_processor.set_transaction_appeal_failed(
                context.transaction.hash,
                context.transaction.appeal_failed + 1,
            )

        # Set the transaction result
        context.transactions_processor.set_transaction_result(
            context.transaction.hash,
            context.consensus_data.to_dict(strip_contract_state=True),
        )

        context.transactions_processor.update_consensus_history(
            context.transaction.hash,
            consensus_round,
            (
                None
                if consensus_round == ConsensusRound.VALIDATOR_APPEAL_FAILED
                else context.consensus_data.leader_receipt
            ),
            context.validation_results,
            TransactionStatus.ACCEPTED,
        )

        # Send a message indicating consensus was reached
        context.msg_handler.send_message(
            LogEvent(
                "consensus_event",
                EventType.SUCCESS,
                EventScope.CONSENSUS,
                "Reached consensus",
                {
                    "transaction_hash": context.transaction.hash,
                    "consensus_data": _redact_consensus_data_for_log(
                        context.consensus_data.to_dict()
                    ),
                },
                transaction_hash=context.transaction.hash,
            )
        )

        # Retrieve the leader's receipt from the consensus data
        leader_receipt = context.consensus_data.leader_receipt[0]

        # Extract contract_state before it gets stripped from receipts for database storage
        # This is needed to update the CurrentState table (source of truth for contract state)
        accepted_contract_state = leader_receipt.contract_state

        # Do not deploy or update the contract if validator appeal failed
        if not context.transaction.appealed:
            # Set the contract snapshot for the transaction for a future rollback
            if not context.transaction.contract_snapshot:
                context.transactions_processor.set_transaction_contract_snapshot(
                    context.transaction.hash, context.contract_snapshot.to_dict()
                )

            # Do not deploy or update the contract if the execution failed
            if leader_receipt.execution_result == ExecutionResultStatus.SUCCESS:
                # Register contract if it is a new contract
                if context.transaction.type == TransactionType.DEPLOY_CONTRACT:
                    code_slot_b64 = base64.b64encode(get_code_slot()).decode("ascii")
                    new_contract = {
                        "id": context.transaction.data["contract_address"],
                        "data": {
                            "state": {
                                "accepted": accepted_contract_state,
                                "finalized": {
                                    code_slot_b64: accepted_contract_state.get(
                                        code_slot_b64, b""
                                    )
                                },
                            },
                        },
                    }
                    try:
                        context.contract_processor.register_contract(new_contract)

                        # Send a message indicating successful contract deployment
                        context.msg_handler.send_message(
                            LogEvent(
                                "deployed_contract",
                                EventType.SUCCESS,
                                EventScope.GENVM,
                                "Contract deployed",
                                _redact_contract_for_log(new_contract),
                                transaction_hash=context.transaction.hash,
                            )
                        )
                    except Exception as e:
                        # Log the error but continue with the transaction processing
                        context.msg_handler.send_message(
                            LogEvent(
                                "consensus_event",
                                EventType.ERROR,
                                EventScope.CONSENSUS,
                                "Failed to register contract",
                                {
                                    "transaction_hash": context.transaction.hash,
                                    "error": str(e),
                                },
                                transaction_hash=context.transaction.hash,
                            )
                        )
                # Update contract state if it is an existing contract
                else:
                    context.contract_processor.update_contract_state(
                        context.transaction.to_address,
                        accepted_state=accepted_contract_state,
                    )

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

                # Insert triggered transactions BEFORE updating parent status
                # to ACCEPTED.  This prevents a race where external callers see
                # the ACCEPTED status but the triggered_transactions
                # relationship is still empty.
                _emit_messages(
                    context, insert_transactions_data, rollup_receipt, "accepted"
                )

        else:
            context.transaction.appealed = False

            context.consensus_service.emit_transaction_event(
                "emitTransactionAccepted",
                leader_receipt.node_config,
                context.transaction.hash,
                [],
            )

        # Update the transaction status to ACCEPTED after triggered transactions
        # have been inserted so they are visible when callers observe the new status.
        await ConsensusAlgorithm.dispatch_transaction_status_update(
            context.transactions_processor,
            context.transaction.hash,
            TransactionStatus.ACCEPTED,
            context.msg_handler,
            False,
        )

        # Set the transaction appeal undetermined status to false and return appeal status
        if context.transaction.appeal_undetermined:
            context.transactions_processor.set_transaction_appeal_undetermined(
                context.transaction.hash, False
            )
            context.transaction.appeal_undetermined = False
            return consensus_round
        elif context.transaction.appeal_leader_timeout:
            context.transaction.appeal_leader_timeout = (
                context.transactions_processor.set_transaction_appeal_leader_timeout(
                    context.transaction.hash, False
                )
            )
            return ConsensusRound.LEADER_TIMEOUT_APPEAL_SUCCESSFUL
        elif consensus_round == ConsensusRound.ACCEPTED:
            return consensus_round
        else:
            return None


class UndeterminedState(TransactionState):
    """
    Class representing the undetermined state of a transaction.
    """

    async def handle(self, context):
        """
        Handle the undetermined state transition.

        Args:
            context (TransactionContext): The context of the transaction.

        Returns:
            None: The transaction remains in an undetermined state.
        """
        # Record timestamp for entering UNDETERMINED state
        context.transactions_processor.add_state_timestamp(
            context.transaction.hash, "UNDETERMINED"
        )

        # Send a message indicating consensus failure
        context.msg_handler.send_message(
            LogEvent(
                "consensus_event",
                EventType.ERROR,
                EventScope.CONSENSUS,
                "Failed to reach consensus",
                {
                    "transaction_hash": context.transaction.hash,
                    "consensus_data": _redact_consensus_data_for_log(
                        context.consensus_data.to_dict()
                    ),
                },
                transaction_hash=context.transaction.hash,
            )
        )

        # When appeal fails, the appeal window is not reset
        if not context.transaction.appeal_undetermined:
            context.transactions_processor.set_transaction_timestamp_awaiting_finalization(
                context.transaction.hash
            )

        # Set the transaction appeal undetermined status to false
        if context.transaction.appeal_undetermined:
            context.transactions_processor.set_transaction_appeal_undetermined(
                context.transaction.hash, False
            )
            context.transaction.appeal_undetermined = False
            consensus_round = ConsensusRound.LEADER_APPEAL_FAILED
            context.transactions_processor.set_transaction_appeal_failed(
                context.transaction.hash,
                context.transaction.appeal_failed + 1,
            )
        else:
            consensus_round = ConsensusRound.UNDETERMINED

        # Save the contract snapshot for potential future appeals
        if not context.transaction.contract_snapshot:
            context.transactions_processor.set_transaction_contract_snapshot(
                context.transaction.hash, context.contract_snapshot.to_dict()
            )

        # Set the transaction result with the current consensus data
        context.transactions_processor.set_transaction_result(
            context.transaction.hash,
            context.consensus_data.to_dict(strip_contract_state=True),
        )

        # Increment the appeal processing time when the transaction was appealed
        if context.transaction.timestamp_appeal is not None:
            context.transactions_processor.set_transaction_appeal_processing_time(
                context.transaction.hash
            )

        context.transactions_processor.update_consensus_history(
            context.transaction.hash,
            consensus_round,
            context.consensus_data.leader_receipt,
            context.consensus_data.validators,
            TransactionStatus.UNDETERMINED,
        )

        # Update the transaction status to undetermined
        await ConsensusAlgorithm.dispatch_transaction_status_update(
            context.transactions_processor,
            context.transaction.hash,
            TransactionStatus.UNDETERMINED,
            context.msg_handler,
            False,
        )

        return None


class LeaderTimeoutState(TransactionState):
    """
    Class representing the leader timeout state of a transaction.
    """

    async def handle(self, context):
        """
        Handle the leader timeout state transition.

        Args:
            context (TransactionContext): The context of the transaction.

        Returns:
            None: The transaction is in a leader timeout state.
        """
        # Record timestamp for entering LEADER_TIMEOUT state
        context.transactions_processor.add_state_timestamp(
            context.transaction.hash, "LEADER_TIMEOUT"
        )

        # Save the contract snapshot for potential future appeals
        if not context.transaction.contract_snapshot:
            context.transactions_processor.set_transaction_contract_snapshot(
                context.transaction.hash, context.contract_snapshot.to_dict()
            )

        if context.transaction.appeal_undetermined:
            consensus_round = ConsensusRound.LEADER_APPEAL_SUCCESSFUL
            context.transactions_processor.set_transaction_timestamp_awaiting_finalization(
                context.transaction.hash
            )
            context.transactions_processor.reset_transaction_appeal_processing_time(
                context.transaction.hash
            )
            context.transactions_processor.set_transaction_timestamp_appeal(
                context.transaction.hash, None
            )
        elif context.transaction.appeal_leader_timeout:
            consensus_round = ConsensusRound.LEADER_TIMEOUT_APPEAL_FAILED
            context.transactions_processor.set_transaction_appeal_processing_time(
                context.transaction.hash
            )
        else:
            consensus_round = ConsensusRound.LEADER_TIMEOUT
            context.transactions_processor.set_transaction_timestamp_awaiting_finalization(
                context.transaction.hash
            )

        # Save involved validators for appeal
        context.transactions_processor.set_leader_timeout_validators(
            context.transaction.hash, context.remaining_validators
        )

        # Update the consensus history
        context.transactions_processor.update_consensus_history(
            context.transaction.hash,
            consensus_round,
            context.consensus_data.leader_receipt,
            [],
            TransactionStatus.LEADER_TIMEOUT,
        )

        # Update the transaction status to LEADER_TIMEOUT
        await ConsensusAlgorithm.dispatch_transaction_status_update(
            context.transactions_processor,
            context.transaction.hash,
            TransactionStatus.LEADER_TIMEOUT,
            context.msg_handler,
            False,
        )

        # Send event in rollup to communicate the leader timeout
        context.consensus_service.emit_transaction_event(
            "emitTransactionLeaderTimeout",
            context.leader,
            context.transaction.hash,
        )

        return None


class ValidatorsTimeoutState(TransactionState):
    """
    Class representing the validators timeout state of a transaction.
    """

    async def handle(self, context):
        """
        Handle the validators timeout state transition.

        Args:
            context (TransactionContext): The context of the transaction.

        Returns:
            None: The transaction is in a validators timeout state.
        """
        # Record timestamp for entering VALIDATORS_TIMEOUT state
        context.transactions_processor.add_state_timestamp(
            context.transaction.hash, "VALIDATORS_TIMEOUT"
        )

        if context.transaction.appeal_undetermined:
            consensus_round = ConsensusRound.LEADER_APPEAL_SUCCESSFUL
            context.transactions_processor.set_transaction_timestamp_awaiting_finalization(
                context.transaction.hash
            )
            context.transactions_processor.reset_transaction_appeal_processing_time(
                context.transaction.hash
            )
            context.transactions_processor.set_transaction_timestamp_appeal(
                context.transaction.hash, None
            )
            context.transactions_processor.set_transaction_appeal_undetermined(
                context.transaction.hash, False
            )

        elif context.transaction.appeal_validators_timeout:
            consensus_round = ConsensusRound.VALIDATORS_TIMEOUT_APPEAL_FAILED
            # Increment the appeal processing time when the transaction was appealed
            context.transactions_processor.set_transaction_appeal_processing_time(
                context.transaction.hash
            )

            # Appeal failed, increment the appeal_failed counter
            context.transactions_processor.set_transaction_appeal_failed(
                context.transaction.hash,
                context.transaction.appeal_failed + 1,
            )

        else:
            consensus_round = ConsensusRound.VALIDATORS_TIMEOUT
            context.transactions_processor.set_transaction_timestamp_awaiting_finalization(
                context.transaction.hash
            )

        if context.transaction.appeal_leader_timeout:
            context.transaction.appeal_leader_timeout = (
                context.transactions_processor.set_transaction_appeal_leader_timeout(
                    context.transaction.hash, False
                )
            )

        # Set the transaction result
        context.transactions_processor.set_transaction_result(
            context.transaction.hash,
            context.consensus_data.to_dict(strip_contract_state=True),
        )

        context.transactions_processor.update_consensus_history(
            context.transaction.hash,
            consensus_round,
            (
                None
                if consensus_round == ConsensusRound.VALIDATORS_TIMEOUT_APPEAL_FAILED
                else context.consensus_data.leader_receipt
            ),
            context.validation_results,
            TransactionStatus.VALIDATORS_TIMEOUT,
        )

        # Update the transaction status to VALIDATORS_TIMEOUT
        await ConsensusAlgorithm.dispatch_transaction_status_update(
            context.transactions_processor,
            context.transaction.hash,
            TransactionStatus.VALIDATORS_TIMEOUT,
            context.msg_handler,
            False,
        )

        if not context.transaction.contract_snapshot:
            context.transactions_processor.set_transaction_contract_snapshot(
                context.transaction.hash, context.contract_snapshot.to_dict()
            )

        return None


class FinalizingState(TransactionState):
    """
    Class representing the finalizing state of a transaction.
    """

    async def handle(self, context):
        """
        Handle the finalizing state transition.

        Args:
            context (TransactionContext): The context of the transaction.

        Returns:
            None: The transaction is finalized.
        """
        # Record timestamp for entering FINALIZED state
        context.transactions_processor.add_state_timestamp(
            context.transaction.hash, "FINALIZED"
        )

        # Retrieve the leader's receipt from the consensus data
        leader_receipt = context.transaction.consensus_data.leader_receipt[0]

        if (context.transaction.status == TransactionStatus.ACCEPTED) and (
            leader_receipt.execution_result == ExecutionResultStatus.SUCCESS
        ):
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

            # Insert pending transactions generated by contract-to-contract calls
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

            # Insert triggered transactions BEFORE updating parent status to
            # FINALIZED.  This prevents a race where external callers see the
            # FINALIZED status but the triggered_transactions relationship is
            # still empty.
            _emit_messages(
                context, insert_transactions_data, rollup_receipt, "finalized"
            )
        else:
            # Send events in rollup to communicate the transaction is finalized
            context.consensus_service.emit_transaction_event(
                "emitTransactionFinalized",
                leader_receipt.node_config,
                context.transaction.hash,
                [],
            )

        # Update the transaction status to FINALIZED after triggered
        # transactions have been inserted so they are visible when callers
        # observe the new status.
        await ConsensusAlgorithm.dispatch_transaction_status_update(
            context.transactions_processor,
            context.transaction.hash,
            TransactionStatus.FINALIZED,
            context.msg_handler,
        )


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
