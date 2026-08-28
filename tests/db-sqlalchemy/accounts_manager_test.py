from datetime import datetime
import time
import threading
from eth_account.signers.local import (
    LocalAccount,
)
import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.database_handler.accounts_manager import AccountsManager
from backend.database_handler.errors import AccountNotFoundError
from backend.database_handler.models import Transactions, TransactionStatus
from backend.database_handler.transactions_processor import TransactionsProcessor
from backend.consensus.history import (
    ACTIVE_APPEAL_BASIS_KEY,
    APPEAL_RECOVERY_SNAPSHOT_KEY,
)
from backend.protocol_rpc.fees import (
    FEE_ACCOUNTING_KEY,
    calculate_appeal_charge,
    create_fee_accounting,
    record_appeal_bond,
    required_fee_deposit,
)


@pytest.fixture
def accounts_manager(session: Session):
    yield AccountsManager(session)


def test_create_new_account(accounts_manager: AccountsManager):
    account = accounts_manager.create_new_account()
    assert isinstance(account, LocalAccount)

    account_data = accounts_manager.get_account_or_fail(account.address)
    assert account_data["id"] == account.address


def test_create_new_account_with_address(accounts_manager: AccountsManager):
    address = "0x9F0e84243496AcFB3Cd99D02eA59673c05901501"
    accounts_manager.create_new_account_with_address(address)

    account_data = accounts_manager.get_account_or_fail(address)
    assert account_data["id"] == address


def test_create_new_account_with_invalid_address(accounts_manager: AccountsManager):
    invalid_address = "invalid_address"
    with pytest.raises(ValueError):
        accounts_manager.create_new_account_with_address(invalid_address)


def test_is_valid_address(accounts_manager: AccountsManager):
    valid_address = "0x9F0e84243496AcFB3Cd99D02eA59673c05901501"
    invalid_address = "invalid_address"

    assert accounts_manager.is_valid_address(valid_address) is True
    assert accounts_manager.is_valid_address(invalid_address) is False


def test_get_account(accounts_manager: AccountsManager):
    address = "0x9F0e84243496AcFB3Cd99D02eA59673c05901501"
    accounts_manager.create_new_account_with_address(address)

    account = accounts_manager.get_account(address)
    assert account is not None
    assert account.id == address

    non_existent_address = "0x0000000000000000000000000000000000000000"
    non_existent_account = accounts_manager.get_account(non_existent_address)
    assert non_existent_account is None


def test_get_account_or_fail(accounts_manager: AccountsManager):
    address = "0x9F0e84243496AcFB3Cd99D02eA59673c05901501"
    accounts_manager.create_new_account_with_address(address)

    account_data = accounts_manager.get_account_or_fail(address)
    assert account_data["id"] == address

    non_existent_address = "0x0000000000000000000000000000000000000000"
    with pytest.raises(AccountNotFoundError):
        accounts_manager.get_account_or_fail(non_existent_address)


def test_get_account_balance(accounts_manager: AccountsManager):
    address = "0x9F0e84243496AcFB3Cd99D02eA59673c05901501"
    accounts_manager.create_new_account_with_address(address)

    balance = accounts_manager.get_account_balance(address)
    assert balance == 0

    non_existent_address = "0x0000000000000000000000000000000000000000"
    non_existent_balance = accounts_manager.get_account_balance(non_existent_address)
    assert non_existent_balance == 0


def test_update_account_balance(accounts_manager: AccountsManager):
    address = "0x9F0e84243496AcFB3Cd99D02eA59673c05901501"
    accounts_manager.create_new_account_with_address(address)

    new_balance = 100
    accounts_manager.update_account_balance(address, new_balance)

    updated_balance = accounts_manager.get_account_balance(address)
    assert updated_balance == new_balance

    non_existent_address = "0x0000000000000000000000000000000000000000"
    accounts_manager.update_account_balance(non_existent_address, new_balance)

    created_account_balance = accounts_manager.get_account_balance(non_existent_address)
    assert created_account_balance == new_balance


