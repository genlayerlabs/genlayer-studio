from collections import defaultdict
from typing import Callable
from unittest.mock import AsyncMock, Mock, MagicMock
from pathlib import Path
import time
import threading
import pytest
import os
import asyncio
from backend.consensus.base import (
    ConsensusAlgorithm,
    DEFAULT_VALIDATORS_COUNT,
    ConsensusRound,
)
from backend.database_handler.transactions_processor import TransactionsProcessor
from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.database_handler.models import TransactionStatus
from backend.domain.types import Transaction, TransactionType
from backend.node.base import Node
from backend.node.types import ExecutionMode, ExecutionResultStatus, Receipt, Vote
from backend.protocol_rpc.message_handler.base import MessageHandler
import backend.validators as validators
from typing import Optional
from datetime import datetime
from copy import deepcopy

DEFAULT_FINALITY_WINDOW = 5
# Reduce sleep time for faster tests when using mocks
DEFAULT_CONSENSUS_SLEEP_TIME = (
    0.1 if os.getenv("TEST_WITH_MOCKS", "true").lower() == "true" else 2
)
DEFAULT_EXEC_RESULT = b"\x00\x00"  # success(null)
TIMEOUT_EXEC_RESULT = b"\x02timeout"

# Configuration for LLM mocking
USE_MOCK_LLMS = os.getenv("TEST_WITH_MOCKS", "true").lower() == "true"


class AccountsManagerMock:
    def __init__(self, accounts: dict[str, int] | None = None):
        self.accounts = accounts or defaultdict(int)

    def get_account_balance(self, address: str) -> int:
        return self.accounts[address]

    def update_account_balance(self, address: str, balance: int):
        self.accounts[address] = balance


