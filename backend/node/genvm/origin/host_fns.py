# This file is auto-generated. Do not edit!

# fmt: off
# ruff: noqa

import typing
from enum import IntEnum


class Methods(IntEnum):
    STORAGE_READ = 0
    CONSUME_FUEL = 1
    ETH_CALL = 2
    GET_BALANCE = 3
    REMAINING_FUEL_AS_GEN = 4
    NOTIFY_NONDET_DISAGREEMENT = 5
    CONSUME_RESULT = 6
    RESOLVE_CALLCONTRACT_EXECUTOR = 7
    RUN_NESTED = 8


class Errors(IntEnum):
    OK = 0
    EVM_REVERTED = 1
    FORBIDDEN = 2


class VmErrorDetail:
    __slots__ = ("value",)

    def __init__(self, value: str):
        self.value = value

    def __str__(self) -> str:
        return self.value

    @staticmethod
    def internal() -> "VmErrorDetail":
        return VmErrorDetail("internal")

    @staticmethod
    def external() -> "VmErrorDetail":
        return VmErrorDetail("external")


CURRENT_MAJOR: typing.Final[int] = 0


CURRENT_MAJOR_STR: typing.Final[str] = "v0.0.0"
