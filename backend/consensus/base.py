# backend/consensus/base.py

DEFAULT_VALIDATORS_COUNT = 5
DEFAULT_CONSENSUS_SLEEP_TIME = 5

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
from backend.node.genvm.origin.result_codes import ResultCode
from backend.consensus.types import ConsensusResult, ConsensusRound
from backend.consensus.utils import determine_consensus_from_votes
from backend.node.genvm.origin.base_host import get_code_slot

type NodeFactory = Callable[
    [
        dict,
        ExecutionMode,
        ContractSnapshot,
        Receipt | None,
        MessageHandler,
        Callable[[str], ContractSnapshot],
        validators.Snapshot,
    ],
    Node,
]

EXECUTING_TRANSACTION_TIMEOUT = 600
ACTIVATED_TRANSACTION_TIMEOUT = 1800


def node_factory(
    validator: dict,
    validator_mode: ExecutionMode,
    contract_snapshot: ContractSnapshot,
    leader_receipt: Receipt | None,
    msg_handler: MessageHandler,
    contract_snapshot_factory: Callable[[str], ContractSnapshot],
    validators_manager_snapshot: validators.Snapshot,
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
        chain_snapshot: ChainSnapshot,
        accounts_manager: AccountsManager,
        contract_snapshot_factory: Callable[[str], ContractSnapshot],
        contract_processor: ContractProcessor,
        node_factory: NodeFactory,
        msg_handler: MessageHandler,
        consensus_service: ConsensusService,
        validators_snapshot: validators.Snapshot | None,
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
        pending_queues (dict[str, asyncio.Queue]): Dictionary of pending_queues for transactions.
        finality_window_time (int): Time in seconds for the finality window.
        consensus_sleep_time (int): Time in seconds for the consensus sleep time.
    """

    def __init__(
        self,
        get_session: Callable[[], Session],
        msg_handler: MessageHandler,
        consensus_service: ConsensusService,
        validators_manager: validators.Manager,
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
        self.pending_queues: dict[str, asyncio.Queue] = {}
        self.finality_window_time = int(os.environ["VITE_FINALITY_WINDOW"])
        self.finality_window_appeal_failed_reduction = float(
            os.environ["VITE_FINALITY_WINDOW_APPEAL_FAILED_REDUCTION"]
        )
        self.consensus_sleep_time = DEFAULT_CONSENSUS_SLEEP_TIME
        self.pending_queue_stop_events: dict[str, asyncio.Event] = (
            {}
        )  # Events to stop tasks for each pending queue
        self.pending_queue_task_running: dict[str, bool] = (
            {}
        )  # Track running state for each pending queue
        # Per-address blocked head to preserve order when predecessor isn't ready
        # Structure: {address: {"transaction": Transaction, "blocked_at": timestamp}}
        self.blocked_heads: dict[str, dict | None] = {}
        self.validators_manager = validators_manager

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
        Crawl snapshots and process pending transactions.

        Args:
            chain_snapshot_factory (Callable[[Session], ChainSnapshot]): Creates snapshots of the blockchain state at specific points in time.
            transactions_processor_factory (Callable[[Session], TransactionsProcessor]): Creates processors to modify transactions.
            stop_event (threading.Event): Control signal to terminate the loop.
        """
        while not stop_event.is_set():
            with self.get_session() as session:
                chain_snapshot = chain_snapshot_factory(session)
                transactions_processor = transactions_processor_factory(session)
                pending_transactions = chain_snapshot.get_pending_transactions()
                for transaction in pending_transactions:
                    transaction = Transaction.from_dict(transaction)
                    address = transaction.to_address

                    if address is None:
                        # it happens in tests/integration/accounts/test_accounts.py::test_accounts_burn
                        print(f"_crawl_snapshot: address is None, tx {transaction}")
                        traceback.print_stack()

                    # Initialize queue and stop event for the address if not present
                    if address not in self.pending_queues:
                        self.pending_queues[address] = asyncio.Queue()

                    if address not in self.pending_queue_stop_events:
                        self.pending_queue_stop_events[address] = asyncio.Event()

                    # Only add to the queue if the stop event is not set
                    if not self.pending_queue_stop_events[address].is_set():
                        await self.pending_queues[address].put(transaction)

                        # Set the transaction as activated so it is not added to the queue again
                        ConsensusAlgorithm.dispatch_transaction_status_update(
                            transactions_processor,
                            transaction.hash,
                            TransactionStatus.ACTIVATED,
                            self.msg_handler,
                        )

                # Clear stale blocked heads before recovery
                for address, blocked_data in list(self.blocked_heads.items()):
                    if blocked_data:
                        blocked_tx = blocked_data["transaction"]
                        blocked_at = blocked_data["blocked_at"]

                        # Check if the blocked transaction has timed out (blocked for > 600 seconds)
                        if time.time() - blocked_at > EXECUTING_TRANSACTION_TIMEOUT:
                            print(
                                f"Clearing timed-out blocked head {blocked_tx.hash} for address {address} (blocked for {time.time() - blocked_at:.0f}s)"
                            )
                            self.blocked_heads[address] = None
                            continue

                        # Check if the blocked transaction is still valid
                        tx_status = transactions_processor.get_transaction_by_hash(
                            blocked_tx.hash
                        )
                        if not tx_status or tx_status["status"] not in [
                            TransactionStatus.ACTIVATED.value,
                            TransactionStatus.PROPOSING.value,
                            TransactionStatus.COMMITTING.value,
                            TransactionStatus.REVEALING.value,
                            TransactionStatus.ACCEPTED.value,
                        ]:
                            # Transaction is no longer in a valid state - clear the stale entry
                            print(
                                f"Clearing stale blocked head {blocked_tx.hash} for address {address}"
                            )
                            self.blocked_heads[address] = None

                # Check for stuck ACTIVATED transactions (safety mechanism)
                # This recovers orphaned transactions that may have been lost due to errors
                activated_transactions = (
                    transactions_processor.get_activated_transactions_older_than(
                        ACTIVATED_TRANSACTION_TIMEOUT
                    )
                )
                for tx_data in activated_transactions:
                    address = tx_data["to_address"]
                    if not address:
                        continue

                    # Check if this address has a blocked head
                    blocked_data = self.blocked_heads.get(address)
                    if blocked_data:
                        blocked_tx = blocked_data["transaction"]
                        # Check if the blocked head is stale
                        if blocked_tx.hash == tx_data["hash"]:
                            # This is the blocked transaction itself - skip recovery
                            print(
                                f"Skipping recovery for {tx_data['hash']} at address {address} - currently blocked waiting for predecessor"
                            )
                            continue
                        else:
                            # Check if this activated transaction is the predecessor of the blocked transaction
                            previous_tx = (
                                transactions_processor.get_previous_transaction(
                                    blocked_tx.hash
                                )
                            )
                            if previous_tx and previous_tx["hash"] == tx_data["hash"]:
                                # This is the predecessor of the blocked transaction
                                # It's been stuck for >300s, so we need to clear the blocked head to allow recovery
                                print(
                                    f"Clearing blocked head {blocked_tx.hash} to allow recovery of stuck predecessor {tx_data['hash']}"
                                )
                                self.blocked_heads[address] = None
                                # Now recover the stuck predecessor
                                print(
                                    f"Recovering stuck predecessor transaction {tx_data['hash']} for address {address}"
                                )
                                ConsensusAlgorithm.dispatch_transaction_status_update(
                                    transactions_processor,
                                    tx_data["hash"],
                                    TransactionStatus.PENDING,
                                    self.msg_handler,
                                )
                                continue
                            else:
                                # Different transaction is blocked for this address, skip recovery
                                print(
                                    f"Skipping recovery for {tx_data['hash']} at address {address} - another transaction is blocked"
                                )
                                continue

                    # Original recovery logic for non-blocked addresses
                    queue = self.pending_queues.get(address)
                    should_reset = queue is None or (
                        queue.empty()
                        and not self.pending_queue_task_running.get(address, False)
                    )
                    if should_reset:
                        reason = "no queue present" if queue is None else "idle queue"
                        print(
                            f"Recovering stuck transaction {tx_data['hash']} for address {address} ({reason})"
                        )
                        ConsensusAlgorithm.dispatch_transaction_status_update(
                            transactions_processor,
                            tx_data["hash"],
                            TransactionStatus.PENDING,
                            self.msg_handler,
                        )

            await asyncio.sleep(self.consensus_sleep_time)

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
        Process pending transactions.

        Args:
            chain_snapshot_factory (Callable[[Session], ChainSnapshot]): Creates snapshots of the blockchain state at specific points in time.
            transactions_processor_factory (Callable[[Session], TransactionsProcessor]): Creates processors to modify transactions.
            accounts_manager_factory (Callable[[Session], AccountsManager]): Creates managers to handle account state.
            contract_snapshot_factory (Callable[[str, Session, Transaction], ContractSnapshot]): Creates snapshots of contract states.
            node_factory (Callable[[dict, ExecutionMode, ContractSnapshot, Receipt | None, MessageHandler, Callable[[str], ContractSnapshot]], Node]): Creates node instances that can execute contracts and process transactions.
            stop_event (threading.Event): Control signal to terminate the pending transactions process.
        """
        # Note: ollama uses GPU resources and webrequest aka selenium uses RAM
        # TODO: Consider using async sessions to avoid blocking the current thread
        while not stop_event.is_set():
            try:
                async with asyncio.TaskGroup() as tg:
                    for queue_address, queue in self.pending_queues.items():
                        if (
                            (not queue.empty() or self.blocked_heads.get(queue_address))
                            and not self.pending_queue_stop_events.get(
                                queue_address, asyncio.Event()
                            ).is_set()
                            and not self.pending_queue_task_running.get(
                                queue_address, False
                            )
                        ):
                            # Sessions cannot be shared between coroutines; create a new session for each coroutine
                            # Reference: https://docs.sqlalchemy.org/en/20/orm/session_basics.html#is-the-session-thread-safe-is-asyncsession-safe-to-share-in-concurrent-tasks

                            async def exec_transaction_with_session_handling(
                                queue: asyncio.Queue,
                                queue_address: str,
                            ):
                                transaction: Transaction | None = None
                                try:
                                    # Mark as running before dequeuing to prevent race conditions
                                    self.pending_queue_task_running[queue_address] = (
                                        True
                                    )

                                    # Prefer blocked head if present; else attempt to dequeue
                                    existing_blocked_data = self.blocked_heads.get(
                                        queue_address
                                    )
                                    if existing_blocked_data is not None:
                                        transaction = existing_blocked_data[
                                            "transaction"
                                        ]
                                    else:
                                        try:
                                            transaction = await asyncio.wait_for(
                                                queue.get(), timeout=0.1
                                            )
                                        except asyncio.TimeoutError:
                                            # Queue became empty between check and task creation
                                            print(
                                                f"Queue became empty between check and task creation for address {queue_address}"
                                            )
                                            return
                                        # Hold as blocked head until predecessor readiness confirmed
                                        self.blocked_heads[queue_address] = {
                                            "transaction": transaction,
                                            "blocked_at": time.time(),
                                        }

                                    with self.get_session() as session:
                                        virtual_validators = []
                                        if (
                                            transaction.sim_config
                                            and transaction.sim_config.validators
                                        ):
                                            for (
                                                validator
                                            ) in transaction.sim_config.validators:
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
                                                    llm_provider = (
                                                        get_default_provider_for(
                                                            provider, model
                                                        )
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

                                                account = accounts_manager_factory(
                                                    session
                                                ).create_new_account()
                                                virtual_validators.append(
                                                    Validator(
                                                        address=account.address,
                                                        private_key=account.key.to_0x_hex(),
                                                        stake=validator.stake,
                                                        llmprovider=llm_provider,
                                                    )
                                                )
                                        if len(virtual_validators) > 0:
                                            snapshot_func = (
                                                self.validators_manager.temporal_snapshot
                                            )
                                            args = [virtual_validators]
                                        else:
                                            snapshot_func = (
                                                self.validators_manager.snapshot
                                            )
                                            args = []
                                        transactions_processor = (
                                            transactions_processor_factory(session)
                                        )

                                        # Check predecessor readiness before executing
                                        previous_transaction = transactions_processor.get_previous_transaction(
                                            transaction.hash,
                                        )
                                        predecessor_ready = (
                                            (previous_transaction is None)
                                            or (
                                                previous_transaction["appealed"]
                                            )  # noqa: E711
                                            or (
                                                previous_transaction[
                                                    "appeal_undetermined"
                                                ]
                                            )  # noqa: E711
                                            or (
                                                previous_transaction[
                                                    "appeal_leader_timeout"
                                                ]
                                            )  # noqa: E711
                                            or (
                                                previous_transaction[
                                                    "appeal_validators_timeout"
                                                ]
                                            )  # noqa: E711
                                            or (
                                                previous_transaction["status"]
                                                in [
                                                    TransactionStatus.ACCEPTED.value,
                                                    TransactionStatus.UNDETERMINED.value,
                                                    TransactionStatus.FINALIZED.value,
                                                    TransactionStatus.LEADER_TIMEOUT.value,
                                                    TransactionStatus.VALIDATORS_TIMEOUT.value,
                                                ]
                                            )
                                        )

                                        if not predecessor_ready:
                                            # Check if the predecessor is stuck before blocking
                                            if (
                                                previous_transaction
                                                and self.is_transaction_stuck(
                                                    previous_transaction["hash"],
                                                    transactions_processor,
                                                    ACTIVATED_TRANSACTION_TIMEOUT,
                                                )
                                            ):
                                                # Predecessor is stuck - recover it instead of blocking
                                                print(
                                                    f"Predecessor {previous_transaction['hash']} is stuck, recovering it instead of blocking {transaction.hash}"
                                                )
                                                ConsensusAlgorithm.dispatch_transaction_status_update(
                                                    transactions_processor,
                                                    previous_transaction["hash"],
                                                    TransactionStatus.PENDING,
                                                    self.msg_handler,
                                                )
                                                # Also reset the current transaction
                                                ConsensusAlgorithm.dispatch_transaction_status_update(
                                                    transactions_processor,
                                                    transaction.hash,
                                                    TransactionStatus.PENDING,
                                                    self.msg_handler,
                                                )
                                                return

                                            # Keep as blocked head and skip execution this cycle
                                            # Check if it's already blocked to preserve original timestamp
                                            existing_blocked = self.blocked_heads.get(
                                                queue_address
                                            )
                                            if (
                                                not existing_blocked
                                                or existing_blocked["transaction"].hash
                                                != transaction.hash
                                            ):
                                                self.blocked_heads[queue_address] = {
                                                    "transaction": transaction,
                                                    "blocked_at": time.time(),
                                                }
                                            try:
                                                self.msg_handler.send_message(
                                                    LogEvent(
                                                        "consensus_event",
                                                        EventType.INFO,
                                                        EventScope.CONSENSUS,
                                                        "Head blocked; predecessor not ready",
                                                        {
                                                            "hash": transaction.hash,
                                                            "address": queue_address,
                                                            "previous_status": previous_transaction[
                                                                "status"
                                                            ],
                                                        },
                                                        transaction_hash=transaction.hash,
                                                    )
                                                )
                                            except Exception:
                                                pass
                                            return

                                        async with snapshot_func(
                                            *args
                                        ) as validators_snapshot:
                                            await self.exec_transaction(
                                                transaction,
                                                transactions_processor,
                                                chain_snapshot_factory(session),
                                                accounts_manager_factory(session),
                                                lambda contract_address: contract_snapshot_factory(
                                                    contract_address,
                                                    session,
                                                    transaction,
                                                ),
                                                contract_processor_factory(session),
                                                node_factory,
                                                validators_snapshot,
                                            )
                                        session.commit()
                                        # Clear blocked head after successful execution
                                        blocked_data = self.blocked_heads.get(
                                            queue_address
                                        )
                                        if (
                                            blocked_data
                                            and blocked_data["transaction"]
                                            is transaction
                                        ):
                                            self.blocked_heads[queue_address] = None
                                finally:
                                    self.pending_queue_task_running[queue_address] = (
                                        False
                                    )

                            tg.create_task(
                                exec_transaction_with_session_handling(
                                    queue, queue_address
                                )
                            )

            except Exception as e:
                print("Error running consensus", e)
                print(traceback.format_exc())
            finally:
                for queue_address in self.pending_queues:
                    self.pending_queue_task_running[queue_address] = False
            await asyncio.sleep(self.consensus_sleep_time)

    def is_pending_queue_task_running(self, address: str):
        """
        Check if a task for a specific pending queue is currently running.
        """
        return self.pending_queue_task_running.get(address, False)

    def stop_pending_queue_task(self, address: str):
        """
        Signal the task for a specific pending queue to stop.
        """
        if address in self.pending_queues:
            if address not in self.pending_queue_stop_events:
                self.pending_queue_stop_events[address] = asyncio.Event()
            self.pending_queue_stop_events[address].set()

    def start_pending_queue_task(self, address: str):
        """
        Allow the task for a specific pending queue to start.
        """
        if address in self.pending_queue_stop_events:
            self.pending_queue_stop_events[address].clear()

    def is_transaction_stuck(
        self,
        transaction_hash: str,
        transactions_processor: TransactionsProcessor,
        stuck_threshold_seconds: int = ACTIVATED_TRANSACTION_TIMEOUT,
    ) -> bool:
        """
        Check if a transaction has been stuck in ACTIVATED state for too long.

        Args:
            transaction_hash: Hash of the transaction to check
            transactions_processor: Processor to query transaction status
            stuck_threshold_seconds: Number of seconds to consider a transaction stuck

        Returns:
            True if the transaction is stuck, False otherwise
        """
        from datetime import datetime, timedelta, timezone

        tx_data = transactions_processor.get_transaction_by_hash(transaction_hash)
        if not tx_data:
            return False

        # Check if transaction is in ACTIVATED state
        if tx_data["status"] != TransactionStatus.ACTIVATED.value:
            return False

        # Check if it's been stuck for too long
        created_at = datetime.fromisoformat(tx_data["created_at"])
        # Use timezone-aware datetime for comparison
        cutoff_time = datetime.now(timezone.utc) - timedelta(
            seconds=stuck_threshold_seconds
        )

        # Convert created_at to UTC if it has a different timezone
        if created_at.tzinfo != timezone.utc:
            created_at = created_at.astimezone(timezone.utc)

        return created_at < cutoff_time

    async def exec_transaction(
        self,
        transaction: Transaction,
        transactions_processor: TransactionsProcessor,
        chain_snapshot: ChainSnapshot,
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
                        ("consensus_results" in context.transaction.consensus_history)
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
                        self.rollback_transactions(
                            context, False
                        )  # Put on False because this happens in the pending queue, so we don't need to stop it
                    break
                state = next_state

    @staticmethod
    def dispatch_transaction_status_update(
        transactions_processor: TransactionsProcessor,
        transaction_hash: str,
        new_status: TransactionStatus,
        msg_handler: MessageHandler,
        update_current_status_changes: bool = True,
    ):
        """
        Dispatch a transaction status update.

        Args:
            transactions_processor (TransactionsProcessor): Instance responsible for handling transaction operations within the database.
            transaction_hash (str): Hash of the transaction.
            new_status (TransactionStatus): New status of the transaction.
            msg_handler (MessageHandler): Handler for messaging.
        """
        # Update the transaction status in the transactions processor
        transactions_processor.update_transaction_status(
            transaction_hash,
            new_status,
            update_current_status_changes,
        )

        # Send a message indicating the transaction status update
        msg_handler.send_message(
            LogEvent(
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
        )

    @staticmethod
    def execute_transfer(
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
                ConsensusAlgorithm.dispatch_transaction_status_update(
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
        ConsensusAlgorithm.dispatch_transaction_status_update(
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
        while not stop_event.is_set():
            try:
                async with asyncio.TaskGroup() as tg:
                    with self.get_session() as session:
                        # Get the accepted and undetermined transactions per contract address
                        chain_snapshot = chain_snapshot_factory(session)
                        awaiting_finalization_transactions = (
                            chain_snapshot.get_awaiting_finalization_transactions()
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
                                        transactions_processor_factory(task_session)
                                    )

                                    # Go through the whole queue to check for appeals and finalizations
                                    for index, transaction in enumerate(
                                        awaiting_finalization_queue
                                    ):
                                        current_transaction = Transaction.from_dict(
                                            transaction
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

            except Exception as e:
                print("Error running consensus", e)
                print(traceback.format_exc())
            await asyncio.sleep(self.consensus_sleep_time)

    def can_finalize_transaction(
        self,
        transactions_processor: TransactionsProcessor,
        transaction: Transaction,
        index: int,
        awaiting_finalization_queue: list[dict],
    ) -> bool:
        """
        Check if the transaction can be finalized based on the following criteria:
        - The transaction is a leader only transaction
        - The transaction has exceeded the finality window
        - The previous transaction has been finalized

        Args:
            transactions_processor (TransactionsProcessor): The transactions processor instance.
            transaction (Transaction): The transaction to be possibly finalized.
            index (int): The index of the current transaction in the awaiting_finalization_queue.
            awaiting_finalization_queue (list[dict]): The list of accepted and undetermined transactions for one contract.

        Returns:
            bool: True if the transaction can be finalized, False otherwise.
        """
        if (transaction.leader_only) or (
            (
                time.time()
                - transaction.timestamp_awaiting_finalization
                - transaction.appeal_processing_time
            )
            > self.finality_window_time
            * (
                (1 - self.finality_window_appeal_failed_reduction)
                ** transaction.appeal_failed
            )
        ):
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
        chain_snapshot: ChainSnapshot,
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
        )

        # Transition to the FinalizingState
        state = FinalizingState()
        await state.handle(context)

    async def process_leader_appeal(
        self,
        transaction: Transaction,
        transactions_processor: TransactionsProcessor,
        chain_snapshot: ChainSnapshot,
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
            validators_snapshot=validators_snapshot,
            consensus_service=self.consensus_service,
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
                    self.rollback_transactions(context, True)
                    break
                state = next_state

    async def process_leader_timeout_appeal(
        self,
        transaction: Transaction,
        transactions_processor: TransactionsProcessor,
        chain_snapshot: ChainSnapshot,
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
                    self.rollback_transactions(context, True)
                    break
                state = next_state

    async def process_validator_appeal(
        self,
        transaction: Transaction,
        transactions_processor: TransactionsProcessor,
        chain_snapshot: ChainSnapshot,
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
                        self.rollback_transactions(context, True)

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

                    ConsensusAlgorithm.dispatch_transaction_status_update(
                        context.transactions_processor,
                        context.transaction.hash,
                        TransactionStatus.PENDING,
                        context.msg_handler,
                    )

                    # Transaction will be picked up by _crawl_snapshot
                    break
                state = next_state

    def rollback_transactions(self, context: TransactionContext, stop_pending_queue):
        """
        Rollback newer transactions.
        """
        # Rollback all future transactions for the current contract
        address = context.transaction.to_address

        # Stop the _crawl_snapshot and the _run_consensus for the current contract
        if stop_pending_queue:
            self.stop_pending_queue_task(address)

            # Wait until task is finished
            while self.is_pending_queue_task_running(address):
                time.sleep(1)

        # Empty the pending queue
        self.pending_queues[address] = asyncio.Queue()

        # Set all transactions with higher created_at to PENDING
        future_transactions = context.transactions_processor.get_newer_transactions(
            context.transaction.hash
        )
        for future_transaction in future_transactions:
            ConsensusAlgorithm.dispatch_transaction_status_update(
                context.transactions_processor,
                future_transaction["hash"],
                TransactionStatus.PENDING,
                context.msg_handler,
            )

            # Reset the contract snapshot for the transaction
            context.transactions_processor.set_transaction_contract_snapshot(
                future_transaction["hash"], None
            )

        # Start the queue loop again
        if stop_pending_queue:
            self.start_pending_queue_task(address)

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
        if "consensus_results" in consensus_history:
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
                        "transaction": context.transaction.to_dict(),
                    },
                    transaction_hash=context.transaction.hash,
                )
            )

        # If transaction is a transfer, execute it
        # TODO: consider when the transfer involves a contract account, bridging, etc.
        if context.transaction.type == TransactionType.SEND:
            ConsensusAlgorithm.execute_transfer(
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
            return None

        # Determine the involved validators based on whether the transaction is appealed
        if (
            context.transaction.appealed
            or context.transaction.appeal_validators_timeout
        ):
            # If the transaction is appealed, remove the old leader
            context.involved_validators, _ = (
                ConsensusAlgorithm.get_validators_from_consensus_data(
                    all_validators, context.transaction.consensus_data, False
                )
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
        # Dispatch a transaction status update to PROPOSING
        ConsensusAlgorithm.dispatch_transaction_status_update(
            context.transactions_processor,
            context.transaction.hash,
            TransactionStatus.PROPOSING,
            context.msg_handler,
        )

        # The leader is elected randomly
        random.shuffle(context.involved_validators)

        # Unpack the leader and validators
        [context.leader, *context.remaining_validators] = context.involved_validators

        # If the transaction is leader-only, clear the validators
        if context.transaction.leader_only:
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
        # Create a leader node for executing the transaction
        leader_node = context.node_factory(
            context.leader,
            ExecutionMode.LEADER,
            deepcopy(context.contract_snapshot),
            None,
            context.msg_handler,
            context.contract_snapshot_factory,
            context.validators_snapshot,
        )

        # Execute the transaction and obtain the leader receipt
        context.consensus_data.leader_receipt = [
            await leader_node.exec_transaction(context.transaction)
        ]

        # Update the consensus data with the leader's vote and receipt
        context.consensus_data.votes = {}
        context.votes = {}
        context.consensus_data.validators = []
        context.transactions_processor.set_transaction_result(
            context.transaction.hash, context.consensus_data.to_dict()
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

        # Transition to the CommittingState
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

        def create_validator_node(context: TransactionContext, validator: dict):
            assert context.validators_snapshot is not None
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
            )

        # Dispatch a transaction status update to COMMITTING
        ConsensusAlgorithm.dispatch_transaction_status_update(
            context.transactions_processor,
            context.transaction.hash,
            TransactionStatus.COMMITTING,
            context.msg_handler,
        )

        # Execute the transaction with a semaphore to limit the number of concurrent validators
        sem = asyncio.Semaphore(8)

        async def run_single_validator(validator: Node) -> Receipt:
            async with sem:
                return await validator.exec_transaction(context.transaction)

        # Leader evaluates validation function
        validation_by_leader = (
            context.consensus_data.leader_receipt
            and len(context.consensus_data.leader_receipt) == 1
        )

        # Create validator node for the leader
        if validation_by_leader:
            context.validator_nodes = [create_validator_node(context, context.leader)]
        else:
            context.validator_nodes = []

        # Create validator nodes for each validator
        context.validator_nodes.extend(
            [
                create_validator_node(context, validator)
                for validator in context.remaining_validators
            ]
        )

        # Execute the transaction on each validator node and gather the results
        validation_tasks = [
            run_single_validator(validator) for validator in context.validator_nodes
        ]
        context.validation_results = await asyncio.gather(*validation_tasks)

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
        # Update the transaction status to REVEALING
        ConsensusAlgorithm.dispatch_transaction_status_update(
            context.transactions_processor,
            context.transaction.hash,
            TransactionStatus.REVEALING,
            context.msg_handler,
        )

        # Process each validation result and update the context
        for i, validation_result in enumerate(context.validation_results):
            # Store the vote from each validator node
            context.votes[context.validator_nodes[i].address] = (
                validation_result.vote.value
            )

        # Determine the consensus result
        votes_list = list(context.votes.values())
        consensus_result = determine_consensus_from_votes(votes_list)

        # Send event in rollup to communicate the votes are revealed
        for i, validation_result in enumerate(context.validation_results):
            last_vote = i == len(context.validation_results) - 1
            context.consensus_service.emit_transaction_event(
                "emitVoteRevealed",
                validation_result.node_config,
                context.transaction.hash,
                validation_result.node_config["address"],
                int(validation_result.vote),
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
                    context.transaction.hash, context.consensus_data.to_dict()
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
            context.transaction.hash, context.consensus_data.to_dict()
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

        # Update the transaction status to ACCEPTED
        ConsensusAlgorithm.dispatch_transaction_status_update(
            context.transactions_processor,
            context.transaction.hash,
            TransactionStatus.ACCEPTED,
            context.msg_handler,
            False,
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
                    "consensus_data": context.consensus_data.to_dict(),
                },
                transaction_hash=context.transaction.hash,
            )
        )

        # Retrieve the leader's receipt from the consensus data
        leader_receipt = context.consensus_data.leader_receipt[0]

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
                                "accepted": leader_receipt.contract_state,
                                "finalized": {
                                    code_slot_b64: leader_receipt.contract_state.get(
                                        code_slot_b64, b""
                                    )
                                },
                            },
                            "code": context.transaction.data["contract_code"],
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
                                new_contract,
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
                                },
                                transaction_hash=context.transaction.hash,
                            )
                        )
                # Update contract state if it is an existing contract
                else:
                    context.contract_processor.update_contract_state(
                        context.transaction.to_address,
                        accepted_state=leader_receipt.contract_state,
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

                _emit_messages(context, insert_transactions_data, rollup_receipt)

        else:
            context.transaction.appealed = False

            context.consensus_service.emit_transaction_event(
                "emitTransactionAccepted",
                leader_receipt.node_config,
                context.transaction.hash,
                [],
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
        # Send a message indicating consensus failure
        context.msg_handler.send_message(
            LogEvent(
                "consensus_event",
                EventType.ERROR,
                EventScope.CONSENSUS,
                "Failed to reach consensus",
                {
                    "transaction_hash": context.transaction.hash,
                    "consensus_data": context.consensus_data.to_dict(),
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
            context.consensus_data.to_dict(),
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
        ConsensusAlgorithm.dispatch_transaction_status_update(
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
        ConsensusAlgorithm.dispatch_transaction_status_update(
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
            context.transaction.hash, context.consensus_data.to_dict()
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
        ConsensusAlgorithm.dispatch_transaction_status_update(
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
        # Update the transaction status to FINALIZED
        ConsensusAlgorithm.dispatch_transaction_status_update(
            context.transactions_processor,
            context.transaction.hash,
            TransactionStatus.FINALIZED,
            context.msg_handler,
        )

        # Retrieve the leader's receipt from the consensus data
        leader_receipt = context.transaction.consensus_data.leader_receipt[0]

        if (context.transaction.status == TransactionStatus.ACCEPTED) and (
            leader_receipt.execution_result == ExecutionResultStatus.SUCCESS
        ):
            # Update contract state
            context.contract_processor.update_contract_state(
                context.transaction.to_address,
                finalized_state=leader_receipt.contract_state,
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

            _emit_messages(context, insert_transactions_data, rollup_receipt)
        else:
            # Send events in rollup to communicate the transaction is finalized
            context.consensus_service.emit_transaction_event(
                "emitTransactionFinalized",
                leader_receipt.node_config,
                context.transaction.hash,
                [],
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
                from backend.node.base import SIMULATOR_CHAIN_ID

                arr = bytearray()
                arr.append(1)
                arr.extend(Address(context.transaction.to_address).as_bytes)
                arr.extend(
                    pending_transaction.salt_nonce.to_bytes(32, "big", signed=False)
                )
                arr.extend(SIMULATOR_CHAIN_ID.to_bytes(32, "big", signed=False))
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
):
    for i, insert_transaction_data in enumerate(insert_transactions_data):
        transaction_hash = (
            receipt["tx_ids_hex"][i] if receipt and "tx_ids_hex" in receipt else None
        )
        context.transactions_processor.insert_transaction(
            context.transaction.to_address,  # new calls are done by the contract
            insert_transaction_data[0],
            insert_transaction_data[1],
            value=0,  # we only handle EOA transfers at the moment, so no value gets transferred
            type=insert_transaction_data[2],
            nonce=insert_transaction_data[3],
            leader_only=context.transaction.leader_only,  # Cascade
            num_of_initial_validators=context.transaction.num_of_initial_validators,
            triggered_by_hash=context.transaction.hash,
            transaction_hash=transaction_hash,
            config_rotation_rounds=context.transaction.config_rotation_rounds,
        )
