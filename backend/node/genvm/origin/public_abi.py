# This file is auto-generated. Do not edit!

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
    FD_ALLOCATION = 96


class SpecialMethod(StrEnum):
    GET_SCHEMA = "#get-schema"
    ERRORED_MESSAGE = "#error"


class VmError(StrEnum):
    TIMEOUT = "timeout"
    EXIT_CODE = "exit_code"
    VALIDATOR_DISAGREES = "validator_disagrees"
    VERSION_TOO_BIG = "version_too_big"
    OOM = "OOM"
    INVALID_CONTRACT = "invalid_contract"


EVENT_MAX_TOPICS: typing.Final[int] = 4


ABSENT_VERSION: typing.Final[str] = "v0.1.0"


CODE_SLOT_OFFSET: typing.Final[int] = 1
