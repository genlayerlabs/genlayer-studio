# consensus/services/transactions_db_service.py

from eth_account import Account
from eth_utils import is_address

from .models import CurrentState
from backend.database_handler.errors import AccountNotFoundError

from sqlalchemy.orm import Session
from sqlalchemy import text


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

    def create_new_account_with_address(self, address: str) -> Account:
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

    def debit_account_balance(self, account_address: str, amount: int) -> bool:
        """Atomic conditional debit. Returns False if insufficient balance."""
        if amount <= 0:
            return True
        result = self.session.execute(
            text(
                "UPDATE current_state SET balance = balance - :amount "
                "WHERE id = :addr AND balance >= :amount"
            ),
            {"amount": amount, "addr": account_address},
        )
        return result.rowcount > 0

    def credit_account_balance(self, account_address: str, amount: int):
        """Atomic credit. Creates account if it doesn't exist."""
        if amount <= 0:
            return
        self.session.execute(
            text(
                "INSERT INTO current_state (id, data, balance) "
                "VALUES (:addr, '{}'::jsonb, 0) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"addr": account_address},
        )
        self.session.execute(
            text(
                "UPDATE current_state SET balance = balance + :amount "
                "WHERE id = :addr"
            ),
            {"amount": amount, "addr": account_address},
        )

    def credit_tx_value_once(
        self, tx_hash: str, target_address: str, amount: int
    ) -> bool:
        """Idempotent activation credit: credits target only if tx not already credited.
        Sets value_credited=true atomically. Returns True if credit was applied."""
        if amount <= 0:
            return False
        # Ensure target account exists
        self.session.execute(
            text(
                "INSERT INTO current_state (id, data, balance) "
                "VALUES (:addr, '{}'::jsonb, 0) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"addr": target_address},
        )
        # Atomic: set value_credited and credit balance in one operation
        result = self.session.execute(
            text(
                "UPDATE transactions SET value_credited = true "
                "WHERE hash = :hash AND value_credited = false AND value > 0"
            ),
            {"hash": tx_hash},
        )
        if result.rowcount > 0:
            self.session.execute(
                text(
                    "UPDATE current_state SET balance = balance + :amount "
                    "WHERE id = :addr"
                ),
                {"amount": amount, "addr": target_address},
            )
            # Expire ORM cache so subsequent reads see post-credit balances
            self.session.expire_all()
            return True
        return False

    def refund_tx_value(self, tx_hash: str, sender_address: str) -> bool:
        """Refund sender for a canceled/failed payable tx if value not yet credited to target.
        Returns True if refund was applied."""
        result = self.session.execute(
            text(
                "SELECT value, value_credited FROM transactions " "WHERE hash = :hash"
            ),
            {"hash": tx_hash},
        )
        row = result.first()
        if row is None or row.value is None or row.value <= 0:
            return False
        if row.value_credited:
            return False  # target already received funds, can't refund
        self.credit_account_balance(sender_address, row.value)
        return True
