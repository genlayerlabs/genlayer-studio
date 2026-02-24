# database_handler/chain_snapshot.py

from typing import List
from sqlalchemy.orm import Session, selectinload, load_only
from collections import defaultdict

from backend.database_handler.transactions_processor import (
    TransactionStatus,
    Transactions,
)
from .transactions_processor import TransactionsProcessor
from backend.database_handler.validators_registry import ValidatorsRegistry


class ChainSnapshot:
    def __init__(self, session: Session):
        self.session = session
        self.pending_transactions = self._load_pending_transactions()
        self.awaiting_finalization_transactions = (
            self._load_awaiting_finalization_transactions()
        )

        del self.session

    def _load_pending_transactions(self) -> List[dict]:
        """Load and return the list of pending transactions from the database."""

        pending_transactions = (
            self.session.query(Transactions)
            .filter(Transactions.status == TransactionStatus.PENDING)
            .options(
                selectinload(Transactions.triggered_transactions).load_only(
                    Transactions.hash
                )
            )
            .order_by(Transactions.created_at)
            .all()
        )
        return [
            TransactionsProcessor._parse_transaction_data(transaction)
            for transaction in pending_transactions
        ]

    def get_pending_transactions(self):
        """Return the list of pending transactions."""
        return self.pending_transactions

    def _load_awaiting_finalization_transactions(self) -> dict[str, List[dict]]:
        """Load and return the list of transactions that are awaiting finalization from the database,
        grouped by address."""

        awaiting_finalization_transactions = (
            self.session.query(Transactions)
            .filter(
                (Transactions.status == TransactionStatus.ACCEPTED)
                | (Transactions.status == TransactionStatus.UNDETERMINED)
                | (Transactions.status == TransactionStatus.LEADER_TIMEOUT)
                | (Transactions.status == TransactionStatus.VALIDATORS_TIMEOUT)
            )
            .options(
                selectinload(Transactions.triggered_transactions).load_only(
                    Transactions.hash
                )
            )
            .order_by(Transactions.created_at)
            .all()
        )

        # Group transactions by address
        transactions_by_address = defaultdict(list)
        for transaction in awaiting_finalization_transactions:
            address = transaction.to_address
            transactions_by_address[address].append(
                TransactionsProcessor._parse_transaction_data(transaction)
            )
        return transactions_by_address

    def get_awaiting_finalization_transactions(self):
        """Return the list of transactions that are awaiting finalization."""
        return self.awaiting_finalization_transactions
