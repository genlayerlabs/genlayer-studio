# This file is auto-generated. Do not edit!

from enum import IntEnum
import typing


class Methods(IntEnum):
    STORAGE_READ = 0
    CONSUME_FUEL = 1
    ETH_CALL = 2
    GET_BALANCE = 3
    REMAINING_FUEL_AS_GEN = 4
    NOTIFY_NONDET_DISAGREEMENT = 5
    CONSUME_RESULT = 6
    NOTIFY_FINISHED = 7


class Errors(IntEnum):
    OK = 0
    ABSENT = 1
    FORBIDDEN = 2
    OUT_OF_STORAGE_GAS = 3


CURRENT_MAJOR: typing.Final[int] = 0


CURRENT_MAJOR_STR: typing.Final[str] = "v0.0.0"
