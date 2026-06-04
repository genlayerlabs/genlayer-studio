from datetime import datetime
import time
from eth_account.signers.local import (
    LocalAccount,
)
import pytest
from sqlalchemy.orm import Session

from backend.database_handler.accounts_manager import AccountsManager
from backend.database_handler.errors import AccountNotFoundError
from backend.database_handler.models import Transactions
from backend.database_handler.transactions_processor import TransactionsProcessor
from backend.protocol_rpc.fees import FEE_ACCOUNTING_KEY, create_fee_accounting


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


def test_settle_tx_fee_accounting_once_uses_actual_final_round_for_refund(
    accounts_manager: AccountsManager,
    session: Session,
):
    sender = "0x9F0e84243496AcFB3Cd99D02eA59673c05901501"
    tx_hash = "0x" + "ef" * 32
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(appeals=2, rotations=[0, 0, 0]),
        num_of_validators=5,
        submitted_value=12300,
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

    assert refund == 11200
    assert accounts_manager.get_account_balance(sender) == 11200
    tx = session.query(Transactions).filter_by(hash=tx_hash).one()
    fee_accounting = tx.data[FEE_ACCOUNTING_KEY]
    assert fee_accounting["actual_final_round"] == 0
    assert fee_accounting["primary_fee_spent"] == 1100