class TransactionsProcessorMock:
    def __init__(self, transactions=None):
        self.transactions = transactions if transactions is not None else {}
        self.updated_transaction_status_history = defaultdict(list)
        self.status_changed_event = threading.Event()
        self.status_update_lock = threading.Lock()
        # Counter to ensure timestamps always increase in mock mode
        self._mock_time_counter = 0

    def get_transaction_by_hash(self, transaction_hash: str) -> dict:
        if transaction_hash in self.transactions:
            # Everytime we do a database request we get a new transaction object
            # This tests how it is done in the real code and we do not get weird behaviour because of passing references
            return deepcopy(self.transactions[transaction_hash])
        else:
            raise ValueError(f"Transaction with hash {transaction_hash} not found")

    def commit(self, transaction: dict):
        # We write the copied transaction back to the transactions dictionary
        self.transactions[transaction["hash"]] = transaction

    def update_transaction_status(
        self,
        transaction_hash: str,
        new_status: TransactionStatus,
        update_current_status_changes: bool = True,
    ):
        with self.status_update_lock:
            transaction = self.get_transaction_by_hash(transaction_hash)
            transaction["status"] = new_status.value
            self.updated_transaction_status_history[transaction_hash].append(new_status)

            if update_current_status_changes:
                if "current_status_changes" in transaction["consensus_history"]:
                    transaction["consensus_history"]["current_status_changes"].append(
                        new_status.value
                    )
                else:
                    transaction["consensus_history"]["current_status_changes"] = [
                        TransactionStatus.PENDING.value,
                        new_status.value,
                    ]

            self.status_changed_event.set()
            self.commit(transaction)

    def wait_for_status_change(self, timeout: float = 0.1) -> bool:
        result = self.status_changed_event.wait(timeout)
        self.status_changed_event.clear()
        return result

    def set_transaction_result(self, transaction_hash: str, consensus_data: dict):
        transaction = self.get_transaction_by_hash(transaction_hash)
        transaction["consensus_data"] = consensus_data
        self.commit(transaction)

    def set_transaction_appeal(self, transaction_hash: str, appeal: bool):
        transaction = self.get_transaction_by_hash(transaction_hash)
        if not appeal:
            transaction["appealed"] = appeal
        elif transaction["status"] in (
            TransactionStatus.ACCEPTED.value,
            TransactionStatus.UNDETERMINED.value,
            TransactionStatus.LEADER_TIMEOUT.value,
            TransactionStatus.VALIDATORS_TIMEOUT.value,
        ):
            # In mock mode, use an incrementing counter to ensure timestamps increase
            if USE_MOCK_LLMS:
                self._mock_time_counter += 1
                timestamp = int(time.time()) + self._mock_time_counter
            else:
                timestamp = int(time.time())
            self.set_transaction_timestamp_appeal(transaction, timestamp)
            # Reduce sleep for faster tests when using mocks
            time.sleep(0.01 if USE_MOCK_LLMS else 1)
            transaction["appealed"] = appeal
        self.commit(transaction)

    def set_transaction_timestamp_awaiting_finalization(
        self, transaction_hash: str, timestamp_awaiting_finalization: int = None
    ):
        transaction = self.get_transaction_by_hash(transaction_hash)
        if timestamp_awaiting_finalization:
            transaction["timestamp_awaiting_finalization"] = (
                timestamp_awaiting_finalization
            )
        else:
            # In mock mode, use an incrementing counter to ensure timestamps increase
            if USE_MOCK_LLMS:
                self._mock_time_counter += 1
                transaction["timestamp_awaiting_finalization"] = (
                    int(time.time()) + self._mock_time_counter
                )
            else:
                transaction["timestamp_awaiting_finalization"] = int(time.time())
        self.commit(transaction)

    def get_awaiting_finalization_transactions(self):
        awaiting_finalization_transactions = []
        for transaction in self.transactions.values():
            if (
                (transaction["status"] == TransactionStatus.ACCEPTED.value)
                or (transaction["status"] == TransactionStatus.UNDETERMINED.value)
                or (transaction["status"] == TransactionStatus.LEADER_TIMEOUT.value)
                or (transaction["status"] == TransactionStatus.VALIDATORS_TIMEOUT.value)
            ):
                awaiting_finalization_transactions.append(transaction)

        awaiting_finalization_transactions = sorted(
            awaiting_finalization_transactions, key=lambda x: x["created_at"]
        )

        # Group transactions by address
        transactions_by_address = defaultdict(list)
        for transaction in awaiting_finalization_transactions:
            address = transaction["to_address"]
            transactions_by_address[address].append(transaction)
        return transactions_by_address

    def set_transaction_appeal_failed(self, transaction_hash: str, appeal_failed: int):
        if appeal_failed < 0:
            raise ValueError("appeal_failed must be a non-negative integer")
        transaction = self.get_transaction_by_hash(transaction_hash)
        transaction["appeal_failed"] = appeal_failed
        self.commit(transaction)

    def set_transaction_appeal_undetermined(
        self, transaction_hash: str, appeal_undetermined: bool
    ):
        transaction = self.get_transaction_by_hash(transaction_hash)
        transaction["appeal_undetermined"] = appeal_undetermined
        self.commit(transaction)

    def get_pending_transactions(self):
        result = []
        for transaction in self.transactions.values():
            if transaction["status"] == TransactionStatus.PENDING.value:
                result.append(transaction)
        return sorted(result, key=lambda x: x["created_at"])

    def get_newer_transactions(self, transaction_hash: str):
        current_transaction = self.get_transaction_by_hash(transaction_hash)

        result = []
        for transaction in self.transactions.values():
            if (transaction["created_at"] > current_transaction["created_at"]) and (
                transaction["to_address"] == current_transaction["to_address"]
            ):
                result.append(transaction)
        return sorted(result, key=lambda x: x["created_at"])

    def update_consensus_history(
        self,
        transaction_hash: str,
        consensus_round: ConsensusRound,
        leader_result: list[Receipt] | None,
        validator_results: list[Receipt],
        extra_status_change: TransactionStatus | None = None,
    ):
        transaction = self.get_transaction_by_hash(transaction_hash)

        status_changes_to_use = (
            transaction["consensus_history"]["current_status_changes"]
            if "current_status_changes" in transaction["consensus_history"]
            else []
        )
        if extra_status_change:
            status_changes_to_use.append(extra_status_change.value)

        current_consensus_results = {
            "consensus_round": consensus_round.value,
            "leader_result": (
                [receipt.to_dict() for receipt in leader_result]
                if leader_result
                else None
            ),
            "validator_results": [receipt.to_dict() for receipt in validator_results],
            "status_changes": status_changes_to_use,
        }
        if "consensus_results" in transaction["consensus_history"]:
            transaction["consensus_history"]["consensus_results"].append(
                current_consensus_results
            )
        else:
            transaction["consensus_history"]["consensus_results"] = [
                current_consensus_results
            ]

        transaction["consensus_history"]["current_status_changes"] = []
        self.commit(transaction)

    def set_transaction_timestamp_appeal(
        self, transaction: dict | str, timestamp_appeal: int
    ):
        if isinstance(transaction, str):  # hash
            transaction = self.get_transaction_by_hash(transaction)
        transaction["timestamp_appeal"] = timestamp_appeal
        self.commit(transaction)

    def set_transaction_appeal_processing_time(self, transaction_hash: str):
        transaction = self.get_transaction_by_hash(transaction_hash)
        # Calculate time difference
        if USE_MOCK_LLMS:
            # In mock mode, use the counter to get consistent timestamps
            self._mock_time_counter += 1
            current_time = int(time.time()) + self._mock_time_counter
        else:
            current_time = round(time.time())
        time_diff = current_time - transaction["timestamp_appeal"]
        # In mock mode with fast sleeps, ensure at least 1 second of processing time
        if USE_MOCK_LLMS and time_diff <= 0:
            time_diff = 1
        transaction["appeal_processing_time"] += time_diff
        self.commit(transaction)

    def reset_transaction_appeal_processing_time(self, transaction_hash: str):
        transaction = self.get_transaction_by_hash(transaction_hash)
        transaction["appeal_processing_time"] = 0
        self.commit(transaction)

    def set_transaction_contract_snapshot(
        self, transaction_hash: str, contract_snapshot: dict
    ):
        transaction = self.get_transaction_by_hash(transaction_hash)
        transaction["contract_snapshot"] = contract_snapshot
        self.commit(transaction)

    def get_previous_transaction(
        self,
        transaction_hash: str,
        status: TransactionStatus | None = None,
        filter_success: bool = False,
    ) -> None:
        return None

    def set_transaction_timestamp_last_vote(self, transaction_hash: str):
        transaction = self.get_transaction_by_hash(transaction_hash)
        transaction["last_vote_timestamp"] = int(time.time())

    def increase_transaction_rotation_count(self, transaction_hash: str):
        transaction = self.get_transaction_by_hash(transaction_hash)
        transaction["rotation_count"] += 1

    def reset_transaction_rotation_count(self, transaction_hash: str):
        transaction = self.get_transaction_by_hash(transaction_hash)
        transaction["rotation_count"] = 0

    def set_transaction_appeal_leader_timeout(
        self, transaction_hash: str, appeal_leader_timeout: bool
    ) -> bool:
        transaction = self.get_transaction_by_hash(transaction_hash)
        transaction["appeal_leader_timeout"] = appeal_leader_timeout
        self.commit(transaction)
        return appeal_leader_timeout

    def set_leader_timeout_validators(self, transaction_hash: str, validators: list):
        transaction = self.get_transaction_by_hash(transaction_hash)
        transaction["leader_timeout_validators"] = validators
        self.commit(transaction)

    def set_transaction_appeal_validators_timeout(
        self, transaction_hash: str, appeal_validators_timeout: bool
    ) -> bool:
        transaction = self.get_transaction_by_hash(transaction_hash)
        transaction["appeal_validators_timeout"] = appeal_validators_timeout
        self.commit(transaction)
        return appeal_validators_timeout

    def get_activated_transactions_older_than(self, seconds: int) -> list[dict]:
        """Get ACTIVATED transactions that have been stuck for more than the specified seconds.
        Mock implementation that returns empty list to prevent interference with tests.
        """
        # Return empty list in tests to prevent the safety mechanism from interfering
        return []

    def get_contracts_with_pending(self) -> list[str]:
        addresses: set[str] = set()
        for transaction in self.transactions.values():
            if (
                transaction.get("status") == TransactionStatus.PENDING.value
                and transaction.get("to_address") is not None
            ):
                addresses.add(transaction["to_address"])
        return sorted(addresses)

    def get_oldest_pending_for_contract(self, contract_address: str) -> dict | None:
        candidates = [
            t
            for t in self.transactions.values()
            if t.get("to_address") == contract_address
            and t.get("status") == TransactionStatus.PENDING.value
        ]
        if not candidates:
            return None
        # Return a copy to mimic DB row parsing behavior
        from copy import deepcopy as _deepcopy

        return _deepcopy(min(candidates, key=lambda x: x["created_at"]))

    def get_processing_transaction_for_contract(
        self, contract_address: str
    ) -> dict | None:
        processing_statuses = {
            TransactionStatus.ACTIVATED.value,
            TransactionStatus.PROPOSING.value,
            TransactionStatus.COMMITTING.value,
            TransactionStatus.REVEALING.value,
        }
        candidates = [
            t
            for t in self.transactions.values()
            if t.get("to_address") == contract_address
            and t.get("status") in processing_statuses
        ]
        if not candidates:
            return None
        from copy import deepcopy as _deepcopy

        # Choose the most recent processing transaction
        return _deepcopy(max(candidates, key=lambda x: x["created_at"]))

    def reset_stuck_transactions(self, timeout_seconds: int = 900) -> int:
        # Use naive datetime to match how mock transactions are created
        from datetime import datetime, timedelta

        cutoff_time = datetime.now() - timedelta(seconds=timeout_seconds)
        processing_statuses = {
            TransactionStatus.ACTIVATED.value,
            TransactionStatus.PROPOSING.value,
            TransactionStatus.COMMITTING.value,
            TransactionStatus.REVEALING.value,
        }

        reset_count = 0
        for tx in self.transactions.values():
            if (
                tx.get("status") in processing_statuses
                and tx.get("created_at") < cutoff_time
            ):
                tx["status"] = TransactionStatus.PENDING.value
                reset_count += 1

        if reset_count > 0:
            self.status_changed_event.set()

        return reset_count


