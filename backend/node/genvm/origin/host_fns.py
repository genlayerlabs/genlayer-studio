# This file is auto-generated. Do not edit!

from enum import IntEnum, StrEnum
import typing


class Methods(IntEnum):
    GET_CALLDATA = 0
    STORAGE_READ = 1
    STORAGE_WRITE = 2
    CONSUME_RESULT = 3
    GET_LEADER_NONDET_RESULT = 4
    POST_NONDET_RESULT = 5
    POST_MESSAGE = 6
    POST_EVENT = 7
    CONSUME_FUEL = 8
    DEPLOY_CONTRACT = 9
    ETH_CALL = 10
    ETH_SEND = 11
    GET_BALANCE = 12
    REMAINING_FUEL_AS_GEN = 13
    NOTIFY_NONDET_DISAGREEMENT = 14


class Errors(IntEnum):
    OK = 0
    ABSENT = 1
    FORBIDDEN = 2
    I_AM_LEADER = 3
    OUT_OF_STORAGE_GAS = 4