def test_accounts_manager_update_timestamp(accounts_manager: AccountsManager):
    address = "0x9F0e84243496AcFB3Cd99D02eA59673c05901501"
    accounts_manager.create_new_account_with_address(address)

    account_data = accounts_manager.get_account_or_fail(address)
    first_updated_at = account_data["updated_at"]
    first_datetime = datetime.fromisoformat(first_updated_at)

    time.sleep(0.1)
    # Perform an action that should update the timestamp
    accounts_manager.update_account_balance(address, 100)

    account_data = accounts_manager.get_account_or_fail(address)
    second_updated_at = account_data["updated_at"]
    second_datetime = datetime.fromisoformat(second_updated_at)

    assert (
        second_datetime > first_datetime
    ), f"Expected {second_datetime} to be later than {first_datetime}"


def _fees_distribution(
    *,
    leader_timeunits=100,
    validator_timeunits=200,
    appeals=0,
    rotations=None,
    execution_budget_per_round=0,
    total_message_fees=0,
):
    if rotations is None:
        rotations = [0] * (appeals + 1)
    return {
        "leaderTimeunitsAllocation": leader_timeunits,
        "validatorTimeunitsAllocation": validator_timeunits,
        "appealRounds": appeals,
        "executionBudgetPerRound": execution_budget_per_round,
        "executionConsumed": 0,
        "totalMessageFees": total_message_fees,
        "rotations": rotations,
        "maxPriceGenPerTimeUnit": 0,
        "storageFeeMaxGasPrice": 0,
        "receiptFeeMaxGasPrice": 0,
    }


def _insert_fee_accounted_transaction(
    session: Session,
    *,
    sender: str,
    accounting: dict,
    tx_hash: str,
    value: int = 0,
    consensus_history: dict | None = None,
):
    transactions_processor = TransactionsProcessor(session)
    transactions_processor.insert_transaction(
        from_address=sender,
        to_address="0xAcec3A6d871C25F591aBd4fC24054e524BBbF794",
        data={FEE_ACCOUNTING_KEY: accounting},
        value=value,
        type=2,
        nonce=0,
        leader_only=False,
        config_rotation_rounds=3,
        transaction_hash=tx_hash,
        num_of_initial_validators=5,
    )
    if consensus_history is not None:
        tx_model = session.query(Transactions).filter_by(hash=tx_hash).one()
        tx_model.consensus_history = consensus_history
        session.commit()


def test_cancel_tx_fee_accounting_once_refunds_and_is_idempotent(
    accounts_manager: AccountsManager,
    session: Session,
):
    sender = "0x9F0e84243496AcFB3Cd99D02eA59673c05901501"
    tx_hash = "0x" + "ab" * 32
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=55),
        num_of_validators=5,
        submitted_value=1155,
        user_value=0,
        sender=sender,
    )
    _insert_fee_accounted_transaction(
        session,
        sender=sender,
        accounting=accounting,
        tx_hash=tx_hash,
    )

    refund = accounts_manager.cancel_tx_fee_accounting_once(tx_hash, sender)
    session.flush()
    session.expire_all()
    second_refund = accounts_manager.cancel_tx_fee_accounting_once(tx_hash, sender)
    session.flush()
    session.expire_all()

    assert refund == 1155
    assert second_refund == 0
    assert accounts_manager.get_account_balance(sender) == 1155
    tx = TransactionsProcessor(session).get_transaction_by_hash(tx_hash)
    fee_accounting = tx["data"][FEE_ACCOUNTING_KEY]
    assert fee_accounting["status"] == "canceled"
    assert fee_accounting["total_refunded"] == 1155