class SnapshotMock:
    def __init__(self, transactions_processor: TransactionsProcessorMock):
        self.transactions_processor = transactions_processor

    def get_pending_transactions(self):
        return self.transactions_processor.get_pending_transactions()

    def get_awaiting_finalization_transactions(self):
        return self.transactions_processor.get_awaiting_finalization_transactions()


class ContractDB:
    def __init__(self, contracts: dict[str, dict] = None):
        self.contracts = contracts or {}
        self.status_changed_event = threading.Event()

    def get_contract(self, address: str) -> dict:
        return self.contracts[address]

    def register_contract(self, contract: dict):
        self.contracts[contract["id"]] = contract

    def update_contract_data(self, address: str, contract_data: dict):
        self.contracts[address]["data"] = contract_data
        self.status_changed_event.set()

    def wait_for_status_change(self, timeout: float = 0.1) -> bool:
        result = self.status_changed_event.wait(timeout)
        self.status_changed_event.clear()
        return result


class ContractSnapshotMock:
    def __init__(self, contract_address: str, contract_db: ContractDB | None = None):
        if contract_address:
            contract_account = contract_db.get_contract(contract_address)
            self.contract_address = contract_address
            self.contract_data = contract_account["data"]
            self.contract_code = self.contract_data["code"]
            self.states = self.contract_data["state"]
            self.contract_db = contract_db

    def __deepcopy__(self, memo):
        """Handle deep copying without copying contract_db."""
        new_instance = ContractSnapshotMock.__new__(ContractSnapshotMock)
        memo[id(self)] = new_instance
        new_instance.contract_address = self.contract_address
        new_instance.contract_data = deepcopy(self.contract_data, memo)
        new_instance.contract_code = self.contract_code
        new_instance.states = deepcopy(self.states, memo)
        new_instance.contract_db = (
            None  # threading event that cannot be copied but not used by nodes
        )
        return new_instance

    def to_dict(self):
        return {
            "contract_address": (
                self.contract_address if self.contract_address else None
            ),
            "contract_code": self.contract_code if self.contract_code else None,
            "states": self.states if self.states else {"accepted": {}, "finalized": {}},
        }

    @classmethod
    def from_dict(cls, input: dict | None) -> Optional["ContractSnapshotMock"]:
        if input:
            instance = cls.__new__(cls)
            instance.contract_address = input.get("contract_address", None)
            instance.contract_code = input.get("contract_code", None)
            instance.states = input.get("states", {"accepted": {}, "finalized": {}})
            instance.contract_db = None
            return instance
        else:
            return None


