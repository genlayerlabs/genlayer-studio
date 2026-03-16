from enum import Enum
from dataclasses import dataclass, field
from backend.domain.types import TransactionType

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


class EndpointResultStatus(Enum):
    SUCCESS = "success"
    ERROR = "error"
    INFO = "info"


@dataclass
class EndpointResult:
    status: EndpointResultStatus
    message: str
    data: dict = field(default_factory=dict)
    exception: Exception = None

    def to_json(self) -> dict[str]:
        return {
            "status": self.status.value,
            "message": self.message,
            "data": self.data,
            "exception": str(self.exception) if self.exception else None,
        }


@dataclass
class DecodedsubmitAppealDataArgs:
    tx_id: str


@dataclass
class DecodedRollupTransactionDataArgs:
    sender: str
    recipient: str
    num_of_initial_validators: int
    max_rotations: int
    data: str


@dataclass
class DecodedRollupTransactionData:
    function_name: str
    args: DecodedRollupTransactionDataArgs


@dataclass
class DecodedRollupTransaction:
    from_address: str
    to_address: str
    data: DecodedRollupTransactionData | DecodedsubmitAppealDataArgs
    type: str
    nonce: int
    value: int


@dataclass
class DecodedMethodCallData:
    calldata: bytes


@dataclass
class DecodedMethodSendData:
    calldata: bytes
    leader_only: bool = False
    execution_mode: str = (
        "NORMAL"  # "NORMAL", "LEADER_ONLY", or "LEADER_SELF_VALIDATOR"
    )


@dataclass
class DecodedDeploymentData:
    contract_code: bytes
    calldata: bytes
    leader_only: bool = False
    execution_mode: str = (
        "NORMAL"  # "NORMAL", "LEADER_ONLY", or "LEADER_SELF_VALIDATOR"
    )


@dataclass
class DecodedGenlayerTransactionData:
    contract_code: str
    calldata: str
    leader_only: bool = False
    execution_mode: str = (
        "NORMAL"  # "NORMAL", "LEADER_ONLY", or "LEADER_SELF_VALIDATOR"
    )


@dataclass
class DecodedGenlayerTransaction:
    from_address: str
    to_address: str
    data: DecodedGenlayerTransactionData
    type: TransactionType
    max_rotations: int
    num_of_initial_validators: int
