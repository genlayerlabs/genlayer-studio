from collections import defaultdict
from typing import Callable
from unittest.mock import AsyncMock, Mock, MagicMock
from pathlib import Path
import time
import threading
import pytest
from backend.consensus.base import ConsensusAlgorithm, DEFAULT_VALIDATORS_COUNT
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
from eth_account import Account

DEFAULT_FINALITY_WINDOW = 5
DEFAULT_CONSENSUS_SLEEP_TIME = 2
DEFAULT_EXEC_RESULT = b"\x00\x00"  # success(null)
TIMEOUT_EXEC_RESULT = b"\x02timeout"


class TransactionsProcessorMock:
    def __init__(self, transactions=None):
        self.transactions = transactions or []
        self.updated_transaction_status_history = defaultdict(list)
        self.status_changed_event = threading.Event()
        self.status_update_lock = threading.Lock()

    def insert_transaction(self, transaction: Transaction):
        self.transactions.append(transaction_to_dict(transaction))

    def get_transaction_by_hash(self, transaction_hash: str) -> dict:
        for transaction in self.transactions:
            if transaction["hash"] == transaction_hash:
                return transaction
        raise ValueError(f"Transaction with hash {transaction_hash} not found")

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

    def wait_for_status_change(self, timeout: float = 0.1) -> bool:
        result = self.status_changed_event.wait(timeout)
        self.status_changed_event.clear()
        return result

    def set_transaction_result(self, transaction_hash: str, consensus_data: dict):
        transaction = self.get_transaction_by_hash(transaction_hash)
        transaction["consensus_data"] = consensus_data

    def set_transaction_appeal(self, transaction_hash: str, appeal: bool):
        transaction = self.get_transaction_by_hash(transaction_hash)
        if not appeal:
            transaction["appealed"] = appeal
        elif transaction["status"] in (
            TransactionStatus.ACCEPTED.value,
            TransactionStatus.UNDETERMINED.value,
            TransactionStatus.LEADER_TIMEOUT.value,
        ):
            self.set_transaction_timestamp_appeal(transaction, int(time.time()))
            time.sleep(1)
            transaction["appealed"] = appeal

    def set_transaction_timestamp_awaiting_finalization(
        self, transaction_hash: str, timestamp_awaiting_finalization: int = None
    ):
        transaction = self.get_transaction_by_hash(transaction_hash)
        if timestamp_awaiting_finalization:
            transaction["timestamp_awaiting_finalization"] = (
                timestamp_awaiting_finalization
            )
        else:
            transaction["timestamp_awaiting_finalization"] = int(time.time())

    def get_awaiting_finalization_transactions(self):
        awaiting_finalization_transactions = []
        for transaction in self.transactions:
            if (
                (transaction["status"] == TransactionStatus.ACCEPTED.value)
                or (transaction["status"] == TransactionStatus.UNDETERMINED.value)
                or (transaction["status"] == TransactionStatus.LEADER_TIMEOUT.value)
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

    def set_transaction_appeal_undetermined(
        self, transaction_hash: str, appeal_undetermined: bool
    ):
        transaction = self.get_transaction_by_hash(transaction_hash)
        transaction["appeal_undetermined"] = appeal_undetermined

    def get_pending_transactions(self):
        result = []
        for transaction in self.transactions:
            if transaction["status"] == TransactionStatus.PENDING.value:
                result.append(transaction)
        return sorted(result, key=lambda x: x["created_at"])

    def get_newer_transactions(self, transaction_hash: str):
        current_transaction = self.get_transaction_by_hash(transaction_hash)

        result = []
        for transaction in self.transactions:
            if (transaction["created_at"] > current_transaction["created_at"]) and (
                transaction["to_address"] == current_transaction["to_address"]
            ):
                result.append(transaction)
        return sorted(result, key=lambda x: x["created_at"])

    def update_consensus_history(
        self,
        transaction_hash: str,
        consensus_round: str,
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
            "consensus_round": consensus_round,
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

    def set_transaction_timestamp_appeal(
        self, transaction: dict | str, timestamp_appeal: int
    ):
        if isinstance(transaction, str):  # hash
            transaction = self.get_transaction_by_hash(transaction)
        transaction["timestamp_appeal"] = timestamp_appeal

    def set_transaction_appeal_processing_time(self, transaction_hash: str):
        transaction = self.get_transaction_by_hash(transaction_hash)
        transaction["appeal_processing_time"] += (
            round(time.time()) - transaction["timestamp_appeal"]
        )

    def reset_transaction_appeal_processing_time(self, transaction_hash: str):
        transaction = self.get_transaction_by_hash(transaction_hash)
        transaction["appeal_processing_time"] = 0

    def set_transaction_contract_snapshot(
        self, transaction_hash: str, contract_snapshot: dict
    ):
        transaction = self.get_transaction_by_hash(transaction_hash)
        transaction["contract_snapshot"] = contract_snapshot

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
        return appeal_leader_timeout

    def set_leader_timeout_validators(self, transaction_hash: str, validators: list):
        transaction = self.get_transaction_by_hash(transaction_hash)
        transaction["leader_timeout_validators"] = validators


class SnapshotMock:
    def __init__(self, transactions_processor: TransactionsProcessorMock):
        self.transactions_processor = transactions_processor

    def get_pending_transactions(self):
        return self.transactions_processor.get_pending_transactions()

    def get_awaiting_finalization_transactions(self):
        return self.transactions_processor.get_awaiting_finalization_transactions()


class CurrentStateDB:
    def __init__(self, current_state: dict[str, dict] | None = None):
        """
        current_state = {
            address: {
                id: str, this is the address
                data: {
                    code: str,
                    state: {
                        accepted: dict,
                        finalized: dict
                    },
                },
                balance: int,
            }
        }
        Basically the value of the address key is a current_state postgres database row
        """
        self.current_state = current_state or {}
        self.contract_status_changed_event = threading.Event()
        self.account_status_changed_event = threading.Event()

    def get_record(self, address: str) -> dict | None:
        if address in self.current_state:
            return self.current_state[address]
        else:
            return None

    def add_record(self, record: dict):
        self.current_state[record["id"]] = record

    def register_contract(self, contract: dict):
        address = contract["id"]
        self.current_state[address]["data"] = contract["data"]

    def update_contract_data(self, address: str, contract_data: dict):
        self.current_state[address]["data"] = contract_data
        self.contract_status_changed_event.set()

    def wait_for_contract_status_change(self, timeout: float = 0.1) -> bool:
        result = self.contract_status_changed_event.wait(timeout)
        self.contract_status_changed_event.clear()
        return result

    def wait_for_account_status_change(self, timeout: float = 0.1) -> bool:
        result = self.account_status_changed_event.wait(timeout)
        self.account_status_changed_event.clear()
        return result


class ContractSnapshotMock:
    def __init__(
        self, contract_address: str, current_state_db: CurrentStateDB | None = None
    ):
        if contract_address:
            contract_account = current_state_db.get_record(contract_address)
            self.contract_address = contract_address
            self.contract_data = contract_account["data"]
            self.contract_code = self.contract_data["code"]
            self.states = self.contract_data["state"]
            self.balance = contract_account["balance"]
            self.current_state_db = current_state_db

    def __deepcopy__(self, memo):
        """Handle deep copying without copying current_state_db."""
        new_instance = ContractSnapshotMock.__new__(ContractSnapshotMock)
        memo[id(self)] = new_instance
        new_instance.contract_address = self.contract_address
        new_instance.contract_data = deepcopy(self.contract_data, memo)
        new_instance.contract_code = self.contract_code
        new_instance.states = deepcopy(self.states, memo)
        new_instance.balance = deepcopy(self.balance, memo)
        new_instance.current_state_db = (
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
            instance.current_state_db = None
            return instance
        else:
            return None


class ContractProcessorMock:
    def __init__(self, current_state_db: CurrentStateDB):
        self.current_state_db = current_state_db

    def register_contract(self, contract: dict):
        self.current_state_db.register_contract(contract)

    def update_contract_state(
        self,
        contract_address: str,
        accepted_state: dict[str, str] | None = None,
        finalized_state: dict[str, str] | None = None,
    ):
        contract = self.current_state_db.get_record(contract_address)

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

        self.current_state_db.update_contract_data(contract_address, new_contract_data)


class AccountsManagerMock:
    def __init__(self, current_state_db: CurrentStateDB):
        self.current_state_db = current_state_db

    def create_new_account(self) -> Account:
        account = Account.create()
        self.create_new_account_with_address(account.address)
        return account

    def create_new_account_with_address(self, address: str) -> dict:
        existing_account = self.current_state_db.get_record(address)
        if existing_account:
            return existing_account

        account = {"id": address, "data": {}, "balance": 0}
        self.current_state_db.add_record(account)

        return account

    def get_account(self, account_address: str) -> dict | None:
        return self.current_state_db.get_record(account_address)

    def get_account_balance(self, account_address: str) -> int:
        account = self.get_account(account_address)
        if account is None:
            return 0
        else:
            return account["balance"]

    def set_account_balance(self, account_address: str, new_balance: int):
        to_account = self.get_account(account_address)
        if to_account is None:
            self.create_new_account_with_address(account_address)
            to_account = self.get_account(account_address)
        to_account["balance"] = new_balance

    def update_account_balance(self, address: str, value: int | None):
        if value:
            balance = self.get_account_balance(address)
            if balance + value < 0:
                raise ValueError(f"Insufficient balance: {balance} < {value}")
            self.set_account_balance(
                address,
                balance + value,
            )
            self.current_state_db.account_status_changed_event.set()


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
    mock = Mock(Node)

    mock.validator_mode = mode
    mock.address = node["address"]
    mock.leader_receipt = receipt
    mock.private_key = node["private_key"]
    mock.contract_snapshot = contract_snapshot

    async def exec_with_dynamic_state(transaction: Transaction):
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

    mock.exec_transaction = AsyncMock(side_effect=exec_with_dynamic_state)

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
    current_state_db: CurrentStateDB | None = None,
    accounts_manager: AccountsManagerMock | None = None,
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
    if current_state_db is None:
        current_state_db = CurrentStateDB(
            {
                "to_address": {
                    "id": "to_address",
                    "data": {
                        "state": {"accepted": {}, "finalized": {}},
                        "code": "contract_code",
                    },
                    "balance": 0,
                }
            }
        )
    if accounts_manager is None:
        accounts_manager = AccountsManagerMock(current_state_db)

    chain_snapshot_factory = lambda session: chain_snapshot
    transactions_processor_factory = lambda session: transactions_processor
    accounts_manager_factory = lambda session: accounts_manager
    contract_snapshot_factory = (
        lambda address, session, transaction: ContractSnapshotMock(
            address, current_state_db
        )
    )
    contract_processor_factory = lambda session: ContractProcessorMock(current_state_db)

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
    timeout: int = 30,
    interval: float = 0.1,
) -> TransactionStatus:
    last_status = None
    start_time = time.time()

    while time.time() - start_time < timeout:
        current_status = transactions_processor.get_transaction_by_hash(
            transaction.hash
        )["status"]

        if current_status in expected_statuses:
            return current_status

        if current_status != last_status:
            last_status = current_status

        # Wait for next status change
        transactions_processor.wait_for_status_change(interval)

    raise AssertionError(
        f"Transaction did not reach {expected_statuses} within {timeout} seconds. Last status: {last_status}"
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
    current_state_db: CurrentStateDB,
    to_address: str,
    accepted: dict | None = None,
    finalized: dict | None = None,
):
    if accepted is not None:
        assert (
            current_state_db.get_record(to_address)["data"]["state"]["accepted"]
            == accepted
        )
    if finalized is not None:
        assert (
            current_state_db.get_record(to_address)["data"]["state"]["finalized"]
            == finalized
        )


def check_contract_state_with_timeout(
    current_state_db: CurrentStateDB,
    to_address: str,
    accepted: dict | None = None,
    finalized: dict | None = None,
    timeout: int = 30,
    interval: float = 0.1,
):
    start_time = time.time()
    while time.time() - start_time < timeout:
        if current_state_db.wait_for_contract_status_change(interval):
            check_contract_state(current_state_db, to_address, accepted, finalized)
            return

    raise AssertionError(f"Contract state did not change within {timeout} seconds")


def check_account_balance_event_change_with_timeout(
    current_state_db: CurrentStateDB,
    timeout: int = 30,
    interval: float = 0.1,
):
    start_time = time.time()
    while time.time() - start_time < timeout:
        if current_state_db.wait_for_account_status_change(interval):
            return

    raise AssertionError(f"Balance did not change within {timeout} seconds")


def submit_transaction(
    accounts_manager: AccountsManagerMock,
    transactions_processor: TransactionsProcessorMock,
    transaction: Transaction,
):
    accounts_manager.update_account_balance(
        address=transaction.from_address,
        value=-transaction.value if transaction.value else None,
    )
    accounts_manager.current_state_db.account_status_changed_event.clear()
    transactions_processor.insert_transaction(transaction)


def submit_contract(
    accounts_manager: AccountsManagerMock,
    address: str,
):
    accounts_manager.create_new_account_with_address(address)
    contract = {
        "id": address,
        "data": {
            "state": {"accepted": {}, "finalized": {}},
            "code": "contract_code",
        },
    }
    accounts_manager.current_state_db.register_contract(contract)
