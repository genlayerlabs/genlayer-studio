import os
from enum import Enum
from dataclasses import dataclass
from typing import Any


class EventType(Enum):
    DEBUG = "debug"
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


class EventScope(Enum):
    RPC = "RPC"
    GENVM = "GenVM"
    CONSENSUS = "Consensus"
    TRANSACTION = "Transaction"


def _is_private_key_field(key: Any) -> bool:
    if not isinstance(key, str):
        return False

    normalized = key.replace("_", "").replace("-", "").lower()
    return normalized == "privatekey" or normalized.endswith("privatekey")


def sanitize_log_data(value: Any) -> Any:
    """Return a copy of log data with private-key fields removed."""
    if isinstance(value, dict):
        return {
            key: sanitize_log_data(item)
            for key, item in value.items()
            if not _is_private_key_field(key)
        }

    if isinstance(value, list):
        return [sanitize_log_data(item) for item in value]

    if isinstance(value, tuple):
        return tuple(sanitize_log_data(item) for item in value)

    if hasattr(value, "__dict__"):
        return sanitize_log_data(vars(value))

    return value


@dataclass
class LogEvent:
    name: str
    type: EventType
    scope: EventScope
    message: str
    data: dict | None = None
    transaction_hash: str | None = None
    client_session_id: str | None = None
    account_address: str | None = None

    def sanitized_data(self):
        return sanitize_log_data(self.data)

    def to_dict(self):
        return {
            "name": self.name,
            "type": self.type.value,
            "scope": self.scope.value,
            "message": self.message,
            "data": self.sanitized_data(),
            "transaction_hash": self.transaction_hash,
            "client_id": self.client_session_id,
            "account_address": self.account_address,
        }
