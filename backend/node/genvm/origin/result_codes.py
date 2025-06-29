from enum import IntEnum


class ResultCode(IntEnum):
    RETURN = 0
    ROLLBACK = 1
    CONTRACT_ERROR = 2
    ERROR = 3


class StorageType(IntEnum):
    DEFAULT = 0
    LATEST_FINAL = 1
    LATEST_NON_FINAL = 2


class EntryKind(IntEnum):
    MAIN = 0
    SANDBOX = 1
    CONSENSUS_STAGE = 2
