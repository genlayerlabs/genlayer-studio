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
    fees_distribution: dict | None = None
    top_up_and_submit: bool = False


@dataclass
class DecodedTopUpFeesDataArgs:
    tx_id: str
    fees_distribution: dict


@dataclass
class DecodedRollupTransactionDataArgs:
    sender: str
    recipient: str
    num_of_initial_validators: int
    max_rotations: int
    data: str
    valid_until: int | None = None
    salt_nonce: int = 0
    user_value: int | None = None
    fees_distribution: dict | None = None
    message_allocations: list[dict] = field(default_factory=list)
    message_allocations_count: int = 0


@dataclass
class DecodedRollupTransactionData:
    function_name: str
    args: DecodedRollupTransactionDataArgs


@dataclass
class DecodedRollupTransaction:
    from_address: str
    to_address: str
    data: (
        DecodedRollupTransactionData
        | DecodedsubmitAppealDataArgs
        | DecodedTopUpFeesDataArgs
        | None
    )
    type: str
    nonce: int
    value: int
    fee_value: int = 0
    submitted_value: int | None = None

    @property
    def total_spend(self) -> int:
        if self.submitted_value is not None:
            return self.submitted_value
        return self.value + self.fee_value


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