class ContractProcessorMock:
    def __init__(self, contract_db: ContractDB):
        self.contract_db = contract_db

    def register_contract(self, contract: dict):
        self.contract_db.register_contract(contract)

    def update_contract_state(
        self,
        contract_address: str,
        accepted_state: dict[str, str] | None = None,
        finalized_state: dict[str, str] | None = None,
    ):
        contract = self.contract_db.get_contract(contract_address)

        new_state = {
            "accepted": (
                accepted_state
                if accepted_state is not None
                else contract["data"]["state"]["accepted"]
            ),
            "finalized": (
                finalized_state
                if finalized_state is not None
                else contract["data"]["state"]["finalized"]
            ),
        }
        new_contract_data = {
            "code": contract["data"]["code"],
            "state": new_state,
        }

        self.contract_db.update_contract_data(contract_address, new_contract_data)


def transaction_to_dict(transaction: Transaction) -> dict:
    return {
        "hash": transaction.hash,
        "status": transaction.status.value,
        "from_address": transaction.from_address,
        "to_address": transaction.to_address,
        "input_data": transaction.input_data,
        "data": transaction.data,
        "consensus_data": (
            transaction.consensus_data.to_dict() if transaction.consensus_data else None
        ),
        "nonce": transaction.nonce,
        "value": transaction.value,
        "type": transaction.type.value,
        "gaslimit": transaction.gaslimit,
        "r": transaction.r,
        "s": transaction.s,
        "v": transaction.v,
        "leader_only": transaction.leader_only,
        "created_at": transaction.created_at,
        "appealed": transaction.appealed,
        "timestamp_awaiting_finalization": transaction.timestamp_awaiting_finalization,
        "appeal_failed": transaction.appeal_failed,
        "appeal_undetermined": transaction.appeal_undetermined,
        "consensus_history": transaction.consensus_history,
        "timestamp_appeal": transaction.timestamp_appeal,
        "appeal_processing_time": transaction.appeal_processing_time,
        "contract_snapshot": (
            transaction.contract_snapshot.to_dict()
            if transaction.contract_snapshot
            else None
        ),
        "config_rotation_rounds": transaction.config_rotation_rounds,
        "num_of_initial_validators": transaction.num_of_initial_validators,
        "last_vote_timestamp": transaction.last_vote_timestamp,
        "rotation_count": transaction.rotation_count,
        "appeal_leader_timeout": transaction.appeal_leader_timeout,
        "leader_timeout_validators": transaction.leader_timeout_validators,
        "appeal_validators_timeout": transaction.appeal_validators_timeout,
    }


