# This file is auto-generated. Do not edit!

# fmt: off
# ruff: noqa

import typing
from enum import IntEnum, StrEnum


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


class Permissions(IntEnum):
    CAN_USE_BALANCE_FOR_MESSAGE_FEES = 1


class _MemoryLimiterConsts(typing.NamedTuple):
    TABLE_ENTRY: int = 64
    FILE_MAPPING: int = 256
    FD_ALLOCATION: int = 96
    RUNNER_LOAD_COST: int = 4096
    VM_SPAWN_COST: int = 134217728


memory_limiter_consts: typing.Final = _MemoryLimiterConsts()


class _RootOffsets(typing.NamedTuple):
    MAJOR: int = 0
    CONTRACT: int = 1
    CODE: int = 2
    LOCKED_SLOTS: int = 3
    UPGRADERS: int = 4
    CODE_SLOT: int = 5
    PERMISSIONS: int = 37


root_offsets: typing.Final = _RootOffsets()


class _TopLimits(typing.NamedTuple):
    NONDET_BLOCKS: int = 4096
    LOCKED_SLOTS: int = 256
    UPGRADERS: int = 32
    VM_RECURSION: int = 512
    WEB_REQUEST_MIN_SPACE: int = 65536
    WEB_RENDER_MIN_SPACE: int = 134217728
    MAX_FDS: int = 1024
    WASM_CALL_DEPTH: int = 1024
    WASM_STACK_VALUE_SLOTS: int = 65535


top_limits: typing.Final = _TopLimits()


class SpecialMethod(StrEnum):
    GET_SCHEMA = "#get-schema"
    ERRORED_MESSAGE = "#error"


class _VmErrorWasmTrap:
    @staticmethod
    def val() -> "VmError":
        return VmError("wasm_trap")

    @staticmethod
    def unreachable() -> "VmError":
        return VmError("wasm_trap unreachable")

    @staticmethod
    def stack_overflow() -> "VmError":
        return VmError("wasm_trap stack_overflow")

    @staticmethod
    def memory_out_of_bounds() -> "VmError":
        return VmError("wasm_trap memory_out_of_bounds")

    @staticmethod
    def table_out_of_bounds() -> "VmError":
        return VmError("wasm_trap table_out_of_bounds")

    @staticmethod
    def indirect_call_to_null() -> "VmError":
        return VmError("wasm_trap indirect_call_to_null")

    @staticmethod
    def bad_signature() -> "VmError":
        return VmError("wasm_trap bad_signature")

    @staticmethod
    def integer_overflow() -> "VmError":
        return VmError("wasm_trap integer_overflow")

    @staticmethod
    def integer_divide_by_zero() -> "VmError":
        return VmError("wasm_trap integer_divide_by_zero")

    @staticmethod
    def bad_conversion_to_integer() -> "VmError":
        return VmError("wasm_trap bad_conversion_to_integer")

    @staticmethod
    def heap_misaligned() -> "VmError":
        return VmError("wasm_trap heap_misaligned")

    @staticmethod
    def atomic_wait_non_shared_memory() -> "VmError":
        return VmError("wasm_trap atomic_wait_non_shared_memory")

    @staticmethod
    def out_of_fuel() -> "VmError":
        return VmError("wasm_trap out_of_fuel")

    @staticmethod
    def interrupt() -> "VmError":
        return VmError("wasm_trap interrupt")

    @staticmethod
    def nondet_instruction() -> "VmError":
        return VmError("wasm_trap nondet_instruction")

    @staticmethod
    def fault() -> "VmError":
        return VmError("wasm_trap fault")


class _VmErrorOutOfMemory:
    @staticmethod
    def val() -> "VmError":
        return VmError("out_of memory")

    @staticmethod
    def wasm_memory() -> "VmError":
        return VmError("out_of memory wasm_memory")

    @staticmethod
    def wasm_table() -> "VmError":
        return VmError("out_of memory wasm_table")


class _VmErrorOutOfReceipt:
    @staticmethod
    def nondet_output() -> "VmError":
        return VmError("out_of receipt nondet_output")

    @staticmethod
    def message() -> "VmError":
        return VmError("out_of receipt message")

    @staticmethod
    def event() -> "VmError":
        return VmError("out_of receipt event")


