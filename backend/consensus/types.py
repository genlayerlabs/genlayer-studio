from enum import Enum


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