def init_dummy_transaction(hash: str | None = None):
    return Transaction(
        hash="transaction_hash" if hash is None else hash,
        from_address="from_address",
        to_address="to_address",
        status=TransactionStatus.PENDING,
        type=TransactionType.RUN_CONTRACT,
        created_at=datetime.fromtimestamp(time.time()),
        num_of_initial_validators=DEFAULT_VALIDATORS_COUNT,
    )


def get_nodes_specs(number_of_nodes: int):
    return [
        {
            "address": f"address{i}",
            "stake": i + 1,
            "provider": f"provider{i}",
            "model": f"model{i}",
            "config": f"config{i}",
            "private_key": f"private_key{i}",
        }
        for i in range(number_of_nodes)
    ]


def node_factory(
    node: dict,
    mode: ExecutionMode,
    contract_snapshot: ContractSnapshot,
    receipt: Receipt | None,
    msg_handler: MessageHandler,
    contract_snapshot_factory: Callable[[str], ContractSnapshot],
    snap: validators.Snapshot,
    vote: Vote,
    timeout: bool,
):
    async def exec_with_dynamic_state(transaction: Transaction, llm_mocked: bool):
        if llm_mocked:
            # Add small delay to simulate processing
            await asyncio.sleep(0.01)  # Small delay for mocked responses

        accepted_state = contract_snapshot.states["accepted"]
        set_value = transaction.hash[-1]
        if len(accepted_state) == 0:
            contract_state = {"state_var": set_value}
        else:
            value = accepted_state["state_var"]
            contract_state = {"state_var": value + set_value}

        return Receipt(
            vote=vote,
            calldata=b"",
            mode=mode,
            gas_used=0,
            contract_state=contract_state,  # Dynamic contract state based on transaction
            result=TIMEOUT_EXEC_RESULT if timeout else DEFAULT_EXEC_RESULT,
            node_config={
                "address": node["address"],
                "private_key": node["private_key"],
            },
            eq_outputs={},
            execution_result=ExecutionResultStatus.SUCCESS,
        )

    if USE_MOCK_LLMS:
        # Use mocked node (default, fast)
        mock = Mock(Node)
        mock.validator_mode = mode
        mock.address = node["address"]
        mock.leader_receipt = receipt
        mock.private_key = node["private_key"]
        mock.contract_snapshot = contract_snapshot

        async def mock_exec_transaction(transaction: Transaction):
            return await exec_with_dynamic_state(transaction, llm_mocked=True)

        mock.exec_transaction = AsyncMock(side_effect=mock_exec_transaction)
        return mock
    else:
        # Use real node with actual LLM calls (slow, requires API keys)
        # This would require proper validator setup with real LLM providers
        # For now, we'll still use mocks but log that real mode was requested
        print(
            f"[WARNING] Real LLM mode requested but not fully implemented in unit tests"
        )
        print(
            f"[WARNING] Using mocked responses. For real LLMs, use integration tests."
        )

        # Fall back to mock for now
        mock = Mock(Node)
        mock.validator_mode = mode
        mock.address = node["address"]
        mock.leader_receipt = receipt
        mock.private_key = node["private_key"]
        mock.contract_snapshot = contract_snapshot

        async def mock_exec_transaction(transaction: Transaction):
            return await exec_with_dynamic_state(transaction, llm_mocked=False)

        mock.exec_transaction = AsyncMock(side_effect=mock_exec_transaction)
        return mock