class _VmErrorOutOfMessageFee:
    @staticmethod
    def total() -> "VmError":
        return VmError("out_of message_fee total")

    @staticmethod
    def node() -> "VmError":
        return VmError("out_of message_fee node")


class _VmErrorOutOf:
    @staticmethod
    def storage() -> "VmError":
        return VmError("out_of storage")

    @staticmethod
    def vm_recursion() -> "VmError":
        return VmError("out_of vm_recursion")

    @staticmethod
    def nondet_blocks() -> "VmError":
        return VmError("out_of nondet_blocks")

    @staticmethod
    def locked_slots() -> "VmError":
        return VmError("out_of locked_slots")

    @staticmethod
    def upgraders() -> "VmError":
        return VmError("out_of upgraders")

    @staticmethod
    def fds() -> "VmError":
        return VmError("out_of fds")

    @staticmethod
    def memory() -> "_VmErrorOutOfMemory":
        return _VmErrorOutOfMemory()

    @staticmethod
    def receipt() -> "_VmErrorOutOfReceipt":
        return _VmErrorOutOfReceipt()

    @staticmethod
    def message_fee() -> "_VmErrorOutOfMessageFee":
        return _VmErrorOutOfMessageFee()


class _VmErrorFee:
    @staticmethod
    def no_matching_node() -> "VmError":
        return VmError("fee no_matching_node")

    @staticmethod
    def below_minimum() -> "VmError":
        return VmError("fee below_minimum")

    @staticmethod
    def too_many_rounds() -> "VmError":
        return VmError("fee too_many_rounds")


class _VmErrorEvm:
    @staticmethod
    def reverted() -> "VmError":
        return VmError("evm reverted")


class _VmErrorInvalidContractWasm:
    @staticmethod
    def validating() -> "VmError":
        return VmError("invalid_contract wasm validating")

    @staticmethod
    def linking() -> "VmError":
        return VmError("invalid_contract wasm linking")

    @staticmethod
    def entrypoint() -> "VmError":
        return VmError("invalid_contract wasm entrypoint")


class _VmErrorInvalidContract:
    @staticmethod
    def val() -> "VmError":
        return VmError("invalid_contract")

    @staticmethod
    def absent_runner_comment() -> "VmError":
        return VmError("invalid_contract absent_runner_comment")

    @staticmethod
    def not_utf8_text() -> "VmError":
        return VmError("invalid_contract not_utf8_text")

    @staticmethod
    def malformed_runner() -> "VmError":
        return VmError("invalid_contract malformed_runner")

    @staticmethod
    def major_mismatch() -> "VmError":
        return VmError("invalid_contract major_mismatch")

    @staticmethod
    def wasm() -> "_VmErrorInvalidContractWasm":
        return _VmErrorInvalidContractWasm()


class _VmErrorExitCode:
    @staticmethod
    def val_i32(v: int) -> "VmError":
        return VmError(f"exit_code {v}")


class VmError:
    __slots__ = ("value",)

    def __init__(self, value: str):
        self.value = value

    def __str__(self) -> str:
        return self.value

    @staticmethod
    def timeout() -> "VmError":
        return VmError("timeout")

    @staticmethod
    def absent_leader_nondet_output() -> "VmError":
        return VmError("absent_leader_nondet_output")

    @staticmethod
    def host_forbidden() -> "VmError":
        return VmError("host_forbidden")

    @staticmethod
    def exit_code() -> "_VmErrorExitCode":
        return _VmErrorExitCode()

    @staticmethod
    def wasm_trap() -> "_VmErrorWasmTrap":
        return _VmErrorWasmTrap()

    @staticmethod
    def out_of() -> "_VmErrorOutOf":
        return _VmErrorOutOf()

    @staticmethod
    def fee() -> "_VmErrorFee":
        return _VmErrorFee()

    @staticmethod
    def evm() -> "_VmErrorEvm":
        return _VmErrorEvm()

    @staticmethod
    def invalid_contract() -> "_VmErrorInvalidContract":
        return _VmErrorInvalidContract()


EVENT_MAX_TOPICS: typing.Final[int] = 4
