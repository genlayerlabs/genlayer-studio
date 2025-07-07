from enum import IntEnum, StrEnum
import typing


class ResultCode(IntEnum):
    RETURN = 0
    USER_ERROR = 1
    VM_ERROR = 2
    INTERNAL_ERROR = 3


class StorageType(IntEnum):
    DEFAULT = 0
    LATEST_FINAL = 1
    LATEST_NON_FINAL = 2


class EntryKind(IntEnum):
    MAIN = 0
    SANDBOX = 1
    CONSENSUS_STAGE = 2


class MemoryLimiterConsts(IntEnum):
    TABLE_ENTRY = 64
    FILE_MAPPING = 256


class SpecialMethod(StrEnum):
    GET_SCHEMA = "#get-schema"
    ERRORED_MESSAGE = "#error"


EVENT_MAX_TOPICS: typing.Final[int] = 4


ABSENT_VERSION: typing.Final[str] = "v0.1.0"