def appeal(transaction: Transaction, transactions_processor: TransactionsProcessorMock):
    transactions_processor.status_changed_event.clear()
    assert (
        transactions_processor.get_transaction_by_hash(transaction.hash)["appealed"]
        == False
    )
    transactions_processor.set_transaction_appeal(transaction.hash, True)
    assert (
        transactions_processor.get_transaction_by_hash(transaction.hash)["appealed"]
        == True
    )


def check_validator_count(
    transaction: Transaction, transactions_processor: TransactionsProcessor, n: int
):
    assert (
        len(
            transactions_processor.get_transaction_by_hash(transaction.hash)[
                "consensus_data"
            ]["validators"]
        )
        == n - 1  # -1 because of the leader
    )


def get_leader_address(
    transaction: Transaction, transactions_processor: TransactionsProcessor
):
    transaction_dict = transactions_processor.get_transaction_by_hash(transaction.hash)
    return transaction_dict["consensus_data"]["leader_receipt"][0]["node_config"][
        "address"
    ]


def get_validator_addresses(
    transaction: Transaction, transactions_processor: TransactionsProcessor
):
    transaction_dict = transactions_processor.get_transaction_by_hash(transaction.hash)
    return {
        validator["node_config"]["address"]
        for validator in transaction_dict["consensus_data"]["validators"]
    }