def test_abort_tx_appeal_admission_refunds_charge_and_restores_decision(
    accounts_manager: AccountsManager,
    session: Session,
):
    sender = "0x9F0e84243496AcFB3Cd99D02eA59673c05901501"
    appealer = "0x1111111111111111111111111111111111111111"
    tx_hash = "0x" + "ad" * 32
    fees = _fees_distribution(appeals=0, rotations=[0])
    accounting = create_fee_accounting(
        fees_distribution=fees,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees, 5),
        user_value=0,
        sender=sender,
    )
    charge = calculate_appeal_charge(
        accounting["fees_distribution"],
        current_round=0,
        status="ACCEPTED",
    )
    recorded = record_appeal_bond(
        accounting,
        amount=charge["bond"] + charge["funding"],
        appealer=appealer,
        current_round=0,
        status="ACCEPTED",
    )
    _insert_fee_accounted_transaction(
        session,
        sender=sender,
        accounting=recorded,
        tx_hash=tx_hash,
    )
    tx_model = session.query(Transactions).filter_by(hash=tx_hash).one()
    tx_model.appealed = True
    tx_model.timestamp_appeal = 123
    tx_model.consensus_history = {
        ACTIVE_APPEAL_BASIS_KEY: {
            "decisionId": 1,
            "submittedAt": 123,
            "nextAppealWindow": 10,
        }
    }
    tx_model.data = {
        **tx_model.data,
        APPEAL_RECOVERY_SNAPSHOT_KEY: {"status": "ACCEPTED"},
    }
    session.commit()

    expected_refund = charge["bond"] + charge["funding"]
    assert accounts_manager.abort_tx_appeal_admission_once(tx_hash) == expected_refund
    session.commit()
    assert accounts_manager.abort_tx_appeal_admission_once(tx_hash) == 0
    session.expire_all()

    assert accounts_manager.get_account_balance(appealer) == expected_refund
    tx = session.query(Transactions).filter_by(hash=tx_hash).one()
    restored = tx.data[FEE_ACCOUNTING_KEY]
    assert tx.appealed is False
    assert tx.timestamp_appeal is None
    assert ACTIVE_APPEAL_BASIS_KEY not in tx.consensus_history
    assert APPEAL_RECOVERY_SNAPSHOT_KEY not in tx.data
    assert restored["appeal_bonds"] == []
    assert restored["primary_fee_budget"] == accounting["primary_fee_budget"]
    assert restored["fees_distribution"] == accounting["fees_distribution"]


def test_admitted_appeal_snapshot_restores_exact_agreed_state_for_retry(
    session: Session,
):
    sender = "0x9F0e84243496AcFB3Cd99D02eA59673c05901501"
    tx_hash = "0x" + "af" * 32
    fees = _fees_distribution(appeals=0, rotations=[0])
    accounting = create_fee_accounting(
        fees_distribution=fees,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees, 5),
        user_value=0,
        sender=sender,
    )
    original_history = {
        "latestDecision": {
            "decisionId": 1,
            "status": "ACCEPTED",
            "materializedAt": 100,
            "appealDeadline": 1_000_000_000_000,
        },
        "consensus_results": [{"consensus_round": "ACCEPTED"}],
    }
    original_consensus_data = {"leader_receipt": [{"result": "original"}]}
    original_contract_snapshot = {"states": {"accepted": {"slot": "original"}}}
    _insert_fee_accounted_transaction(
        session,
        sender=sender,
        accounting=accounting,
        tx_hash=tx_hash,
        consensus_history=original_history,
    )
    tx = session.query(Transactions).filter_by(hash=tx_hash).one()
    tx.status = TransactionStatus.ACCEPTED
    tx.consensus_data = original_consensus_data
    tx.contract_snapshot = original_contract_snapshot
    tx.appeal_failed = 2
    tx.appeal_processing_time = 7
    tx.rotation_count = 1
    tx.timestamp_awaiting_finalization = 99
    session.commit()

    processor = TransactionsProcessor(session)
    processor.admit_transaction_appeal(
        tx_hash,
        expected_decision_id=1,
        submitted_at=200,
        appeal_deadline=1_000_000_000_000,
        retention_bps=8_000,
        prepare_fee_accounting=lambda current: (current, 0),
    )
    session.commit()
    session.expire_all()

    tx = session.query(Transactions).filter_by(hash=tx_hash).one()
    admitted_history = tx.consensus_history
    assert APPEAL_RECOVERY_SNAPSHOT_KEY in tx.data
    tx.status = TransactionStatus.COMMITTING
    tx.consensus_history = {"consensus_results": [{"partial": True}]}
    tx.consensus_data = {"partial": True}
    tx.contract_snapshot = {"states": {"accepted": {"slot": "partial"}}}
    tx.appealed = False
    tx.appeal_failed = 99
    tx.appeal_undetermined = True
    tx.appeal_leader_timeout = True
    tx.appeal_validators_timeout = True
    tx.appeal_processing_time = 999
    tx.rotation_count = 999
    tx.leader_timeout_validators = [{"address": "partial"}]
    tx.timestamp_awaiting_finalization = None
    tx.timestamp_appeal = None
    session.commit()

    assert processor.restore_transaction_appeal_for_retry(tx_hash) is True
    session.commit()
    session.expire_all()

    restored = session.query(Transactions).filter_by(hash=tx_hash).one()
    assert restored.status == TransactionStatus.ACCEPTED
    assert restored.consensus_history == admitted_history
    assert restored.consensus_data == original_consensus_data
    assert restored.contract_snapshot == original_contract_snapshot
    assert restored.appealed is True
    assert restored.appeal_failed == 2
    assert restored.appeal_undetermined is False
    assert restored.appeal_leader_timeout is False
    assert restored.appeal_validators_timeout is False
    assert restored.appeal_processing_time == 7
    assert restored.rotation_count == 1
    assert restored.leader_timeout_validators is None
    assert restored.timestamp_awaiting_finalization == 99
    assert restored.timestamp_appeal is not None


