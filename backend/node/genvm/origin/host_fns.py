# This file is auto-generated. Do not edit!

from enum import IntEnum, StrEnum
import typing


class Methods(IntEnum):
    STORAGE_READ = 0
    STORAGE_WRITE = 1
    CONSUME_FUEL = 2
    ETH_CALL = 3
    GET_BALANCE = 4
    REMAINING_FUEL_AS_GEN = 5
    NOTIFY_NONDET_DISAGREEMENT = 6
    CONSUME_RESULT = 7
    NOTIFY_FINISHED = 8


class Errors(IntEnum):
    OK = 0
    ABSENT = 1
    FORBIDDEN = 2
    OUT_OF_STORAGE_GAS = 3