def get_leader_timeout_validators_addresses(
    transaction: Transaction, transactions_processor: TransactionsProcessor
):
    transaction_dict = transactions_processor.get_transaction_by_hash(transaction.hash)
    return {
        validator["address"]
        for validator in transaction_dict["leader_timeout_validators"]
    }


def get_consensus_rounds_names(
    transaction: Transaction, transactions_processor: TransactionsProcessor
):
    transaction_dict = transactions_processor.get_transaction_by_hash(transaction.hash)
    return [
        round["consensus_round"]
        for round in transaction_dict["consensus_history"]["consensus_results"]
    ]


@pytest.fixture
def consensus_algorithm() -> ConsensusAlgorithm:
    class MessageHandlerMock:
        def send_message(self, log_event, log_to_terminal: bool = True):
            print(log_event)

    # Mock the session and other dependencies
    mock_session = MagicMock()
    mock_msg_handler = MessageHandlerMock()
    mock_validators_manager = AsyncMock()

    consensus_algorithm = ConsensusAlgorithm(
        get_session=lambda: mock_session,
        msg_handler=mock_msg_handler,
        consensus_service=MagicMock(),
        validators_manager=mock_validators_manager,
    )
    consensus_algorithm.finality_window_time = DEFAULT_FINALITY_WINDOW
    consensus_algorithm.consensus_sleep_time = DEFAULT_CONSENSUS_SLEEP_TIME
    return consensus_algorithm


def setup_test_environment(
    consensus_algorithm: ConsensusAlgorithm,
    transactions_processor: TransactionsProcessorMock,
    nodes: list,
    created_nodes: list,
    get_vote: Callable[[], Vote],
    get_timeout: Callable[[], bool] | None = None,
    contract_db: ContractDB | None = None,
):
    import contextlib
    from backend.domain.types import Validator, LLMProvider

    @contextlib.asynccontextmanager
    async def fake_snapshot():
        snap_nodes: list[validators.SingleValidatorSnapshot] = []
        for i in nodes:
            snap_nodes.append(
                validators.SingleValidatorSnapshot(
                    Validator(
                        i["address"],
                        i["stake"],
                        LLMProvider("heurist", "other", {}, "heurist", {}),
                    ),
                    "",
                )
            )
        yield validators.Snapshot(snap_nodes, Path())

    consensus_algorithm.validators_manager.snapshot = fake_snapshot

    chain_snapshot = SnapshotMock(transactions_processor)
    accounts_manager = AccountsManagerMock()

    chain_snapshot_factory = lambda session: chain_snapshot
    transactions_processor_factory = lambda session: transactions_processor
    accounts_manager_factory = lambda session: accounts_manager
    if contract_db is None:
        contract_db = ContractDB(
            {
                "to_address": {
                    "id": "to_address",
                    "data": {
                        "state": {"accepted": {}, "finalized": {}},
                        "code": "contract_code",
                    },
                }
            }
        )
    contract_snapshot_factory = (
        lambda address, session, transaction: ContractSnapshotMock(address, contract_db)
    )
    contract_processor_factory = lambda session: ContractProcessorMock(contract_db)

    def node_factory_supplier(*args):
        created_nodes.append(
            node_factory(
                *args,
                vote=get_vote(),
                timeout=get_timeout() if get_timeout else False,
            )
        )
        return created_nodes[-1]

    # Create a stop event
    stop_event = threading.Event()

    import asyncio

    consensus_loop = asyncio.new_event_loop()

    async def start_all():
        futures = [
            consensus_algorithm.run_crawl_snapshot_loop(
                chain_snapshot_factory, transactions_processor_factory, stop_event
            ),
            consensus_algorithm.run_process_pending_transactions_loop(
                chain_snapshot_factory,
                transactions_processor_factory,
                accounts_manager_factory,
                contract_snapshot_factory,
                contract_processor_factory,
                node_factory_supplier,
                stop_event,
            ),
            consensus_algorithm.run_appeal_window_loop(
                chain_snapshot_factory,
                transactions_processor_factory,
                accounts_manager_factory,
                contract_snapshot_factory,
                contract_processor_factory,
                node_factory_supplier,
                stop_event,
            ),
        ]

        await asyncio.wait(
            [asyncio.tasks.create_task(f) for f in futures],
            return_when="FIRST_EXCEPTION",
        )

    def start_thread():
        try:
            consensus_loop.run_until_complete(start_all())
        except BaseException as e:
            import traceback

            traceback.print_exception(e)
            import sys

            sys.exit(1)

    # Start the crawl_snapshot, process_pending_transactions and appeal_window threads
    thread_all = threading.Thread(
        target=start_thread,
    )

    thread_all.start()

    return (
        stop_event,
        thread_all,
    )