def test_appeal_recovery_snapshot_survives_pending_terminal_recomputation(
    session: Session,
):
    sender = "0x9F0e84243496AcFB3Cd99D02eA59673c05901501"
    tx_hash = "0x" + "ae" * 32
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(appeals=0, rotations=[0]),
        num_of_validators=5,
        submitted_value=required_fee_deposit(
            _fees_distribution(appeals=0, rotations=[0]), 5
        ),
        user_value=0,
        sender=sender,
    )
    _insert_fee_accounted_transaction(
        session,
        sender=sender,
        accounting=accounting,
        tx_hash=tx_hash,
        consensus_history={"consensus_results": []},
    )
    tx = session.query(Transactions).filter_by(hash=tx_hash).one()
    tx.status = TransactionStatus.PENDING
    tx.data = {
        **tx.data,
        APPEAL_RECOVERY_SNAPSHOT_KEY: {"status": "ACCEPTED"},
    }
    session.commit()

    processor = TransactionsProcessor(session)
    processor.clear_transaction_appeal_recovery_snapshot(tx_hash, include_pending=False)
    session.commit()
    session.expire_all()
    pending = session.query(Transactions).filter_by(hash=tx_hash).one()
    assert APPEAL_RECOVERY_SNAPSHOT_KEY in pending.data

    pending.status = TransactionStatus.ACCEPTED
    session.commit()
    processor.clear_transaction_appeal_recovery_snapshot(tx_hash, include_pending=False)
    session.commit()
    session.expire_all()
    completed = session.query(Transactions).filter_by(hash=tx_hash).one()
    assert APPEAL_RECOVERY_SNAPSHOT_KEY not in completed.data


