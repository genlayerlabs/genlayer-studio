from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Effect:
    """Base class for all consensus side effects."""

    pass


# ── Timestamp Effects ──────────────────────────────────────────────


@dataclass(frozen=True)
class AddTimestampEffect(Effect):
    tx_hash: str
    state_name: str


# ── Status Effects ─────────────────────────────────────────────────


@dataclass(frozen=True)
class StatusUpdateEffect(Effect):
    tx_hash: str
    new_status: str  # TransactionStatus.value
    update_current_status_changes: bool = True


# ── Messaging Effects ──────────────────────────────────────────────


@dataclass(frozen=True)
class SendMessageEffect(Effect):
    event_name: str
    event_type: str  # EventType.value
    event_scope: str  # EventScope.value
    message: str
    data: dict | None = None
    tx_hash: str | None = None
    log_to_terminal: bool = True


# ── Rollup / On-Chain Effects ──────────────────────────────────────


@dataclass(frozen=True)
class EmitRollupEventEffect(Effect):
    event_name: str
    account: dict  # validator/leader dict passed to emit_transaction_event
    tx_hash: str
    extra_args: tuple = ()  # positional args after tx_hash


# ── Database Write Effects ─────────────────────────────────────────


@dataclass(frozen=True)
class DBWriteEffect(Effect):
    """Generic TransactionsProcessor method call."""

    method_name: str
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)


# ── Contract Write Effects ─────────────────────────────────────────


@dataclass(frozen=True)
class RegisterContractEffect(Effect):
    contract_data: dict


@dataclass(frozen=True)
class UpdateContractStateEffect(Effect):
    address: str
    accepted_state: dict | None = None
    finalized_state: dict | None = None


# ── Triggered Transaction Effects ──────────────────────────────────


@dataclass(frozen=True)
class InsertTriggeredTransactionEffect(Effect):
    from_address: str
    to_address: str
    data: dict
    value: int
    tx_type: str  # TransactionType.value
    nonce: int
    leader_only: bool
    num_of_initial_validators: int
    triggered_by_hash: str
    transaction_hash: str | None
    config_rotation_rounds: int
    sim_config: dict | None
    triggered_on: str  # "accepted" | "finalized"
    execution_mode: str  # TransactionExecutionMode.value


# ── Consensus History Effects ──────────────────────────────────────


@dataclass(frozen=True)
class UpdateConsensusHistoryEffect(Effect):
    tx_hash: str
    consensus_round: Any  # ConsensusRound enum value
    leader_receipt: Any  # list[Receipt] | None
    validation_results: Any  # list[Receipt]
    new_status: Any = None  # TransactionStatus | None


@dataclass(frozen=True)
class SetTransactionResultEffect(Effect):
    tx_hash: str
    consensus_data_dict: dict


# ── Appeal-Related Effects ─────────────────────────────────────────


@dataclass(frozen=True)
class SetAppealEffect(Effect):
    tx_hash: str
    appealed: bool


@dataclass(frozen=True)
class SetAppealUndeterminedEffect(Effect):
    tx_hash: str
    value: bool


@dataclass(frozen=True)
class SetAppealLeaderTimeoutEffect(Effect):
    tx_hash: str
    value: bool


@dataclass(frozen=True)
class SetAppealValidatorsTimeoutEffect(Effect):
    tx_hash: str
    value: bool


@dataclass(frozen=True)
class SetAppealFailedEffect(Effect):
    tx_hash: str
    count: int


@dataclass(frozen=True)
class SetAppealProcessingTimeEffect(Effect):
    tx_hash: str


@dataclass(frozen=True)
class ResetAppealProcessingTimeEffect(Effect):
    tx_hash: str


@dataclass(frozen=True)
class SetTimestampAppealEffect(Effect):
    tx_hash: str
    value: int | None


@dataclass(frozen=True)
class SetTimestampAwaitingFinalizationEffect(Effect):
    tx_hash: str


# ── Contract Snapshot Effects ──────────────────────────────────────


@dataclass(frozen=True)
class SetContractSnapshotEffect(Effect):
    tx_hash: str
    snapshot_dict: dict | None


# ── Leader Timeout Validators ──────────────────────────────────────


@dataclass(frozen=True)
class SetLeaderTimeoutValidatorsEffect(Effect):
    tx_hash: str
    validators: list


# ── Rotation Count ─────────────────────────────────────────────────


@dataclass(frozen=True)
class ResetRotationCountEffect(Effect):
    tx_hash: str


@dataclass(frozen=True)
class IncreaseRotationCountEffect(Effect):
    tx_hash: str


# ── Timestamp Last Vote ───────────────────────────────────────────


@dataclass(frozen=True)
class SetTimestampLastVoteEffect(Effect):
    tx_hash: str
