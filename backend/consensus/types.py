from enum import Enum

from backend.node.types import ExecutionResultStatus, Vote


class ConsensusResult(Enum):
    IDLE = "IDLE"
    AGREE = "AGREE"
    DISAGREE = "DISAGREE"
    TIMEOUT = "TIMEOUT"
    DETERMINISTIC_VIOLATION = "DETERMINISTIC_VIOLATION"
    NO_MAJORITY = "NO_MAJORITY"
    MAJORITY_AGREE = "MAJORITY_AGREE"
    MAJORITY_DISAGREE = "MAJORITY_DISAGREE"

    @classmethod
    def from_string(cls, value: str) -> "ConsensusResult":
        try:
            return cls(value)
        except ValueError:
            raise ValueError(f"Invalid transaction result value: {value}")

    def __int__(self) -> int:
        values = {
            ConsensusResult.IDLE: 0,
            ConsensusResult.AGREE: 1,
            ConsensusResult.DISAGREE: 2,
            ConsensusResult.TIMEOUT: 3,
            ConsensusResult.DETERMINISTIC_VIOLATION: 4,
            ConsensusResult.NO_MAJORITY: 5,
            ConsensusResult.MAJORITY_AGREE: 6,
            ConsensusResult.MAJORITY_DISAGREE: 7,
        }
        return values[self]


def consensus_result_type_code(result: ConsensusResult) -> int:
    """Return the canonical v0.6 ITransactions.ResultType ordinal."""

    return {
        ConsensusResult.IDLE: 0,
        ConsensusResult.AGREE: 1,
        ConsensusResult.MAJORITY_AGREE: 1,
        ConsensusResult.DISAGREE: 2,
        ConsensusResult.MAJORITY_DISAGREE: 2,
        ConsensusResult.TIMEOUT: 3,
        ConsensusResult.DETERMINISTIC_VIOLATION: 4,
        ConsensusResult.NO_MAJORITY: 5,
    }[result]


def consensus_vote_type_code(
    vote: Vote | str,
    execution_result: ExecutionResultStatus | str | None = None,
) -> int:
    """Translate a Studio ballot to the canonical v0.6 VoteType ordinal."""

    local_vote = Vote.from_string(vote) if isinstance(vote, str) else vote
    if local_vote == Vote.NOT_VOTED:
        return 0
    if local_vote == Vote.AGREE:
        status = (
            execution_result.value
            if isinstance(execution_result, ExecutionResultStatus)
            else str(execution_result or "").upper()
        )
        return 2 if status == ExecutionResultStatus.ERROR.value else 1
    if local_vote in {Vote.TIMEOUT, Vote.IDLE}:
        return 3
    if local_vote == Vote.DISAGREE:
        return 4
    if local_vote == Vote.DETERMINISTIC_VIOLATION:
        return 5
    raise ValueError(f"Unsupported vote: {local_vote}")


class ConsensusRound(Enum):
    ACCEPTED = "Accepted"
    LEADER_ROTATION = "Leader Rotation"
    UNDETERMINED = "Undetermined"
    LEADER_TIMEOUT = "Leader Timeout"
    VALIDATORS_TIMEOUT = "Validators Timeout"
    LEADER_ROTATION_APPEAL = "Leader Rotation Appeal"
    VALIDATOR_APPEAL_SUCCESSFUL = "Validator Appeal Successful"
    VALIDATOR_APPEAL_FAILED = "Validator Appeal Failed"
    LEADER_APPEAL_SUCCESSFUL = "Leader Appeal Successful"
    LEADER_APPEAL_FAILED = "Leader Appeal Failed"
    LEADER_TIMEOUT_APPEAL_SUCCESSFUL = "Leader Timeout Appeal Successful"
    LEADER_TIMEOUT_APPEAL_FAILED = "Leader Timeout Appeal Failed"
    VALIDATOR_TIMEOUT_APPEAL_SUCCESSFUL = "Validator Timeout Appeal Successful"
    VALIDATORS_TIMEOUT_APPEAL_FAILED = "Validators Timeout Appeal Failed"