def test_concurrent_appeal_admission_abort_refunds_only_once(engine: Engine):
    SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)
    sender = "0x9F0e84243496AcFB3Cd99D02eA59673c05901501"
    appealer = "0x1111111111111111111111111111111111111111"
    tx_hash = "0x" + "ae" * 32
    fees = _fees_distribution(appeals=0, rotations=[0])
    accounting = create_fee_accounting(
        fees_distribution=fees,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees, 5),
        user_value=0,
        sender=sender,
    )
    charge = calculate_appeal_charge(
        accounting["fees_distribution"], current_round=0, status="ACCEPTED"
    )
    recorded = record_appeal_bond(
        accounting,
        amount=charge["bond"] + charge["funding"],
        appealer=appealer,
        current_round=0,
        status="ACCEPTED",
    )
    with SessionFactory() as setup_session:
        _insert_fee_accounted_transaction(
            setup_session,
            sender=sender,
            accounting=recorded,
            tx_hash=tx_hash,
        )
        tx = setup_session.query(Transactions).filter_by(hash=tx_hash).one()
        tx.appealed = True
        tx.consensus_history = {
            ACTIVE_APPEAL_BASIS_KEY: {
                "decisionId": 1,
                "submittedAt": 123,
                "nextAppealWindow": 10,
            }
        }
        setup_session.commit()

    barrier = threading.Barrier(2, timeout=5)
    refunds: list[int] = []
    errors: list[BaseException] = []

    def abort():
        try:
            with SessionFactory() as worker_session:
                manager = AccountsManager(worker_session)
                barrier.wait()
                refunds.append(manager.abort_tx_appeal_admission_once(tx_hash))
                worker_session.commit()
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=abort)
    second = threading.Thread(target=abort)
    first.start()
    second.start()
    first.join(timeout=10)
    second.join(timeout=10)

    expected_refund = charge["bond"] + charge["funding"]
    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert sorted(refunds) == [0, expected_refund]
    with SessionFactory() as read_session:
        assert (
            AccountsManager(read_session).get_account_balance(appealer)
            == expected_refund
        )
        tx = read_session.query(Transactions).filter_by(hash=tx_hash).one()
        assert tx.data[FEE_ACCOUNTING_KEY]["appeal_bonds"] == []


def test_concurrent_cancel_refunds_fee_accounting_only_once(engine: Engine):
    SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)
    sender = "0x9F0e84243496AcFB3Cd99D02eA59673c05901501"
    tx_hash = "0x" + "ac" * 32
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=55),
        num_of_validators=5,
        submitted_value=1_155,
        user_value=0,
        sender=sender,
    )
    with SessionFactory() as setup_session:
        _insert_fee_accounted_transaction(
            setup_session,
            sender=sender,
            accounting=accounting,
            tx_hash=tx_hash,
        )

    barrier = threading.Barrier(2, timeout=5)
    refunds: list[int] = []
    errors: list[BaseException] = []

    def cancel():
        try:
            with SessionFactory() as worker_session:
                manager = AccountsManager(worker_session)
                barrier.wait()
                refunds.append(manager.cancel_tx_fee_accounting_once(tx_hash, sender))
                worker_session.commit()
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=cancel)
    second = threading.Thread(target=cancel)
    first.start()
    second.start()
    first.join(timeout=10)
    second.join(timeout=10)

    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert sorted(refunds) == [0, 1_155]
    with SessionFactory() as read_session:
        assert AccountsManager(read_session).get_account_balance(sender) == 1_155


def test_settle_tx_fee_accounting_once_refunds_surplus_and_is_idempotent(
    accounts_manager: AccountsManager,
    session: Session,
):
    sender = "0x9F0e84243496AcFB3Cd99D02eA59673c05901501"
    tx_hash = "0x" + "cd" * 32
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=55),
        num_of_validators=5,
        submitted_value=1267,
        user_value=12,
        sender=sender,
    )
    _insert_fee_accounted_transaction(
        session,
        sender=sender,
        accounting=accounting,
        tx_hash=tx_hash,
        value=12,
    )

    refund = accounts_manager.settle_tx_fee_accounting_once(tx_hash, sender)
    session.flush()
    session.expire_all()
    second_refund = accounts_manager.settle_tx_fee_accounting_once(tx_hash, sender)
    session.flush()
    session.expire_all()

    assert refund == 155
    assert second_refund == 0
    assert accounts_manager.get_account_balance(sender) == 155
    tx = TransactionsProcessor(session).get_transaction_by_hash(tx_hash)
    fee_accounting = tx["data"][FEE_ACCOUNTING_KEY]
    assert fee_accounting["status"] == "settled"
    assert fee_accounting["primary_fee_refunded"] == 100
    assert fee_accounting["message_fee_refunded"] == 55


