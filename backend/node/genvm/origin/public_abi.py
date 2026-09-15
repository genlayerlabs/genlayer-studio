# This file is auto-generated. Do not edit!

# fmt: off
# ruff: noqa

import typing
from enum import IntEnum, StrEnum


class ResultCode(IntEnum):
    RETURN = 0
    USER_ERROR = 1
    VM_ERROR = 2


class StorageView(IntEnum):
    DEFAULT = 0
    LATEST_FINALIZED = 1
    LATEST_DECIDED = 2


class EntryKind(IntEnum):
    MAIN = 0
    SANDBOX = 1
    CONSENSUS_STAGE = 2


class Permissions(IntEnum):
    CAN_USE_BALANCE_FOR_MESSAGE_FEES = 1


class _RootOffsets(typing.NamedTuple):
    MAJOR: int = 0
    CONTRACT: int = 1
    CODE: int = 2
    LOCKED_SLOTS: int = 3
    UPGRADERS: int = 4
    CODE_SLOT: int = 5
    PERMISSIONS: int = 37


root_offsets: typing.Final = _RootOffsets()


class SpecialMethod(StrEnum):
    GET_SCHEMA = "#get-schema"
    ERRORED_MESSAGE = "#error"


class _VmErrorLeaderFaultNondetOutput:
    @staticmethod
    def absent() -> "VmError":
        return VmError("leader_fault nondet_output absent")

    @staticmethod
    def malformed() -> "VmError":
        return VmError("leader_fault nondet_output malformed")

    @staticmethod
    def uses_this_error() -> "_VmErrorLeaderFaultNondetOutputUsesThisError":
        return _VmErrorLeaderFaultNondetOutputUsesThisError()

    @staticmethod
    def extra() -> "_VmErrorLeaderFaultNondetOutputExtra":
        return _VmErrorLeaderFaultNondetOutputExtra()


class _VmErrorLeaderFaultNondetOutputUsesThisError:
    @staticmethod
    def val_str(v: str) -> "VmError":
        return VmError(f"leader_fault nondet_output uses_this_error {v}")


class _VmErrorLeaderFaultNondetOutputExtra:
    @staticmethod
    def val_str(v: str) -> "VmError":
        return VmError(f"leader_fault nondet_output extra {v}")


class _VmErrorLeaderFault:
    @staticmethod
    def nondet_output() -> "_VmErrorLeaderFaultNondetOutput":
        return _VmErrorLeaderFaultNondetOutput()


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


class _VmErrorOutOfReceiptMessage:
    @staticmethod
    def val() -> "VmError":
        return VmError("out_of receipt message")

    @staticmethod
    def internal() -> "VmError":
        return VmError("out_of receipt message # internal")


class _VmErrorOutOfReceipt:
    @staticmethod
    def nondet_output() -> "VmError":
        return VmError("out_of receipt nondet_output")

    @staticmethod
    def event() -> "VmError":
        return VmError("out_of receipt event")

    @staticmethod
    def message() -> "_VmErrorOutOfReceiptMessage":
        return _VmErrorOutOfReceiptMessage()


class _VmErrorOutOfMessageFeeTotal:
    @staticmethod
    def val() -> "VmError":
        return VmError("out_of message_fee total")

    @staticmethod
    def internal() -> "VmError":
        return VmError("out_of message_fee total # internal")

    @staticmethod
    def external() -> "VmError":
        return VmError("out_of message_fee total # external")


class _VmErrorOutOfMessageFeeAllocationBudget:
    @staticmethod
    def val() -> "VmError":
        return VmError("out_of message_fee allocation_budget")

    @staticmethod
    def internal() -> "VmError":
        return VmError("out_of message_fee allocation_budget # internal")

    @staticmethod
    def external() -> "VmError":
        return VmError("out_of message_fee allocation_budget # external")


class _VmErrorOutOfMessageFee:
    @staticmethod
    def total() -> "_VmErrorOutOfMessageFeeTotal":
        return _VmErrorOutOfMessageFeeTotal()

    @staticmethod
    def allocation_budget() -> "_VmErrorOutOfMessageFeeAllocationBudget":
        return _VmErrorOutOfMessageFeeAllocationBudget()


class _VmErrorOutOf:
    @staticmethod
    def storage() -> "VmError":
        return VmError("out_of storage")

    @staticmethod
    def subvm_recursion() -> "VmError":
        return VmError("out_of subvm_recursion")

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


class _VmErrorFeeNoMatchingAllocation:
    @staticmethod
    def val() -> "VmError":
        return VmError("fee no_matching_allocation")

    @staticmethod
    def internal() -> "VmError":
        return VmError("fee no_matching_allocation # internal")

    @staticmethod
    def external() -> "VmError":
        return VmError("fee no_matching_allocation # external")


class _VmErrorFee:
    @staticmethod
    def below_minimum() -> "VmError":
        return VmError("fee below_minimum")

    @staticmethod
    def phase_timeout_out_of_bounds() -> "VmError":
        return VmError("fee phase_timeout_out_of_bounds")

    @staticmethod
    def too_many_rounds() -> "VmError":
        return VmError("fee too_many_rounds")

    @staticmethod
    def no_matching_allocation() -> "_VmErrorFeeNoMatchingAllocation":
        return _VmErrorFeeNoMatchingAllocation()


class _VmErrorEvm:
    @staticmethod
    def reverted() -> "VmError":
        return VmError("evm reverted")


class _VmErrorInvalidContractRunner:
    @staticmethod
    def absent() -> "VmError":
        return VmError("invalid_contract runner absent")

    @staticmethod
    def malformed() -> "VmError":
        return VmError("invalid_contract runner malformed")


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
    def not_utf8_text() -> "VmError":
        return VmError("invalid_contract not_utf8_text")

    @staticmethod
    def major_mismatch() -> "VmError":
        return VmError("invalid_contract major_mismatch")

    @staticmethod
    def runner() -> "_VmErrorInvalidContractRunner":
        return _VmErrorInvalidContractRunner()

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
    def malformed_entry() -> "VmError":
        return VmError("malformed_entry")

    @staticmethod
    def forbidden() -> "VmError":
        return VmError("forbidden")

    @staticmethod
    def leader_fault() -> "_VmErrorLeaderFault":
        return _VmErrorLeaderFault()

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
