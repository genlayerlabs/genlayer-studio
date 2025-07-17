from datetime import datetime
import time
from eth_account.signers.local import (
    LocalAccount,
)
import pytest
from sqlalchemy.orm import Session

from backend.database_handler.accounts_manager import AccountsManager
from backend.database_handler.errors import AccountNotFoundError
from backend.database_handler.transactions_processor import TransactionsProcessor


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


def test_set_account_balance(accounts_manager: AccountsManager):
    address = "0x9F0e84243496AcFB3Cd99D02eA59673c05901501"
    accounts_manager.create_new_account_with_address(address)

    new_balance = 100
    accounts_manager.set_account_balance(address, new_balance)

    updated_balance = accounts_manager.get_account_balance(address)
    assert updated_balance == new_balance

    non_existent_address = "0x0000000000000000000000000000000000000000"
    accounts_manager.set_account_balance(non_existent_address, new_balance)

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
    accounts_manager.set_account_balance(address, 100)

    account_data = accounts_manager.get_account_or_fail(address)
    second_updated_at = account_data["updated_at"]
    second_datetime = datetime.fromisoformat(second_updated_at)

    assert (
        second_datetime > first_datetime
    ), f"Expected {second_datetime} to be later than {first_datetime}"
