# consensus/services/transactions_db_service.py

from eth_account import Account
from eth_utils import is_address

from .models import CurrentState
from backend.database_handler.errors import AccountNotFoundError

from sqlalchemy.orm import Session


class AccountsManager:
    def __init__(self, session: Session):
        self.session = session

    def _parse_account_data(self, account_data: CurrentState) -> dict:
        return {
            "id": account_data.id,
            "data": account_data.data,
            "balance": account_data.balance,
            "updated_at": account_data.updated_at.isoformat(),
        }

    def create_new_account(self) -> Account:
        """
        Used when generating intelligent contract's accounts or sending funds to a new account.
        Users should create their accounts client-side
        """
        account = Account.create()
        self.create_new_account_with_address(account.address)
        return account

    def create_new_account_with_address(self, address: str) -> CurrentState:
        # Check if account already exists
        if not is_address(address):
            raise ValueError(f"Invalid address: {address}")

        existing_account = (
            self.session.query(CurrentState).filter(CurrentState.id == address).first()
        )
        if existing_account is not None:
            return existing_account

        # If account doesn't exist, create it
        account = CurrentState(id=address, data={}, balance=0)
        self.session.add(account)
        self.session.commit()
        return account

    def is_valid_address(self, address: str) -> bool:
        return is_address(address)

    def get_account(self, account_address: str) -> CurrentState | None:
        """Private method to retrieve an account from the data base"""
        account = (
            self.session.query(CurrentState)
            .filter(CurrentState.id == account_address)
            .one_or_none()
        )
        return account

    def get_account_or_fail(self, account_address: str) -> dict:
        """Private method to check if an account exists, and raise an error if not."""
        account_data = self.get_account(account_address)
        if not account_data:
            raise AccountNotFoundError(
                account_address, f"Account {account_address} does not exist."
            )
        return self._parse_account_data(account_data)

    def get_account_balance(self, account_address: str) -> int:
        account = self.get_account(account_address)
        if not account:
            return 0
        return account.balance

    def update_account_balance(self, account_address: str, new_balance: int):
        to_account = self.get_account(account_address)
        if to_account is None:
            self.create_new_account_with_address(account_address)
            to_account = self.get_account(account_address)
        to_account.balance = new_balance
