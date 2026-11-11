# This file is auto-generated. Do not edit!

# fmt: off
# ruff: noqa

import typing
from enum import IntEnum

class Methods(IntEnum):
    ERROR = 0
    HELLO = 1
    EVENT = 2
    RUN = 3
    ATTACH = 4
    CANCEL = 5
    ACK = 6
    GET_ARTIFACT = 7


class Errors(IntEnum):
    INTERNAL = 0
    MALFORMED_FRAME = 1
    UNKNOWN_METHOD = 2
    UNKNOWN_ID = 3
    BOOT_ID_MISMATCH = 4
    BAD_REQUEST_ID = 5
    NOT_FINISHED = 6


CURRENT_MAJOR: typing.Final[int] = 0