def test_settle_tx_fee_accounting_credits_external_execution_payouts_once(
    accounts_manager: AccountsManager,
    session: Session,
):
    sender = "0x9F0e84243496AcFB3Cd99D02eA59673c05901501"
    executor = "0x9999999999999999999999999999999999999999"
    tx_hash = "0x" + "cf" * 32
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=1_000),
        num_of_validators=5,
        submitted_value=2_100,
        user_value=0,
        sender=sender,
    )
    accounting["message_fee_consumed"] = 700
    accounting["external_message_fee_reserved"] = 700
    accounting["external_message_fee_reimbursed"] = 420
    accounting["external_message_fee_remainder"] = 280
    accounting["external_message_fee_settled"] = 700
    accounting["external_message_fee_payouts"] = [
        {
            "recipient": executor,
            "amount": 420,
            "source": "external-executor-reimbursement",
        },
        {
            "recipient": sender,
            "amount": 280,
            "source": "external-execution-remainder",
        },
    ]
    _insert_fee_accounted_transaction(
        session,
        sender=sender,
        accounting=accounting,
        tx_hash=tx_hash,
    )

    refund = accounts_manager.settle_tx_fee_accounting_once(tx_hash, sender)
    second_refund = accounts_manager.settle_tx_fee_accounting_once(tx_hash, sender)
    session.flush()
    session.expire_all()

    assert refund == 300
    assert second_refund == 0
    assert accounts_manager.get_account_balance(executor) == 420
    assert accounts_manager.get_account_balance(sender) == 580


def test_settle_tx_fee_accounting_refunds_to_accounting_sender(
    accounts_manager: AccountsManager,
    session: Session,
):
    transaction_origin = "0x9F0e84243496AcFB3Cd99D02eA59673c05901501"
    contract_fee_sender = "0x1111111111111111111111111111111111111111"
    tx_hash = "0x" + "ce" * 32
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=55),
        num_of_validators=5,
        submitted_value=1_155,
        user_value=0,
        sender=contract_fee_sender,
    )
    _insert_fee_accounted_transaction(
        session,
        sender=transaction_origin,
        accounting=accounting,
        tx_hash=tx_hash,
    )

    refund = accounts_manager.settle_tx_fee_accounting_once(
        tx_hash,
        transaction_origin,
    )
    session.flush()
    session.expire_all()

    assert refund == 55
    assert accounts_manager.get_account_balance(contract_fee_sender) == 55
    assert accounts_manager.get_account_balance(transaction_origin) == 0


def test_settle_tx_fee_accounting_once_uses_actual_final_round_for_refund(
    accounts_manager: AccountsManager,
    session: Session,
):
    sender = "0x9F0e84243496AcFB3Cd99D02eA59673c05901501"
    tx_hash = "0x" + "ef" * 32
    fees_distribution = _fees_distribution(appeals=2, rotations=[0, 0, 0])
    submitted_value = required_fee_deposit(fees_distribution, 5)
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=submitted_value,
        user_value=0,
        sender=sender,
    )
    _insert_fee_accounted_transaction(
        session,
        sender=sender,
        accounting=accounting,
        tx_hash=tx_hash,
        consensus_history={"consensus_results": [{}]},
    )

    refund = accounts_manager.settle_tx_fee_accounting_once(tx_hash, sender)
    session.flush()
    session.expire_all()

    assert refund == submitted_value - 1100
    assert accounts_manager.get_account_balance(sender) == submitted_value - 1100
    tx = session.query(Transactions).filter_by(hash=tx_hash).one()
    fee_accounting = tx.data[FEE_ACCOUNTING_KEY]
    assert fee_accounting["actual_final_round"] == 0
    assert fee_accounting["primary_fee_spent"] == 1100
