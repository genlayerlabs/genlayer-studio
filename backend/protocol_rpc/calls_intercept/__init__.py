from abc import ABC, abstractmethod
from backend.database_handler.transactions_processor import TransactionsProcessor


class CallHandler(ABC):
    """Abstract base class for ConsensusData contract call handlers."""

    @abstractmethod
    def can_handle(self, data: str) -> bool:
        """Check if this handler can process the given call data."""
        pass

    @abstractmethod
    def handle(self, transactions_processor: TransactionsProcessor, data: str) -> str:
        """Handle the contract call and return the result."""
        pass