def cleanup_threads(event: threading.Event, threads: list[threading.Thread]):
    event.set()
    for thread in threads:
        thread.join()


def assert_transaction_status_match(
    transactions_processor: TransactionsProcessorMock,
    transaction: Transaction,
    expected_statuses: list[TransactionStatus],
    timeout: int = None,
    interval: float = 0.1,
) -> TransactionStatus:
    # Use adaptive timeout based on LLM mode
    if timeout is None:
        timeout = (
            60 if USE_MOCK_LLMS else 180
        )  # 1 minute for mocks, 3 minutes for real LLMs

    last_status = None
    start_time = time.time()

    # Log mode if using real LLMs
    if not USE_MOCK_LLMS:
        print(
            f"[LLM Mode: REAL] Waiting for status {expected_statuses} with timeout {timeout}s"
        )

    while time.time() - start_time < timeout:
        current_status = transactions_processor.get_transaction_by_hash(
            transaction.hash
        )["status"]

        if current_status in expected_statuses:
            return current_status

        if current_status != last_status:
            last_status = current_status
            if not USE_MOCK_LLMS:
                print(f"[LLM Mode: REAL] Status changed to: {current_status}")

        # Wait for next status change
        transactions_processor.wait_for_status_change(interval)

    mode_info = "MOCK" if USE_MOCK_LLMS else "REAL"
    raise AssertionError(
        f"Transaction did not reach {expected_statuses} within {timeout} seconds. "
        f"Last status: {last_status}. LLM mode: {mode_info}"
    )


def assert_transaction_status_change_and_match(
    transactions_processor: TransactionsProcessorMock,
    transaction: Transaction,
    expected_statuses: list[TransactionStatus],
    timeout: int = 30,
    interval: float = 0.1,
):
    transactions_processor.status_changed_event.wait()
    assert_transaction_status_match(
        transactions_processor,
        transaction,
        expected_statuses,
        timeout=timeout,
        interval=interval,
    )


def check_contract_state(
    contract_db: ContractDB,
    to_address: str,
    accepted: dict | None = None,
    finalized: dict | None = None,
):
    if accepted is not None:
        assert (
            contract_db.contracts[to_address]["data"]["state"]["accepted"] == accepted
        )
    if finalized is not None:
        assert (
            contract_db.contracts[to_address]["data"]["state"]["finalized"] == finalized
        )


def check_contract_state_with_timeout(
    contract_db: ContractDB,
    to_address: str,
    accepted: dict | None = None,
    finalized: dict | None = None,
    timeout: int = 30,
    interval: float = 0.1,
):
    start_time = time.time()
    while time.time() - start_time < timeout:
        if contract_db.wait_for_status_change(interval):
            check_contract_state(contract_db, to_address, accepted, finalized)
            return

    raise AssertionError(f"Contract state did not change within {timeout} seconds")
