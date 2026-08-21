# This file is auto-generated. Do not edit!

# fmt: off
# ruff: noqa

import typing
from enum import IntEnum


class Methods(IntEnum):
    STORAGE_READ = 0
    CONSUME_TIME_FEE_GEN_WEI = 1
    EXTERNAL_CALL = 2
    GET_BALANCE_GEN_WEI = 3
    GET_REMAINING_TIME_FEE_GEN_WEI = 4
    NOTIFY_NONDET_DISAGREEMENT = 5
    CONSUME_RESULT = 6
    RESOLVE_CALL_CONTRACT_EXECUTOR = 7
    RUN_NESTED = 8


class ResultCode(IntEnum):
    RETURN = 0
    USER_ERROR = 1
    VM_ERROR = 2
    INTERNAL_ERROR = 3
    FATAL_VM_ERROR = 4


class Errors(IntEnum):
    OK = 0
    EVM_REVERTED = 1
    FORBIDDEN = 2


CURRENT_MAJOR: typing.Final[int] = 0


CURRENT_MAJOR_STR: typing.Final[str] = "v0.0.0"
