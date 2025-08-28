from enum import Enum
from dataclasses import dataclass


class EventType(Enum):
    DEBUG = "debug"
    INFO = "info"
    SUCCESS = "success"
    ERROR = "error"


class EventScope(Enum):
    RPC = "RPC"
    GENVM = "GenVM"
    CONSENSUS = "Consensus"


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

    def to_dict(self):
        return {
            "name": self.name,
            "type": self.type.value,
            "scope": self.scope.value,
            "message": self.message,
            "data": self.data,
            "transaction_hash": self.transaction_hash,
            "client_id": self.client_session_id,
            "account_address": self.account_address,
        }
