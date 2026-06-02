from __future__ import annotations

import base64
import copy
import os
from dataclasses import dataclass, fields
from typing import Any, Callable

import rlp
from eth_abi import decode, encode


VALIDATORS_PER_ROUND = (
    5,
    7,
    11,
    13,
    23,
    25,
    47,
    49,
    95,
    97,
    191,
    193,
    383,
    385,
    767,
    769,
    1535,
    1537,
)

MIN_RECEIPT_BYTES = 512
PROPOSE_RECEIPT_SLOTS = 7
MESSAGE_REVEAL_LENGTH_SLOTS = 32
NONDET_OUTPUT_LENGTH_BYTES = 32
NODE_ROOT_SENTINEL = (1 << 256) - 1
CALL_KEY_WILDCARD = "0x" + ("0" * 64)
MESSAGE_TYPE_EXTERNAL = 0
MESSAGE_TYPE_INTERNAL = 1
FEE_ACCOUNTING_KEY = "fee_accounting"

INTERNAL_MESSAGE_FEE_PARAMS_ABI_TYPE = "(uint256,uint256,uint256,uint256,uint256[])"
EXTERNAL_MESSAGE_FEE_PARAMS_ABI_TYPE = "(uint256,uint256)"
MESSAGE_ALLOCATION_NODE_ABI_TYPE = (
    "(uint8,bool,uint256,address,bytes32,uint256,bytes)[]"
)
SUBMITTED_MESSAGE_ABI_TYPE = (
    "(uint8,address,uint256,bytes,bool,uint256,bytes,uint256,bytes,bytes32)[]"
)

WEI_PER_GEN = 10**18
DEFAULT_GEN_PER_TIME_UNIT = WEI_PER_GEN // 1_000
DEFAULT_STORAGE_UNIT_PRICE = 1
DEFAULT_RECEIPT_GAS_PRICE = 1
DEFAULT_TRANSACTION_EXECUTION_BUDGET_PER_ROUND = 500_000
DEFAULT_LEADER_TIMEUNITS_ALLOCATION = 100
DEFAULT_VALIDATOR_TIMEUNITS_ALLOCATION = 200
DEFAULT_PRICE_CAP_HEADROOM_BPS = 12_000
GENVM_UNMETERED_DATA_FEE_BUCKET = (1 << 256) - 1


class FeeValidationError(ValueError):
    pass


class InvalidNumOfValidators(FeeValidationError):
    pass


class InvalidAppealRounds(FeeValidationError):
    pass


class InsufficientFees(FeeValidationError):
    pass


class BudgetTooLow(FeeValidationError):
    pass


class MaxPriceExceeded(FeeValidationError):
    pass


class MessageAllocationsNotEqualBudget(FeeValidationError):
    pass


class AllocationTreeMalformed(FeeValidationError):
    pass


class AllocationLifecycleBudgetInsufficient(FeeValidationError):
    pass


class AllocationTreeBudgetInconsistent(FeeValidationError):
    pass


class AllocationSubtreeMismatch(FeeValidationError):
    pass


class AllocationDuplicateKey(FeeValidationError):
    pass


class AllocationTreeTooDeep(FeeValidationError):
    pass


class ExternalAllocationInvalid(FeeValidationError):
    pass


class InvalidFeeParams(FeeValidationError):
    pass


class Mode1MessageFeesRequireGenVMPerEmissionSupport(FeeValidationError):
    """GenVM must expose per-emission feeParams/declaredBudget before Mode 1 is safe."""


class InvalidAppealBond(FeeValidationError):
    pass


class MessageDeclaredBudgetInsufficient(FeeValidationError):
    pass


class MessageFeesReportMismatch(FeeValidationError):
    pass


class MessageBudgetExceeded(FeeValidationError):
    pass


def _with_cap_headroom(
    value: int, headroom_bps: int = DEFAULT_PRICE_CAP_HEADROOM_BPS
) -> int:
    if value <= 0:
        return 0
    return (value * headroom_bps + 9_999) // 10_000


def _with_padding(value: int, padding_bps: int) -> int:
    if value <= 0:
        return 0
    return (value * int(padding_bps) + 9_999) // 10_000


class MessageNoMatchingAllocation(FeeValidationError):
    pass


class MessageEmissionPhaseMismatch(FeeValidationError):
    pass


class MessageFeeParamsMismatch(FeeValidationError):
    pass


class TooManyMessages(FeeValidationError):
    pass


@dataclass(frozen=True)
class StudioFeePolicy:
    gen_per_time_unit: int = 0
    storage_unit_price: int = 0
    receipt_gas_price: int = 0
    intrinsic_gas: int = 21_000
    bootloader_overhead: int = 60_000
    gas_per_changed_slot: int = 1_000
    calldata_gas_per_byte: int = 16
    fixed_propose_receipt_gas: int = 210_000
    fixed_message_reveal_gas: int = 100_000
    receipt_wrapper_bytes: int = 1_024
    extra_exec_gas: int = 210_000
    max_allocation_tree_depth: int = 5
    max_messages_per_tx: int = 0

    @classmethod
    def from_env(cls) -> "StudioFeePolicy":
        return cls(
            gen_per_time_unit=_env_int(
                "GENLAYER_STUDIO_GEN_PER_TIME_UNIT", DEFAULT_GEN_PER_TIME_UNIT
            ),
            storage_unit_price=_env_int(
                "GENLAYER_STUDIO_STORAGE_UNIT_PRICE", DEFAULT_STORAGE_UNIT_PRICE
            ),
            receipt_gas_price=_env_int(
                "GENLAYER_STUDIO_RECEIPT_GAS_PRICE", DEFAULT_RECEIPT_GAS_PRICE
            ),
            intrinsic_gas=_env_int("GENLAYER_STUDIO_INTRINSIC_GAS", 21_000),
            bootloader_overhead=_env_int("GENLAYER_STUDIO_BOOTLOADER_OVERHEAD", 60_000),
            gas_per_changed_slot=_env_int(
                "GENLAYER_STUDIO_GAS_PER_CHANGED_SLOT", 1_000
            ),
            calldata_gas_per_byte=_env_int("GENLAYER_STUDIO_CALLDATA_GAS_PER_BYTE", 16),
            fixed_propose_receipt_gas=_env_int(
                "GENLAYER_STUDIO_FIXED_PROPOSE_RECEIPT_GAS", 210_000
            ),
            fixed_message_reveal_gas=_env_int(
                "GENLAYER_STUDIO_FIXED_MESSAGE_REVEAL_GAS", 100_000
            ),
            receipt_wrapper_bytes=_env_int(
                "GENLAYER_STUDIO_RECEIPT_WRAPPER_BYTES", 1_024
            ),
            extra_exec_gas=_env_int("GENLAYER_STUDIO_EXTRA_EXEC_GAS", 210_000),
            max_allocation_tree_depth=_env_int(
                "GENLAYER_STUDIO_MAX_ALLOCATION_TREE_DEPTH", 5
            ),
            max_messages_per_tx=_env_int("GENLAYER_STUDIO_MAX_MESSAGES_PER_TX", 0),
        )

    def estimate_propose_receipt_bytes(self, eq_outputs_length: int) -> int:
        return self.receipt_wrapper_bytes + max(0, int(eq_outputs_length))

    def estimate_propose_receipt_gas(self, receipt_bytes: int) -> int:
        return (
            self.fixed_propose_receipt_gas
            + self.intrinsic_gas
            + self.bootloader_overhead
            + (max(0, int(receipt_bytes)) * self.calldata_gas_per_byte)
            + (PROPOSE_RECEIPT_SLOTS * self.gas_per_changed_slot)
        )

    def estimate_message_reveal_gas(
        self,
        message_bytes: int,
        message_count: int,
    ) -> int:
        return (
            self.fixed_message_reveal_gas
            + self.intrinsic_gas
            + self.bootloader_overhead
            + (max(0, int(message_bytes)) * self.calldata_gas_per_byte)
            + (
                (MESSAGE_REVEAL_LENGTH_SLOTS + max(0, int(message_count)))
                * self.gas_per_changed_slot
            )
        )

    def estimate_consensus_message_reveal_gas(
        self,
        message_bytes: int,
        message_count: int,
    ) -> int:
        return self.estimate_receipt_gas(
            measured_exec_gas=0,
            calldata_length=message_bytes,
            slots_changed=message_count,
        )

    def estimate_receipt_gas(
        self,
        measured_exec_gas: int = 0,
        calldata_length: int = MIN_RECEIPT_BYTES,
        slots_changed: int = 7,
    ) -> int:
        measured = max(0, int(measured_exec_gas))
        if measured > 0:
            measured += self.extra_exec_gas
        return (
            measured
            + self.intrinsic_gas
            + self.bootloader_overhead
            + (max(0, int(calldata_length)) * self.calldata_gas_per_byte)
            + (max(0, int(slots_changed)) * self.gas_per_changed_slot)
        )

    def estimate_nondet_output_start_gas(self) -> int:
        return NONDET_OUTPUT_LENGTH_BYTES * self.calldata_gas_per_byte

    def message_fee_params_budget_floor(self) -> int:
        return self.minimum_execution_budget_per_round()

    def minimum_execution_budget_per_round(self) -> int:
        if self.receipt_gas_price <= 0:
            return 0
        fixed_bucket_gas = self.estimate_receipt_gas(
            measured_exec_gas=0,
            calldata_length=MIN_RECEIPT_BYTES,
            slots_changed=PROPOSE_RECEIPT_SLOTS,
        )
        return fixed_bucket_gas * self.receipt_gas_price

    def fee_accounting_enabled(self) -> bool:
        return (
            self.gen_per_time_unit > 0
            or self.storage_unit_price > 0
            or self.receipt_gas_price > 0
        )

    def to_snapshot(self) -> dict[str, int]:
        return {field.name: int(getattr(self, field.name)) for field in fields(self)}

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, Any]) -> "StudioFeePolicy":
        return cls(**{field.name: int(snapshot[field.name]) for field in fields(cls)})


def _accounting_policy(
    accounting: dict[str, Any] | None,
    override: StudioFeePolicy | None = None,
) -> StudioFeePolicy:
    if override is not None:
        return override
    snapshot = (accounting or {}).get("policy_snapshot")
    if isinstance(snapshot, dict):
        try:
            return StudioFeePolicy.from_snapshot(snapshot)
        except (KeyError, TypeError, ValueError):
            pass
    return StudioFeePolicy()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _int_field(fees_distribution: dict[str, Any], field: str) -> int:
    return int(fees_distribution.get(field, 0) or 0)


def normalize_fees_distribution(
    fees_distribution: dict[str, Any],
) -> dict[str, int | list[int]]:
    return {
        "leaderTimeunitsAllocation": _int_field(
            fees_distribution, "leaderTimeunitsAllocation"
        ),
        "validatorTimeunitsAllocation": _int_field(
            fees_distribution, "validatorTimeunitsAllocation"
        ),
        "appealRounds": _int_field(fees_distribution, "appealRounds"),
        "executionBudgetPerRound": _int_field(
            fees_distribution, "executionBudgetPerRound"
        ),
        "executionConsumed": _int_field(fees_distribution, "executionConsumed"),
        "totalMessageFees": _int_field(fees_distribution, "totalMessageFees"),
        "rotations": [
            int(rotation) for rotation in fees_distribution.get("rotations", [])
        ],
        "maxPriceGenPerTimeUnit": _int_field(
            fees_distribution, "maxPriceGenPerTimeUnit"
        ),
        "storageFeeMaxGasPrice": _int_field(fees_distribution, "storageFeeMaxGasPrice"),
        "receiptFeeMaxGasPrice": _int_field(fees_distribution, "receiptFeeMaxGasPrice"),
    }


def get_leader_rounds(fees_distribution: dict[str, Any]) -> int:
    fees = normalize_fees_distribution(fees_distribution)
    return sum(rotation + 1 for rotation in fees["rotations"]) + int(
        fees["appealRounds"]
    )


def get_leader_rounds_through_round(
    fees_distribution: dict[str, Any], final_round: int
) -> int:
    fees = normalize_fees_distribution(fees_distribution)
    rotations = fees["rotations"]
    if not isinstance(rotations, list) or len(rotations) == 0:
        raise InvalidAppealRounds("InvalidAppealRounds")

    final_round = max(0, int(final_round))
    total = int(rotations[0]) + 1
    rotations_index = 1
    for offset in range(1, min(final_round, int(fees["appealRounds"]) * 2) + 1):
        if offset % 2 == 1:
            total += 1
        elif rotations_index < len(rotations):
            total += int(rotations[rotations_index]) + 1
            rotations_index += 1
    return total


def calculate_time_unit_fees_through_round(
    fees_distribution: dict[str, Any],
    num_of_validators: int,
    final_round: int,
    policy: StudioFeePolicy | None = None,
) -> int:
    fees = normalize_fees_distribution(fees_distribution)
    policy = policy or StudioFeePolicy()
    validator_index = _validator_index(num_of_validators)
    rotations = fees["rotations"]
    if not isinstance(rotations, list) or len(rotations) == 0:
        raise InvalidAppealRounds("InvalidAppealRounds")

    capped_final_round = min(max(0, int(final_round)), int(fees["appealRounds"]) * 2)
    if validator_index + capped_final_round >= len(VALIDATORS_PER_ROUND):
        raise InvalidNumOfValidators("InvalidNumOfValidators")

    leader_timeunits = int(fees["leaderTimeunitsAllocation"])
    validator_timeunits = int(fees["validatorTimeunitsAllocation"])
    total = _calculate_fee_for_round(
        VALIDATORS_PER_ROUND[validator_index],
        int(rotations[0]) + 1,
        leader_timeunits,
        validator_timeunits,
    )
    rotations_index = 1
    for offset in range(1, capped_final_round + 1):
        if offset % 2 == 0 and rotations_index < len(rotations):
            rotations_this_round = int(rotations[rotations_index]) + 1
            rotations_index += 1
        else:
            rotations_this_round = 1
        total += _calculate_fee_for_round(
            VALIDATORS_PER_ROUND[validator_index + offset],
            rotations_this_round,
            leader_timeunits,
            validator_timeunits,
        )

    max_price = int(fees["maxPriceGenPerTimeUnit"])
    if policy.gen_per_time_unit > 0:
        if max_price > 0 and policy.gen_per_time_unit > max_price:
            raise MaxPriceExceeded("MaxPriceExceeded")
        total *= policy.gen_per_time_unit
    return total


def calculate_round_fees(
    fees_distribution: dict[str, Any],
    num_of_validators: int,
    round: int = 0,
    policy: StudioFeePolicy | None = None,
) -> int:
    fees = normalize_fees_distribution(fees_distribution)
    policy = policy or StudioFeePolicy()

    if round == 0:
        validator_index = _validator_index(num_of_validators)
        if int(fees["appealRounds"]) != len(fees["rotations"]) - 1:
            raise InvalidAppealRounds("InvalidAppealRounds")
        total = _calculate_fees(fees, validator_index)
    else:
        if round >= len(VALIDATORS_PER_ROUND):
            raise InvalidNumOfValidators("InvalidNumOfValidators")
        rotations = (
            int(fees["rotations"][round - 1])
            if round - 1 < len(fees["rotations"])
            else 0
        )
        total = _calculate_fee_for_round(
            VALIDATORS_PER_ROUND[round],
            rotations,
            int(fees["leaderTimeunitsAllocation"]),
            int(fees["validatorTimeunitsAllocation"]),
        )

    max_price = int(fees["maxPriceGenPerTimeUnit"])
    if policy.gen_per_time_unit > 0:
        if max_price > 0 and policy.gen_per_time_unit > max_price:
            raise MaxPriceExceeded("MaxPriceExceeded")
        total *= policy.gen_per_time_unit

    storage_fee_max_gas_price = int(fees["storageFeeMaxGasPrice"])
    if (
        storage_fee_max_gas_price > 0
        and policy.storage_unit_price > storage_fee_max_gas_price
    ):
        raise MaxPriceExceeded("MaxPriceExceeded")

    receipt_fee_max_gas_price = int(fees["receiptFeeMaxGasPrice"])
    if (
        receipt_fee_max_gas_price > 0
        and policy.receipt_gas_price > receipt_fee_max_gas_price
    ):
        raise MaxPriceExceeded("MaxPriceExceeded")

    if round == 0:
        total += int(fees["executionBudgetPerRound"]) * get_leader_rounds(fees)

    return total


def required_fee_deposit(
    fees_distribution: dict[str, Any],
    num_of_validators: int,
    policy: StudioFeePolicy | None = None,
) -> int:
    fees = normalize_fees_distribution(fees_distribution)
    return calculate_round_fees(fees, num_of_validators, 0, policy) + int(
        fees["totalMessageFees"]
    )


def default_transaction_fees_for_policy(
    policy: StudioFeePolicy | None = None,
) -> tuple[dict[str, int | list[int]], int]:
    policy = policy or StudioFeePolicy()
    execution_budget_per_round = (
        max(
            DEFAULT_TRANSACTION_EXECUTION_BUDGET_PER_ROUND,
            policy.message_fee_params_budget_floor(),
        )
        if policy.storage_unit_price > 0 or policy.receipt_gas_price > 0
        else 0
    )
    distribution = _serializable_fees_distribution(
        {
            "leaderTimeunitsAllocation": (
                DEFAULT_LEADER_TIMEUNITS_ALLOCATION
                if policy.gen_per_time_unit > 0
                else 0
            ),
            "validatorTimeunitsAllocation": (
                DEFAULT_VALIDATOR_TIMEUNITS_ALLOCATION
                if policy.gen_per_time_unit > 0
                else 0
            ),
            "appealRounds": 0,
            "executionBudgetPerRound": execution_budget_per_round,
            "executionConsumed": 0,
            "totalMessageFees": 0,
            "rotations": [0],
            "maxPriceGenPerTimeUnit": _with_cap_headroom(policy.gen_per_time_unit),
            "storageFeeMaxGasPrice": _with_cap_headroom(policy.storage_unit_price),
            "receiptFeeMaxGasPrice": _with_cap_headroom(policy.receipt_gas_price),
        }
    )
    fee_value = (
        required_fee_deposit(distribution, VALIDATORS_PER_ROUND[0], policy)
        if policy.fee_accounting_enabled()
        else 0
    )
    return distribution, fee_value


def studio_fee_config(policy: StudioFeePolicy | None = None) -> dict[str, Any]:
    policy = policy or StudioFeePolicy.from_env()
    distribution, fee_value = default_transaction_fees_for_policy(policy)
    return {
        "enabled": policy.fee_accounting_enabled(),
        "policy": {
            "genPerTimeUnit": str(policy.gen_per_time_unit),
            "storageUnitPrice": str(policy.storage_unit_price),
            "receiptGasPrice": str(policy.receipt_gas_price),
            "intrinsicGas": str(policy.intrinsic_gas),
            "bootloaderOverhead": str(policy.bootloader_overhead),
            "gasPerChangedSlot": str(policy.gas_per_changed_slot),
            "calldataGasPerByte": str(policy.calldata_gas_per_byte),
            "fixedProposeReceiptGas": str(policy.fixed_propose_receipt_gas),
            "fixedMessageRevealGas": str(policy.fixed_message_reveal_gas),
            "receiptWrapperBytes": str(policy.receipt_wrapper_bytes),
            "extraExecGas": str(policy.extra_exec_gas),
            "messageFeeParamsBudgetFloor": str(
                policy.message_fee_params_budget_floor()
            ),
            "maxAllocationTreeDepth": str(policy.max_allocation_tree_depth),
            "maxMessagesPerTx": str(policy.max_messages_per_tx),
        },
        "capabilities": {
            "messageFees": {
                "mode1": {
                    "accounting": True,
                    "genvmExecution": False,
                },
                "mode2": {
                    "accounting": True,
                    "genvmExecution": True,
                },
                "externalFinalization": {
                    "accounting": True,
                    "genvmExecution": True,
                },
            }
        },
        "defaultFees": {
            "distribution": {
                key: (
                    [str(item) for item in value]
                    if isinstance(value, list)
                    else str(value)
                )
                for key, value in distribution.items()
            },
            "feeValue": str(fee_value),
        },
    }


def validate_transaction_fee_deposit(
    *,
    fees_distribution: dict[str, Any],
    message_allocations: list[dict[str, Any]] | None = None,
    num_of_validators: int,
    submitted_value: int,
    user_value: int,
    policy: StudioFeePolicy | None = None,
) -> int:
    policy = policy or StudioFeePolicy()
    fees = normalize_fees_distribution(fees_distribution)
    execution_budget_per_round = int(fees["executionBudgetPerRound"])
    if (
        execution_budget_per_round > 0
        and execution_budget_per_round < policy.message_fee_params_budget_floor()
    ):
        raise BudgetTooLow("BudgetTooLow")

    if submitted_value < user_value:
        raise InsufficientFees("InsufficientFees")

    required_fee_value = required_fee_deposit(fees, num_of_validators, policy)
    paid_fee_value = submitted_value - user_value
    if paid_fee_value < required_fee_value:
        raise InsufficientFees("InsufficientFees")

    validate_message_allocations(
        message_allocations or [],
        total_message_fees=int(fees["totalMessageFees"]),
        policy=policy,
    )

    return required_fee_value


def create_fee_accounting(
    *,
    fees_distribution: dict[str, Any],
    message_allocations: list[dict[str, Any]] | None = None,
    num_of_validators: int,
    submitted_value: int,
    user_value: int,
    sender: str | None = None,
    policy: StudioFeePolicy | None = None,
) -> dict[str, Any]:
    policy = policy or StudioFeePolicy()
    required = validate_transaction_fee_deposit(
        fees_distribution=fees_distribution,
        message_allocations=message_allocations or [],
        num_of_validators=num_of_validators,
        submitted_value=submitted_value,
        user_value=user_value,
        policy=policy,
    )
    fee_value = max(0, int(submitted_value) - int(user_value))
    return _new_fee_accounting(
        fees_distribution=fees_distribution,
        message_allocations=message_allocations or [],
        num_of_validators=num_of_validators,
        fee_value=fee_value,
        required_fee_value=required,
        user_value=user_value,
        sender=sender,
        source="submission",
        policy=policy,
    )


def create_child_fee_accounting(
    *,
    message: dict[str, Any],
    parent_fees_distribution: dict[str, Any] | None,
    message_allocations: list[dict[str, Any]] | None = None,
    sender: str | None = None,
    policy: StudioFeePolicy | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    policy = policy or StudioFeePolicy()
    declared_budget = int(message.get("declaredBudget", 0) or 0)
    if declared_budget <= 0:
        raise MessageDeclaredBudgetInsufficient("MessageDeclaredBudgetInsufficient")

    fee_params = decode_internal_message_fee_params(message.get("feeParams", b""))
    capless_child_fees = _fees_distribution_from_internal_params(
        fee_params,
        total_message_fees=0,
        parent_fees_distribution=normalize_fees_distribution({}),
    )
    try:
        child_primary = validate_transaction_fee_deposit(
            fees_distribution=capless_child_fees,
            message_allocations=[],
            num_of_validators=VALIDATORS_PER_ROUND[0],
            submitted_value=declared_budget,
            user_value=0,
            policy=policy,
        )
    except InsufficientFees as exc:
        raise MessageDeclaredBudgetInsufficient(
            "MessageDeclaredBudgetInsufficient"
        ) from exc
    if declared_budget < child_primary:
        raise MessageDeclaredBudgetInsufficient("MessageDeclaredBudgetInsufficient")

    parent_fees = (
        normalize_fees_distribution(parent_fees_distribution)
        if parent_fees_distribution
        else normalize_fees_distribution({})
    )
    child_fees = _fees_distribution_from_internal_params(
        fee_params,
        total_message_fees=declared_budget - child_primary,
        parent_fees_distribution=parent_fees,
    )
    child_message_allocations = _child_allocations_from_message_subtree(
        message,
        message_allocations or [],
    )
    validate_message_allocations(
        child_message_allocations,
        total_message_fees=int(child_fees["totalMessageFees"]),
        policy=policy,
    )
    user_value = int(message.get("value", 0) or 0)
    accounting = _new_fee_accounting(
        fees_distribution=child_fees,
        message_allocations=child_message_allocations,
        num_of_validators=VALIDATORS_PER_ROUND[0],
        fee_value=declared_budget,
        required_fee_value=declared_budget,
        user_value=user_value,
        sender=sender,
        source="internal_message",
        policy=policy,
    )
    return child_fees, accounting


def genvm_fee_context(
    accounting: dict[str, Any] | None,
    policy: StudioFeePolicy | None = None,
) -> tuple[list[int] | None, dict[str, str] | None]:
    if not accounting:
        return None, None

    policy = _accounting_policy(accounting, policy)
    fees = normalize_fees_distribution(accounting.get("fees_distribution") or {})
    bucket_total = int(fees["executionBudgetPerRound"])

    gas_data = {
        "storageUnitPrice": str(policy.storage_unit_price),
        "receiptGasPerByte": str(
            policy.receipt_gas_price * policy.calldata_gas_per_byte
        ),
        "gasPerChangedSlot": str(
            policy.receipt_gas_price * policy.gas_per_changed_slot
        ),
        "intrinsicGas": str(policy.receipt_gas_price * policy.intrinsic_gas),
        "bootloaderOverhead": str(
            policy.receipt_gas_price * policy.bootloader_overhead
        ),
        "fixedProposeReceiptGas": str(
            policy.receipt_gas_price * policy.fixed_propose_receipt_gas
        ),
        "fixedMessageRevealGas": str(
            policy.receipt_gas_price * policy.fixed_message_reveal_gas
        ),
        "genPerTimeUnit": str(policy.gen_per_time_unit),
    }
    message_bucket_total = int(accounting.get("message_fee_budget", 0) or 0)
    if bucket_total > 0 or message_bucket_total > 0:
        data_bucket_total = (
            bucket_total if bucket_total > 0 else GENVM_UNMETERED_DATA_FEE_BUCKET
        )
        bucket_totals = [data_bucket_total, data_bucket_total, message_bucket_total]
    else:
        bucket_totals = None
    return bucket_totals, gas_data


def genvm_message_fee_allocation(
    accounting: dict[str, Any] | None,
    *,
    address_factory: Callable[[str], Any] | None = None,
) -> list[dict[str, Any]]:
    if not accounting:
        return _genvm_unmetered_message_fee_allocation()

    if not accounting.get("message_allocations"):
        if int(accounting.get("message_fee_budget", 0) or 0) > 0:
            raise Mode1MessageFeesRequireGenVMPerEmissionSupport(
                "Mode1MessageFeesRequireGenVMPerEmissionSupport: GenVM v0.3.x "
                "message emissions do not carry per-emission feeParams/"
                "declaredBudget without a message allocation tree"
            )
        return []

    nodes: list[dict[str, Any]] = []
    for raw_node in accounting.get("message_allocations") or []:
        node = _serializable_message_allocation(raw_node)
        if int(node["parentIndex"]) != NODE_ROOT_SENTINEL:
            continue
        recipient = str(node["recipient"]).lower()
        call_key = _normalize_call_key(node["callKey"])
        message_type = (
            "External"
            if int(node["messageType"]) == MESSAGE_TYPE_EXTERNAL
            else (
                "InternalAccepted"
                if bool(node["onAcceptance"])
                else "InternalFinalized"
            )
        )
        nodes.append(
            {
                "message_type": message_type,
                "parent_index": (
                    None
                    if int(node["parentIndex"]) == NODE_ROOT_SENTINEL
                    else int(node["parentIndex"])
                ),
                "recipient": (
                    None
                    if recipient == ""
                    else address_factory(recipient) if address_factory else recipient
                ),
                "call_key": (
                    None
                    if call_key == CALL_KEY_WILDCARD
                    else bytes.fromhex(call_key.removeprefix("0x"))
                ),
                "budget": int(node["budget"]),
                "fee_params": _genvm_message_fee_params(node),
            }
        )
    if nodes:
        nodes.append(_genvm_external_legacy_fallback_message_fee_allocation())
    return nodes


def apply_fee_top_up(
    accounting: dict[str, Any],
    *,
    fees_distribution: dict[str, Any],
    amount: int,
    sender: str | None = None,
    num_of_validators: int = VALIDATORS_PER_ROUND[0],
    perform_fee_checks: bool = True,
    policy: StudioFeePolicy | None = None,
) -> dict[str, Any]:
    policy = _accounting_policy(accounting, policy)
    amount = int(amount)
    incoming = normalize_fees_distribution(fees_distribution)
    incoming_message_fees = int(incoming["totalMessageFees"])
    if incoming_message_fees > amount:
        raise InsufficientFees("InsufficientFees")

    primary_amount = amount - incoming_message_fees
    if perform_fee_checks:
        required_primary = calculate_round_fees(incoming, num_of_validators, 0, policy)
        if required_primary > primary_amount:
            raise InsufficientFees("InsufficientFeesForRound")

    updated = copy.deepcopy(accounting)
    merged = merge_fees_distribution(updated.get("fees_distribution") or {}, incoming)
    if (
        int(merged["executionBudgetPerRound"]) > 0
        and int(merged["executionBudgetPerRound"])
        < policy.message_fee_params_budget_floor()
    ):
        raise BudgetTooLow("BudgetTooLow")

    updated["fees_distribution"] = merged
    updated["paid_fee_value"] = int(updated.get("paid_fee_value", 0)) + amount
    updated["primary_fee_budget"] = (
        int(updated.get("primary_fee_budget", 0)) + primary_amount
    )
    updated["message_fee_budget"] = (
        int(updated.get("message_fee_budget", 0)) + incoming_message_fees
    )
    updated["execution_budget_total"] = int(merged["executionBudgetPerRound"]) * (
        get_leader_rounds(merged)
    )
    updated.setdefault("top_ups", []).append(
        {
            "sender": sender,
            "amount": amount,
            "primaryAmount": primary_amount,
            "messageFees": incoming_message_fees,
            "feesDistribution": _serializable_fees_distribution(incoming),
        }
    )
    _refresh_message_fee_accounting_report_if_present(updated, policy)
    return updated


def record_appeal_bond(
    accounting: dict[str, Any],
    *,
    amount: int,
    appealer: str | None,
    current_round: int = 0,
    status: str | None = None,
    round: int | None = None,
    fees_distribution: dict[str, Any] | None = None,
    top_up_and_submit: bool = False,
    policy: StudioFeePolicy | None = None,
) -> dict[str, Any]:
    updated = copy.deepcopy(accounting)
    policy = _accounting_policy(updated, policy)
    amount = int(amount)

    min_required = 0
    if status is not None:
        min_required = calculate_min_appeal_bond(
            updated.get("fees_distribution") or {},
            current_round=current_round,
            status=status,
            policy=policy,
        )
        if amount < min_required:
            raise InvalidAppealBond("InvalidAppealBond")

    if top_up_and_submit:
        updated["primary_fee_budget"] = (
            int(updated.get("primary_fee_budget", 0)) + amount
        )
        updated["paid_fee_value"] = int(updated.get("paid_fee_value", 0)) + amount
        merged = normalize_fees_distribution(updated.get("fees_distribution") or {})
        merged["appealRounds"] = int(merged["appealRounds"]) + 1
        updated["fees_distribution"] = merged
        updated["execution_budget_total"] = int(
            merged["executionBudgetPerRound"]
        ) * get_leader_rounds(merged)

    updated["appeal_bonds_total"] = int(updated.get("appeal_bonds_total", 0)) + amount
    updated.setdefault("appeal_bonds", []).append(
        {
            "appealer": appealer,
            "amount": amount,
            "round": current_round if round is None else round,
            "status": status,
            "minimumRequired": min_required,
            "topUpAndSubmit": bool(top_up_and_submit),
            "feesDistributionIgnored": fees_distribution is not None
            and top_up_and_submit,
        }
    )
    _refresh_message_fee_accounting_report_if_present(updated, policy)
    return updated


def calculate_min_appeal_bond(
    fees_distribution: dict[str, Any],
    *,
    current_round: int,
    status: str,
    policy: StudioFeePolicy | None = None,
) -> int:
    policy = policy or StudioFeePolicy()
    fees = normalize_fees_distribution(fees_distribution)
    current_round = max(0, int(current_round))
    status_value = str(status).upper()
    if status_value in {"LEADER_TIMEOUT", "UNDETERMINED"}:
        target_round = current_round + 2
        if target_round >= len(VALIDATORS_PER_ROUND):
            raise InvalidNumOfValidators("InvalidNumOfValidators")
        rotations = (
            int(fees["rotations"][target_round - 1])
            if target_round - 1 < len(fees["rotations"])
            else 0
        )
        total = _calculate_fee_for_round(
            VALIDATORS_PER_ROUND[target_round],
            rotations,
            int(fees["leaderTimeunitsAllocation"]),
            int(fees["validatorTimeunitsAllocation"]),
        )
        return (
            total * policy.gen_per_time_unit if policy.gen_per_time_unit > 0 else total
        )

    if status_value in {"VALIDATORS_TIMEOUT", "ACCEPTED"}:
        target_round = current_round + 1
        if target_round >= len(VALIDATORS_PER_ROUND):
            raise InvalidNumOfValidators("InvalidNumOfValidators")
        total = VALIDATORS_PER_ROUND[target_round] * int(
            fees["validatorTimeunitsAllocation"]
        )
        return (
            total * policy.gen_per_time_unit if policy.gen_per_time_unit > 0 else total
        )

    return 0


def fill_message_fee_payload_from_allocation(
    accounting: dict[str, Any],
    message: dict[str, Any],
) -> dict[str, Any]:
    if int(message.get("messageType", MESSAGE_TYPE_INTERNAL)) != MESSAGE_TYPE_INTERNAL:
        return copy.deepcopy(message)

    allocations = accounting.get("message_allocations") or []
    if not allocations:
        return copy.deepcopy(message)

    resolved = _resolve_allocation(allocations, message)
    if resolved is None:
        raise MessageNoMatchingAllocation("MessageNoMatchingAllocation")

    index, allocation = resolved
    if bool(allocation["onAcceptance"]) != bool(message.get("onAcceptance", False)):
        raise MessageEmissionPhaseMismatch("MessageEmissionPhaseMismatch")

    updated = copy.deepcopy(message)
    if int(updated.get("declaredBudget", 0) or 0) == 0:
        updated["declaredBudget"] = int(allocation["budget"])
    if not _message_has_fee_params(updated):
        updated["feeParams"] = allocation["feeParams"]
    updated["callKey"] = _normalize_call_key(
        updated.get("callKey", allocation["callKey"])
    )
    expected_subtree = _allocation_subtree(allocations, index)
    if not updated.get("allocationSubtree"):
        updated["allocationSubtree"] = expected_subtree
    elif (
        _canonical_allocation_subtree(updated["allocationSubtree"]) != expected_subtree
    ):
        raise AllocationSubtreeMismatch("AllocationSubtreeMismatch")
    updated["messageFeeMode"] = "mode2"
    return updated


def consume_message_fees(
    accounting: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    reported_total: int | None = None,
    policy: StudioFeePolicy | None = None,
    reimburse_external: bool = True,
) -> dict[str, Any]:
    policy = _accounting_policy(accounting, policy)
    if policy.max_messages_per_tx > 0 and len(messages) > policy.max_messages_per_tx:
        raise TooManyMessages("TooManyMessages")

    updated = copy.deepcopy(accounting)
    recalculated_total = 0
    external_reimbursement_total = 0

    for message in messages:
        if (
            int(message.get("messageType", MESSAGE_TYPE_INTERNAL))
            == MESSAGE_TYPE_EXTERNAL
        ):
            if int(message.get("declaredBudget", 0) or 0) != 0:
                raise MessageDeclaredBudgetInsufficient(
                    "MessageDeclaredBudgetInsufficient"
                )
            external_reimbursement_total += _reserve_external_execution(
                updated, message, policy, reimburse=reimburse_external
            )
            continue

        if (
            int(message.get("messageType", MESSAGE_TYPE_INTERNAL))
            != MESSAGE_TYPE_INTERNAL
        ):
            continue

        declared_budget = int(message.get("declaredBudget", 0) or 0)
        fee_params = decode_internal_message_fee_params(message.get("feeParams", b""))
        if (
            int(fee_params["executionBudgetPerRound"]) > 0
            and int(fee_params["executionBudgetPerRound"])
            < policy.message_fee_params_budget_floor()
        ):
            raise BudgetTooLow("BudgetTooLow")
        min_required = min_message_primary_fees(fee_params, policy)
        if declared_budget < min_required:
            raise MessageDeclaredBudgetInsufficient("MessageDeclaredBudgetInsufficient")
        recalculated_total += declared_budget
        _consume_against_allocation(updated, message, declared_budget)

    if reported_total is not None and int(reported_total) < recalculated_total:
        raise MessageFeesReportMismatch("MessageFeesReportMismatch")

    attempted = (
        int(updated.get("message_fee_consumed", 0))
        + recalculated_total
        + external_reimbursement_total
    )
    message_budget = int(updated.get("message_fee_budget", 0))
    if attempted > message_budget:
        raise MessageBudgetExceeded("MessageBudgetExceeded")

    updated["message_fee_consumed"] = attempted
    updated.setdefault("message_consumption_events", []).append(
        {
            "consumed": recalculated_total + external_reimbursement_total,
            "internalConsumed": recalculated_total,
            "externalReimbursed": external_reimbursement_total,
            "remaining": message_budget - attempted,
        }
    )
    _refresh_message_fee_accounting_report_if_present(updated, policy)
    return updated


def record_reveal_message_fees(
    accounting: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    reported_total: int | None = None,
    policy: StudioFeePolicy | None = None,
) -> dict[str, Any]:
    updated = consume_message_fees(
        accounting,
        messages,
        reported_total=reported_total,
        policy=policy,
        reimburse_external=False,
    )
    updated["message_fees_recorded_at_reveal"] = True
    return updated


def record_external_message_execution_fees(
    accounting: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    policy: StudioFeePolicy | None = None,
) -> dict[str, Any]:
    updated = copy.deepcopy(accounting)
    policy = _accounting_policy(updated, policy)
    reimbursement_total = 0
    remainder_total = 0
    updated_any = False

    for message in messages:
        if (
            int(message.get("messageType", MESSAGE_TYPE_INTERNAL))
            != MESSAGE_TYPE_EXTERNAL
        ):
            continue

        event_index = _find_unexecuted_external_message_event(updated, message)
        if event_index is None:
            continue

        event = updated.setdefault("external_message_events", [])[event_index]
        reservation = int(event.get("reservation", 0) or 0)
        gas_limit = int(event.get("gasLimit", 0) or 0)
        locked_price = int(event.get("lockedGasPrice", 0) or 0)
        gas_used = int(message.get("gasUsed", 0) or 0)
        effective_gas = min(gas_limit, gas_used)
        reimbursement = min(reservation, effective_gas * locked_price)
        remainder = reservation - reimbursement

        attempted = (
            int(updated.get("message_fee_consumed", 0))
            + reimbursement_total
            + reimbursement
        )
        message_budget = int(updated.get("message_fee_budget", 0))
        if attempted > message_budget:
            raise MessageBudgetExceeded("MessageBudgetExceeded")

        event["gasUsed"] = gas_used
        event["reimbursement"] = reimbursement
        event["remainder"] = remainder
        event["executionRecorded"] = True
        reimbursement_total += reimbursement
        remainder_total += remainder
        updated_any = True

    if updated_any:
        updated["message_fee_consumed"] = (
            int(updated.get("message_fee_consumed", 0)) + reimbursement_total
        )
        updated["external_message_fee_reimbursed"] = (
            int(updated.get("external_message_fee_reimbursed", 0)) + reimbursement_total
        )
        updated["external_message_fee_remainder"] = (
            int(updated.get("external_message_fee_remainder", 0)) + remainder_total
        )
        updated.setdefault("message_consumption_events", []).append(
            {
                "consumed": reimbursement_total,
                "internalConsumed": 0,
                "externalReimbursed": reimbursement_total,
                "remaining": max(
                    0,
                    int(updated.get("message_fee_budget", 0))
                    - int(updated.get("message_fee_consumed", 0)),
                ),
            }
        )
        _refresh_message_fee_accounting_report_if_present(updated, policy)

    return updated


def refund_failed_external_message_fee(
    accounting: dict[str, Any],
    message: dict[str, Any],
) -> dict[str, Any]:
    if int(message.get("messageType", MESSAGE_TYPE_INTERNAL)) != MESSAGE_TYPE_EXTERNAL:
        return copy.deepcopy(accounting)

    updated = copy.deepcopy(accounting)
    event_index = _find_unrefunded_external_message_event(updated, message)
    if event_index is None:
        return updated

    event = updated.setdefault("external_message_events", [])[event_index]
    reservation = int(event.get("reservation", 0) or 0)
    reimbursement = int(event.get("reimbursement", 0) or 0)
    remainder = int(event.get("remainder", 0) or 0)

    # Execution-level failures still spent gas. Consensus reimburses the
    # executor and leaves the external execution reservation consumed; only the
    # external message value leg is refunded outside this fee-accounting helper.
    event["failureRefunded"] = True
    updated.setdefault("external_message_refund_events", []).append(
        {
            "recipient": event.get("recipient"),
            "callKey": event.get("callKey"),
            "allocationIndex": int(event.get("allocationIndex", 0) or 0),
            "reservation": reservation,
            "reimbursement": reimbursement,
            "remainder": remainder,
            "feeRefunded": 0,
        }
    )
    _refresh_message_fee_accounting_report_if_present(updated)
    return updated


def unwind_reveal_message_fees(
    accounting: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    acceptance_dispatched: bool = False,
) -> dict[str, Any]:
    updated = copy.deepcopy(accounting)
    internal_refund = 0
    external_unreserved = 0
    external_reimbursement_rolled_back = 0
    external_remainder_rolled_back = 0

    for message in messages:
        if (
            int(message.get("messageType", MESSAGE_TYPE_INTERNAL))
            == MESSAGE_TYPE_EXTERNAL
        ):
            (
                reservation,
                reimbursement,
                remainder,
            ) = _unreserve_external_message_fee(updated, message)
            external_unreserved += reservation
            external_reimbursement_rolled_back += reimbursement
            external_remainder_rolled_back += remainder
            continue

        if (
            int(message.get("messageType", MESSAGE_TYPE_INTERNAL))
            != MESSAGE_TYPE_INTERNAL
        ):
            continue
        if acceptance_dispatched and bool(message.get("onAcceptance", False)):
            continue

        declared_budget = int(message.get("declaredBudget", 0) or 0)
        if declared_budget <= 0:
            continue
        internal_refund += declared_budget
        _decrement_allocation_consumed(updated, message, declared_budget)

    if internal_refund > 0:
        updated["message_fee_consumed"] = max(
            0,
            int(updated.get("message_fee_consumed", 0)) - internal_refund,
        )

    if (
        internal_refund > 0
        or external_unreserved > 0
        or external_reimbursement_rolled_back > 0
    ):
        updated.setdefault("message_fee_unwind_events", []).append(
            {
                "acceptanceDispatched": bool(acceptance_dispatched),
                "internalRefunded": internal_refund,
                "externalUnreserved": external_unreserved,
                "externalReimbursementRolledBack": (external_reimbursement_rolled_back),
                "externalRemainderRolledBack": external_remainder_rolled_back,
                "remaining": max(
                    0,
                    int(updated.get("message_fee_budget", 0))
                    - int(updated.get("message_fee_consumed", 0))
                    - int(updated.get("message_fee_refunded", 0)),
                ),
            }
        )

    # A re-reveal replaces or discards the previous message set. Keep the
    # aggregate unwind event, but reopen receipt-based message consumption.
    updated.pop("message_fees_recorded_from_receipt", None)
    updated["message_consumption_events"] = []
    _refresh_message_fee_accounting_report_if_present(updated)
    return updated


def record_execution_fee_consumption(
    accounting: dict[str, Any],
    receipt: Any | None,
    policy: StudioFeePolicy | None = None,
) -> dict[str, Any]:
    updated = copy.deepcopy(accounting)
    policy = _accounting_policy(updated, policy)
    message_payloads = _receipt_message_fee_payloads(updated, receipt)
    reported_message_fees_total = _receipt_reported_message_fees_total(receipt)
    if (
        message_payloads
        and _receipt_messages_require_fee_validation(updated, message_payloads)
        and not updated.get("message_fees_recorded_from_receipt")
        and not updated.get("message_consumption_events")
    ):
        updated = consume_message_fees(
            updated,
            message_payloads,
            reported_total=reported_message_fees_total,
            policy=policy,
        )
        updated["message_fees_recorded_from_receipt"] = True
        if reported_message_fees_total is not None:
            updated["reported_message_fees_total"] = reported_message_fees_total

    fee_report = _receipt_fee_report(receipt, policy, message_payloads)
    if fee_report is not None:
        updated["execution_fee_report"] = fee_report
        _attach_message_fee_accounting_report(updated)
        _attach_recommended_fee_preset(updated, policy)
    consumed = _receipt_data_fees_consumed(receipt)
    if consumed is None:
        return updated
    updated["genvm_fee_consumed_buckets"] = consumed
    bucket_report = _genvm_fee_bucket_report(
        consumed,
        execution_budget_per_round=_execution_budget_per_round(updated),
    )
    execution_consumed = _chargeable_execution_fee_buckets(
        consumed,
        fee_report,
        policy,
        receipt,
    )
    execution_bucket_report = _genvm_fee_bucket_report(
        execution_consumed,
        execution_budget_per_round=_execution_budget_per_round(updated),
    )
    updated["execution_fee_consumed"] = sum(execution_consumed)
    updated["execution_fee_consumed_buckets"] = execution_consumed
    updated["genvm_fee_bucket_report"] = bucket_report
    execution_metering_report = _execution_metering_report(
        chargeable_bucket_report=execution_bucket_report,
        genvm_bucket_report=bucket_report,
    )
    updated["execution_fee_report"] = {
        **(updated.get("execution_fee_report") or {}),
        "genvmBuckets": bucket_report,
        "chargeableExecution": execution_bucket_report,
        "executionMetering": execution_metering_report,
    }
    budget_exhaustion_reason = _receipt_budget_exhaustion_reason(
        receipt,
        execution_bucket_report,
    )
    if budget_exhaustion_reason is not None:
        updated["execution_fee_report"][
            "budgetExhaustionReason"
        ] = budget_exhaustion_reason
    if len(consumed) > 2:
        updated["genvm_message_fee_consumed"] = int(consumed[2])
    _attach_message_fee_accounting_report(updated)
    _attach_recommended_fee_preset(updated, policy)
    return updated


def settle_fee_accounting(
    accounting: dict[str, Any],
    *,
    receipt: Any | None = None,
    reason: str = "finalized",
    actual_final_round: int | None = None,
    num_of_validators: int | None = None,
    policy: StudioFeePolicy | None = None,
) -> tuple[dict[str, Any], int]:
    policy = _accounting_policy(accounting, policy)
    updated = record_execution_fee_consumption(accounting, receipt, policy)
    if updated.get("status") in {"settled", "canceled"}:
        return updated, 0

    primary_budget = int(updated.get("primary_fee_budget", 0))
    execution_budget = int(updated.get("execution_budget_total", 0))
    primary_required = int(updated.get("primary_fee_required", 0))
    fees_distribution = updated.get("fees_distribution") or {}
    if actual_final_round is not None:
        validators = int(
            num_of_validators or updated.get("num_of_initial_validators") or 0
        )
        time_unit_budget = calculate_time_unit_fees_through_round(
            fees_distribution,
            validators,
            actual_final_round,
            policy,
        )
        execution_budget = int(
            normalize_fees_distribution(fees_distribution)["executionBudgetPerRound"]
        ) * get_leader_rounds_through_round(fees_distribution, actual_final_round)
        updated["actual_final_round"] = int(actual_final_round)
    else:
        time_unit_budget = max(0, primary_required - execution_budget)
    execution_spent = min(
        int(updated.get("execution_fee_consumed", 0)), execution_budget
    )
    primary_spent = min(primary_budget, time_unit_budget + execution_spent)
    primary_refund = max(
        0, primary_budget - primary_spent - int(updated.get("primary_fee_refunded", 0))
    )

    message_refund = max(
        0,
        int(updated.get("message_fee_budget", 0))
        - int(updated.get("message_fee_consumed", 0))
        - int(updated.get("message_fee_refunded", 0)),
    )
    refund = primary_refund + message_refund

    updated["status"] = "settled"
    updated["settlement_reason"] = reason
    updated["primary_fee_spent"] = primary_spent
    updated["primary_fee_refunded"] = (
        int(updated.get("primary_fee_refunded", 0)) + primary_refund
    )
    updated["message_fee_refunded"] = (
        int(updated.get("message_fee_refunded", 0)) + message_refund
    )
    updated["total_refunded"] = int(updated.get("total_refunded", 0)) + refund
    updated.setdefault("refunds", []).append(
        {
            "reason": reason,
            "primary": primary_refund,
            "message": message_refund,
            "amount": refund,
        }
    )
    _refresh_message_fee_accounting_report_if_present(updated, policy)
    return updated, refund


def cancel_fee_accounting(
    accounting: dict[str, Any],
    *,
    reason: str = "canceled",
) -> tuple[dict[str, Any], int]:
    updated = copy.deepcopy(accounting)
    if updated.get("status") in {"settled", "canceled"}:
        return updated, 0

    primary_refund = max(
        0,
        int(updated.get("primary_fee_budget", 0))
        - int(updated.get("primary_fee_spent", 0))
        - int(updated.get("primary_fee_refunded", 0)),
    )
    message_refund = max(
        0,
        int(updated.get("message_fee_budget", 0))
        - int(updated.get("message_fee_consumed", 0))
        - int(updated.get("message_fee_refunded", 0)),
    )
    refund = primary_refund + message_refund
    updated["status"] = "canceled"
    updated["settlement_reason"] = reason
    updated["primary_fee_refunded"] = (
        int(updated.get("primary_fee_refunded", 0)) + primary_refund
    )
    updated["message_fee_refunded"] = (
        int(updated.get("message_fee_refunded", 0)) + message_refund
    )
    updated["total_refunded"] = int(updated.get("total_refunded", 0)) + refund
    updated.setdefault("refunds", []).append(
        {
            "reason": reason,
            "primary": primary_refund,
            "message": message_refund,
            "amount": refund,
        }
    )
    _refresh_message_fee_accounting_report_if_present(updated)
    return updated, refund


def merge_fees_distribution(
    current: dict[str, Any], incoming: dict[str, Any]
) -> dict[str, Any]:
    current_fees = normalize_fees_distribution(current)
    incoming_fees = normalize_fees_distribution(incoming)
    is_initial = len(current_fees["rotations"]) == 0
    merged = dict(current_fees)
    if is_initial:
        merged["leaderTimeunitsAllocation"] = incoming_fees["leaderTimeunitsAllocation"]
        merged["validatorTimeunitsAllocation"] = incoming_fees[
            "validatorTimeunitsAllocation"
        ]
        merged["appealRounds"] = incoming_fees["appealRounds"]

    merged["executionBudgetPerRound"] = int(merged["executionBudgetPerRound"]) + int(
        incoming_fees["executionBudgetPerRound"]
    )
    merged["totalMessageFees"] = int(merged["totalMessageFees"]) + int(
        incoming_fees["totalMessageFees"]
    )
    merged["rotations"] = list(merged["rotations"]) + list(incoming_fees["rotations"])
    for cap in (
        "maxPriceGenPerTimeUnit",
        "storageFeeMaxGasPrice",
        "receiptFeeMaxGasPrice",
    ):
        incoming_cap = int(incoming_fees[cap])
        if incoming_cap > 0 and (
            is_initial or (int(merged[cap]) > 0 and incoming_cap > int(merged[cap]))
        ):
            merged[cap] = incoming_cap
    return _serializable_fees_distribution(merged)


def validate_message_allocations(
    message_allocations: list[dict[str, Any]],
    *,
    total_message_fees: int,
    policy: StudioFeePolicy | None = None,
) -> None:
    if not message_allocations:
        return

    policy = policy or StudioFeePolicy()
    root_sum = 0
    root_keys: set[tuple[int, str, str]] = set()
    external_keys: set[tuple[str, str]] = set()
    min_required_by_index: dict[int, int] = {}

    for index, raw_node in enumerate(message_allocations):
        node = _normalize_message_allocation(raw_node)
        parent_index = int(node["parentIndex"])
        if parent_index != NODE_ROOT_SENTINEL and parent_index >= index:
            raise AllocationTreeMalformed("AllocationTreeMalformed")
        if parent_index != NODE_ROOT_SENTINEL:
            parent_node = _normalize_message_allocation(
                message_allocations[parent_index]
            )
            if int(parent_node["messageType"]) == MESSAGE_TYPE_EXTERNAL:
                raise AllocationTreeMalformed("AllocationTreeMalformed")

        if int(node["messageType"]) == MESSAGE_TYPE_EXTERNAL:
            _validate_external_allocation(node, external_keys)
            root_sum += int(node["budget"])
            continue

        if int(node["messageType"]) != MESSAGE_TYPE_INTERNAL:
            raise AllocationTreeMalformed("AllocationTreeMalformed")

        internal_fee_params = decode_internal_message_fee_params(node["feeParams"])
        min_primary = min_message_primary_fees(internal_fee_params, policy)
        lifecycle_multiplier = (
            int(internal_fee_params["appealRounds"]) + 1
            if bool(node["onAcceptance"])
            else 1
        )
        min_required = min_primary * lifecycle_multiplier
        min_required_by_index[index] = min_required
        if int(node["budget"]) < min_required:
            raise AllocationLifecycleBudgetInsufficient(
                "AllocationLifecycleBudgetInsufficient"
            )

        execution_budget_per_round = int(internal_fee_params["executionBudgetPerRound"])
        if (
            execution_budget_per_round > 0
            and execution_budget_per_round < policy.message_fee_params_budget_floor()
        ):
            raise BudgetTooLow("BudgetTooLow")

        if parent_index == NODE_ROOT_SENTINEL:
            key = _allocation_key(node)
            if key in root_keys:
                raise AllocationDuplicateKey("AllocationDuplicateKey")
            root_keys.add(key)
            root_sum += int(node["budget"])

    if root_sum != total_message_fees:
        raise MessageAllocationsNotEqualBudget("MessageAllocationsNotEqualBudget")

    for index, raw_node in enumerate(message_allocations):
        node = _normalize_message_allocation(raw_node)
        if int(node["messageType"]) == MESSAGE_TYPE_EXTERNAL:
            continue
        child_sum = sum(
            int(_normalize_message_allocation(child)["budget"])
            for child in message_allocations[index + 1 :]
            if int(_normalize_message_allocation(child)["parentIndex"]) == index
        )
        if int(node["budget"]) < min_required_by_index[index] + child_sum:
            raise AllocationTreeBudgetInconsistent("AllocationTreeBudgetInconsistent")

    _validate_allocation_tree_depth(message_allocations, policy)
    _validate_sibling_duplicates(message_allocations)


def decode_internal_message_fee_params(fee_params: bytes | str) -> dict[str, Any]:
    raw_fee_params = _fee_params_bytes(fee_params)
    try:
        decoded = decode([INTERNAL_MESSAGE_FEE_PARAMS_ABI_TYPE], raw_fee_params)[0]
    except Exception as exc:
        raise InvalidFeeParams("InvalidFeeParams") from exc
    return {
        "leaderTimeunitsAllocation": int(decoded[0]),
        "validatorTimeunitsAllocation": int(decoded[1]),
        "appealRounds": int(decoded[2]),
        "executionBudgetPerRound": int(decoded[3]),
        "rotations": [int(rotation) for rotation in decoded[4]],
    }


def decode_external_message_fee_params(fee_params: bytes | str) -> dict[str, int]:
    raw_fee_params = _fee_params_bytes(fee_params)
    try:
        decoded = decode([EXTERNAL_MESSAGE_FEE_PARAMS_ABI_TYPE], raw_fee_params)[0]
    except Exception as exc:
        raise InvalidFeeParams("InvalidFeeParams") from exc
    return {
        "gasLimit": int(decoded[0]),
        "maxGasPrice": int(decoded[1]),
    }


def min_message_primary_fees(
    internal_fee_params: dict[str, Any],
    policy: StudioFeePolicy | None = None,
) -> int:
    return calculate_round_fees(
        {
            "leaderTimeunitsAllocation": int(
                internal_fee_params["leaderTimeunitsAllocation"]
            ),
            "validatorTimeunitsAllocation": int(
                internal_fee_params["validatorTimeunitsAllocation"]
            ),
            "appealRounds": int(internal_fee_params["appealRounds"]),
            "executionBudgetPerRound": int(
                internal_fee_params["executionBudgetPerRound"]
            ),
            "executionConsumed": 0,
            "totalMessageFees": 0,
            "rotations": internal_fee_params["rotations"],
            "maxPriceGenPerTimeUnit": 0,
            "storageFeeMaxGasPrice": 0,
            "receiptFeeMaxGasPrice": 0,
        },
        VALIDATORS_PER_ROUND[0],
        0,
        policy,
    )


def _validator_index(num_of_validators: int) -> int:
    if num_of_validators > VALIDATORS_PER_ROUND[-1]:
        raise InvalidNumOfValidators("InvalidNumOfValidators")
    for index, validators in enumerate(VALIDATORS_PER_ROUND):
        if validators >= num_of_validators:
            if validators != num_of_validators:
                raise InvalidNumOfValidators("InvalidNumOfValidators")
            return index
    raise InvalidNumOfValidators("InvalidNumOfValidators")


def _calculate_fees(
    fees_distribution: dict[str, int | list[int]], validator_index: int
) -> int:
    rotations = fees_distribution["rotations"]
    if not isinstance(rotations, list) or len(rotations) == 0:
        raise InvalidAppealRounds("InvalidAppealRounds")

    leader_timeunits = int(fees_distribution["leaderTimeunitsAllocation"])
    validator_timeunits = int(fees_distribution["validatorTimeunitsAllocation"])
    calculated_fees = _calculate_fee_for_round(
        VALIDATORS_PER_ROUND[validator_index],
        int(rotations[0]) + 1,
        leader_timeunits,
        validator_timeunits,
    )

    rotations_index = 1
    rotations_this_round = 1
    appeal_rounds = int(fees_distribution["appealRounds"])
    if validator_index + (appeal_rounds * 2) >= len(VALIDATORS_PER_ROUND):
        raise InvalidNumOfValidators("InvalidNumOfValidators")
    for offset in range(1, (appeal_rounds * 2) + 1):
        round_validators = VALIDATORS_PER_ROUND[validator_index + offset]
        if offset % 2 == 0 and rotations_index < len(rotations):
            rotations_this_round = int(rotations[rotations_index]) + 1
            rotations_index += 1
        elif offset % 2 == 1:
            rotations_this_round = 1

        calculated_fees += _calculate_fee_for_round(
            round_validators,
            rotations_this_round,
            leader_timeunits,
            validator_timeunits,
        )

    return calculated_fees


def _calculate_fee_for_round(
    num_of_validators: int,
    rotations: int,
    leader_timeunits_allocation: int,
    validator_timeunits_allocation: int,
) -> int:
    return rotations * (
        leader_timeunits_allocation
        + (num_of_validators * validator_timeunits_allocation)
    )


def _normalize_message_allocation(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "messageType": int(node.get("messageType", 0)),
        "onAcceptance": bool(node.get("onAcceptance", False)),
        "parentIndex": int(node.get("parentIndex", 0)),
        "recipient": str(node.get("recipient", "")).lower(),
        "callKey": _normalize_call_key(node.get("callKey", CALL_KEY_WILDCARD)),
        "budget": int(node.get("budget", 0)),
        "feeParams": node.get("feeParams", b""),
    }


def _validate_external_allocation(
    node: dict[str, Any],
    external_keys: set[tuple[str, str]],
) -> None:
    if int(node["parentIndex"]) != NODE_ROOT_SENTINEL:
        raise AllocationTreeMalformed("AllocationTreeMalformed")

    external_fee_params = decode_external_message_fee_params(node["feeParams"])
    gas_limit = int(external_fee_params["gasLimit"])
    max_gas_price = int(external_fee_params["maxGasPrice"])
    if gas_limit == 0 or max_gas_price == 0:
        raise ExternalAllocationInvalid("ExternalAllocationInvalid")

    per_call = gas_limit * max_gas_price
    budget = int(node["budget"])
    if budget == 0 or budget % per_call != 0:
        raise ExternalAllocationInvalid("ExternalAllocationInvalid")

    external_key = (str(node["recipient"]).lower(), str(node["callKey"]).lower())
    if external_key in external_keys:
        raise ExternalAllocationInvalid("ExternalAllocationInvalid")
    external_keys.add(external_key)


def _validate_allocation_tree_depth(
    message_allocations: list[dict[str, Any]],
    policy: StudioFeePolicy,
) -> None:
    depth: list[int] = []
    cap = policy.max_allocation_tree_depth or 5
    for index, raw_node in enumerate(message_allocations):
        node = _normalize_message_allocation(raw_node)
        if int(node["messageType"]) == MESSAGE_TYPE_EXTERNAL:
            depth.append(1)
            continue
        parent_index = int(node["parentIndex"])
        current_depth = (
            1 if parent_index == NODE_ROOT_SENTINEL else depth[parent_index] + 1
        )
        if current_depth > cap:
            raise AllocationTreeTooDeep("AllocationTreeTooDeep")
        depth.append(current_depth)


def _validate_sibling_duplicates(message_allocations: list[dict[str, Any]]) -> None:
    sibling_keys: set[tuple[int, int, str, str]] = set()
    for raw_node in message_allocations:
        node = _normalize_message_allocation(raw_node)
        parent_index = int(node["parentIndex"])
        if parent_index == NODE_ROOT_SENTINEL:
            continue
        key = (parent_index, *_allocation_key(node))
        if key in sibling_keys:
            raise AllocationDuplicateKey("AllocationDuplicateKey")
        sibling_keys.add(key)


def _allocation_key(node: dict[str, Any]) -> tuple[int, str, str]:
    return (
        int(node["messageType"]),
        str(node["recipient"]).lower(),
        str(node["callKey"]).lower(),
    )


def _fee_params_bytes(fee_params: bytes | str) -> bytes:
    if isinstance(fee_params, str):
        return bytes.fromhex(fee_params.removeprefix("0x"))
    return bytes(fee_params)


def _new_fee_accounting(
    *,
    fees_distribution: dict[str, Any],
    message_allocations: list[dict[str, Any]],
    num_of_validators: int,
    fee_value: int,
    required_fee_value: int,
    user_value: int,
    sender: str | None,
    source: str,
    policy: StudioFeePolicy,
) -> dict[str, Any]:
    fees = _serializable_fees_distribution(fees_distribution)
    total_message_fees = int(fees["totalMessageFees"])
    execution_budget_total = int(fees["executionBudgetPerRound"]) * get_leader_rounds(
        fees
    )
    primary_required = max(0, int(required_fee_value) - total_message_fees)
    return {
        "version": 1,
        "source": source,
        "status": "active",
        "policy_snapshot": policy.to_snapshot(),
        "sender": sender,
        "user_value": int(user_value),
        "num_of_initial_validators": int(num_of_validators),
        "paid_fee_value": int(fee_value),
        "required_fee_value": int(required_fee_value),
        "primary_fee_required": primary_required,
        "primary_fee_budget": max(0, int(fee_value) - total_message_fees),
        "primary_fee_spent": 0,
        "primary_fee_refunded": 0,
        "execution_budget_total": execution_budget_total,
        "execution_fee_consumed": 0,
        "execution_fee_consumed_buckets": [],
        "genvm_fee_consumed_buckets": [],
        "genvm_message_fee_consumed": 0,
        "execution_fee_report": {},
        "message_fee_budget": total_message_fees,
        "message_fee_consumed": 0,
        "message_fee_refunded": 0,
        "external_message_fee_reserved": 0,
        "external_message_fee_reimbursed": 0,
        "external_message_fee_remainder": 0,
        "external_message_events": [],
        "appeal_bonds": [],
        "appeal_bonds_total": 0,
        "total_refunded": 0,
        "refunds": [],
        "top_ups": [
            {
                "sender": sender,
                "amount": int(fee_value),
                "primaryAmount": max(0, int(fee_value) - total_message_fees),
                "messageFees": total_message_fees,
                "feesDistribution": fees,
            }
        ],
        "fees_distribution": fees,
        "message_allocations": [
            _serializable_message_allocation(allocation)
            for allocation in message_allocations
        ],
        "allocation_consumed": {},
        "message_consumption_events": [],
    }


def _serializable_fees_distribution(
    fees_distribution: dict[str, Any],
) -> dict[str, int | list[int]]:
    return normalize_fees_distribution(fees_distribution)


def _serializable_message_allocation(node: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_message_allocation(node)
    return {
        "messageType": int(normalized["messageType"]),
        "onAcceptance": bool(normalized["onAcceptance"]),
        "parentIndex": int(normalized["parentIndex"]),
        "recipient": str(normalized["recipient"]).lower(),
        "callKey": _normalize_call_key(normalized["callKey"]),
        "budget": int(normalized["budget"]),
        "feeParams": _fee_params_hex(normalized["feeParams"]),
    }


def _fees_distribution_from_internal_params(
    fee_params: dict[str, Any],
    *,
    total_message_fees: int,
    parent_fees_distribution: dict[str, Any],
) -> dict[str, Any]:
    return {
        "leaderTimeunitsAllocation": int(fee_params["leaderTimeunitsAllocation"]),
        "validatorTimeunitsAllocation": int(fee_params["validatorTimeunitsAllocation"]),
        "appealRounds": int(fee_params["appealRounds"]),
        "executionBudgetPerRound": int(fee_params["executionBudgetPerRound"]),
        "executionConsumed": 0,
        "totalMessageFees": int(total_message_fees),
        "rotations": [int(rotation) for rotation in fee_params["rotations"]],
        "maxPriceGenPerTimeUnit": int(
            parent_fees_distribution.get("maxPriceGenPerTimeUnit", 0)
        ),
        "storageFeeMaxGasPrice": int(
            parent_fees_distribution.get("storageFeeMaxGasPrice", 0)
        ),
        "receiptFeeMaxGasPrice": int(
            parent_fees_distribution.get("receiptFeeMaxGasPrice", 0)
        ),
    }


def _genvm_message_fee_params(node: dict[str, Any]) -> dict[str, Any]:
    if int(node["messageType"]) == MESSAGE_TYPE_EXTERNAL:
        return {
            "leader_timeunits_allocation": 0,
            "validator_timeunits_allocation": 0,
            "execution_budget_per_round": 0,
            "rotations": [0],
        }

    decoded = decode_internal_message_fee_params(node["feeParams"])
    return {
        "leader_timeunits_allocation": int(decoded["leaderTimeunitsAllocation"]),
        "validator_timeunits_allocation": int(decoded["validatorTimeunitsAllocation"]),
        "execution_budget_per_round": int(decoded["executionBudgetPerRound"]),
        "rotations": [int(rotation) for rotation in decoded["rotations"]],
    }


def _genvm_unmetered_message_fee_allocation() -> list[dict[str, Any]]:
    fee_params = {
        "leader_timeunits_allocation": 5,
        "validator_timeunits_allocation": 5,
        "execution_budget_per_round": 2**10,
        "rotations": [4, 4, 4, 4, 4],
    }
    budget = 2**200
    return [
        {
            "message_type": "External",
            "parent_index": None,
            "recipient": None,
            "call_key": None,
            "budget": budget,
            "fee_params": fee_params,
        },
        {
            "message_type": "InternalFinalized",
            "parent_index": None,
            "recipient": None,
            "call_key": None,
            "budget": budget,
            "fee_params": fee_params,
        },
        {
            "message_type": "InternalAccepted",
            "parent_index": None,
            "recipient": None,
            "call_key": None,
            "budget": budget,
            "fee_params": fee_params,
        },
    ]


def _genvm_external_legacy_fallback_message_fee_allocation() -> dict[str, Any]:
    return {
        "message_type": "External",
        "parent_index": None,
        "recipient": None,
        "call_key": None,
        "budget": 2**200,
        "fee_params": {
            "leader_timeunits_allocation": 0,
            "validator_timeunits_allocation": 0,
            "execution_budget_per_round": 0,
            "rotations": [0],
        },
    }


def _allocation_subtree(
    message_allocations: list[dict[str, Any]],
    root_index: int,
) -> list[dict[str, Any]]:
    root = copy.deepcopy(
        _serializable_message_allocation(message_allocations[root_index])
    )
    root["parentIndex"] = NODE_ROOT_SENTINEL
    old_to_new: dict[int, int] = {root_index: 0}
    subtree: list[dict[str, Any]] = [root]
    for index, raw_node in enumerate(message_allocations):
        if index == root_index:
            continue
        node = _serializable_message_allocation(raw_node)
        parent_index = int(node["parentIndex"])
        if parent_index not in old_to_new:
            continue

        old_to_new[index] = len(subtree)
        copied = copy.deepcopy(node)
        copied["parentIndex"] = old_to_new[parent_index]
        subtree.append(copied)
    return subtree


def _child_allocations_from_message_subtree(
    message: dict[str, Any],
    allocation_subtree: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not allocation_subtree:
        return []

    root = _serializable_message_allocation(allocation_subtree[0])
    if not _is_matched_root_allocation(message, root):
        return [
            _serializable_message_allocation(allocation)
            for allocation in allocation_subtree
        ]

    child_allocations: list[dict[str, Any]] = []
    for raw_node in allocation_subtree[1:]:
        node = _serializable_message_allocation(raw_node)
        copied = copy.deepcopy(node)
        parent_index = int(copied["parentIndex"])
        copied["parentIndex"] = (
            NODE_ROOT_SENTINEL if parent_index == 0 else parent_index - 1
        )
        child_allocations.append(copied)
    return child_allocations


def _canonical_allocation_subtree(
    allocation_subtree: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    canonical = []
    for allocation in allocation_subtree:
        node = _submitted_allocation_node(allocation)
        canonical.append(
            {
                "messageType": int(node[0]),
                "onAcceptance": bool(node[1]),
                "parentIndex": int(node[2]),
                "recipient": str(node[3]).lower(),
                "callKey": "0x" + bytes(node[4]).hex(),
                "budget": int(node[5]),
                "feeParams": "0x" + bytes(node[6]).hex(),
            }
        )
    return canonical


def _is_matched_root_allocation(
    message: dict[str, Any],
    allocation: dict[str, Any],
) -> bool:
    if int(allocation["parentIndex"]) != NODE_ROOT_SENTINEL:
        return False
    if int(allocation["messageType"]) != int(
        message.get("messageType", MESSAGE_TYPE_INTERNAL)
    ):
        return False
    if bool(allocation["onAcceptance"]) != bool(message.get("onAcceptance", False)):
        return False
    if (
        str(allocation["recipient"]).lower()
        != str(message.get("recipient", "")).lower()
    ):
        return False
    if _normalize_call_key(allocation["callKey"]) != _normalize_call_key(
        message.get("callKey", CALL_KEY_WILDCARD)
    ):
        return False
    if _fee_params_hex(allocation["feeParams"]) != _fee_params_hex(
        message.get("feeParams", b"")
    ):
        return False
    return True


def _consume_against_allocation(
    accounting: dict[str, Any],
    message: dict[str, Any],
    declared_budget: int,
) -> None:
    allocations = accounting.get("message_allocations") or []
    if not allocations:
        return

    resolved = _resolve_allocation(allocations, message)
    if resolved is None:
        raise MessageNoMatchingAllocation("MessageNoMatchingAllocation")

    index, allocation = resolved
    if bool(allocation["onAcceptance"]) != bool(message.get("onAcceptance", False)):
        raise MessageEmissionPhaseMismatch("MessageEmissionPhaseMismatch")

    if _fee_params_hex(allocation["feeParams"]) != _fee_params_hex(
        message.get("feeParams", b"")
    ):
        raise MessageFeeParamsMismatch("MessageFeeParamsMismatch")

    key = str(index)
    consumed = int(accounting.setdefault("allocation_consumed", {}).get(key, 0))
    attempted = consumed + declared_budget
    if attempted > int(allocation["budget"]):
        raise MessageBudgetExceeded("MessageBudgetExceeded")
    accounting["allocation_consumed"][key] = attempted


def _reserve_external_execution(
    accounting: dict[str, Any],
    message: dict[str, Any],
    policy: StudioFeePolicy,
    *,
    reimburse: bool = True,
) -> int:
    if bool(message.get("onAcceptance", False)):
        return 0

    allocations = accounting.get("message_allocations") or []
    if not allocations:
        return 0

    resolved = _resolve_allocation(allocations, message)
    if resolved is None:
        return 0

    index, allocation = resolved
    if int(allocation["messageType"]) != MESSAGE_TYPE_EXTERNAL:
        return 0

    external_fee_params = decode_external_message_fee_params(allocation["feeParams"])
    gas_limit = int(external_fee_params["gasLimit"])
    max_gas_price = int(external_fee_params["maxGasPrice"])
    locked_price = (
        min(policy.receipt_gas_price, max_gas_price)
        if policy.receipt_gas_price > 0
        else 0
    )
    reservation = gas_limit * locked_price
    key = str(index)
    consumed = int(accounting.setdefault("allocation_consumed", {}).get(key, 0))
    attempted = consumed + reservation
    if attempted > int(allocation["budget"]):
        raise MessageBudgetExceeded("MessageBudgetExceeded")
    accounting["allocation_consumed"][key] = attempted

    gas_used = int(message.get("gasUsed", 0) or 0)
    reimbursement = min(reservation, gas_used * locked_price)
    remainder = reservation - reimbursement
    accounting["external_message_fee_reserved"] = (
        int(accounting.get("external_message_fee_reserved", 0)) + reservation
    )
    if reimburse:
        accounting["external_message_fee_reimbursed"] = (
            int(accounting.get("external_message_fee_reimbursed", 0)) + reimbursement
        )
        accounting["external_message_fee_remainder"] = (
            int(accounting.get("external_message_fee_remainder", 0)) + remainder
        )
    accounting.setdefault("external_message_events", []).append(
        {
            "recipient": str(message.get("recipient", "")).lower(),
            "callKey": _normalize_call_key(message.get("callKey", CALL_KEY_WILDCARD)),
            "allocationIndex": index,
            "gasLimit": gas_limit,
            "lockedGasPrice": locked_price,
            "reservation": reservation,
            "gasUsed": gas_used if reimburse else 0,
            "reimbursement": reimbursement if reimburse else 0,
            "remainder": remainder if reimburse else 0,
            "executionRecorded": bool(reimburse),
        }
    )
    return reimbursement if reimburse else 0


def _find_unrefunded_external_message_event(
    accounting: dict[str, Any],
    message: dict[str, Any],
) -> int | None:
    recipient = str(message.get("recipient", "")).lower()
    call_key = _normalize_call_key(message.get("callKey", CALL_KEY_WILDCARD))
    for index, event in enumerate(accounting.get("external_message_events") or []):
        if (
            event.get("failureRefunded")
            or event.get("refunded")
            or event.get("unreserved")
        ):
            continue
        if str(event.get("recipient", "")).lower() != recipient:
            continue
        if _normalize_call_key(event.get("callKey", CALL_KEY_WILDCARD)) != call_key:
            continue
        return index
    return None


def _find_unexecuted_external_message_event(
    accounting: dict[str, Any],
    message: dict[str, Any],
) -> int | None:
    recipient = str(message.get("recipient", "")).lower()
    call_key = _normalize_call_key(message.get("callKey", CALL_KEY_WILDCARD))
    for index, event in enumerate(accounting.get("external_message_events") or []):
        if event.get("executionRecorded") or event.get("unreserved"):
            continue
        if str(event.get("recipient", "")).lower() != recipient:
            continue
        if _normalize_call_key(event.get("callKey", CALL_KEY_WILDCARD)) != call_key:
            continue
        return index
    return None


def _unreserve_external_message_fee(
    accounting: dict[str, Any],
    message: dict[str, Any],
) -> tuple[int, int, int]:
    event_index = _find_unrefunded_external_message_event(accounting, message)
    if event_index is None:
        return 0, 0, 0

    event = accounting.setdefault("external_message_events", [])[event_index]
    reservation = int(event.get("reservation", 0) or 0)
    reimbursement = int(event.get("reimbursement", 0) or 0)
    remainder = int(event.get("remainder", 0) or 0)
    allocation_index = str(event.get("allocationIndex"))

    allocation_consumed = accounting.setdefault("allocation_consumed", {})
    consumed = int(allocation_consumed.get(allocation_index, 0) or 0)
    allocation_consumed[allocation_index] = max(0, consumed - reservation)
    accounting["message_fee_consumed"] = max(
        0,
        int(accounting.get("message_fee_consumed", 0)) - reimbursement,
    )
    accounting["external_message_fee_reserved"] = max(
        0,
        int(accounting.get("external_message_fee_reserved", 0)) - reservation,
    )
    accounting["external_message_fee_reimbursed"] = max(
        0,
        int(accounting.get("external_message_fee_reimbursed", 0)) - reimbursement,
    )
    accounting["external_message_fee_remainder"] = max(
        0,
        int(accounting.get("external_message_fee_remainder", 0)) - remainder,
    )
    event["unreserved"] = True
    return reservation, reimbursement, remainder


def _decrement_allocation_consumed(
    accounting: dict[str, Any],
    message: dict[str, Any],
    amount: int,
) -> None:
    resolved = _resolve_allocation(accounting.get("message_allocations") or [], message)
    if resolved is None:
        return
    index, _ = resolved
    allocation_consumed = accounting.setdefault("allocation_consumed", {})
    key = str(index)
    consumed = int(allocation_consumed.get(key, 0) or 0)
    allocation_consumed[key] = max(0, consumed - int(amount))


def _resolve_allocation(
    allocations: list[dict[str, Any]],
    message: dict[str, Any],
) -> tuple[int, dict[str, Any]] | None:
    message_type = int(message.get("messageType", MESSAGE_TYPE_INTERNAL))
    recipient = str(message.get("recipient", "")).lower()
    call_key = _normalize_call_key(message.get("callKey", CALL_KEY_WILDCARD))

    for wanted_call_key in (call_key, CALL_KEY_WILDCARD):
        for index, raw_allocation in enumerate(allocations):
            allocation = _serializable_message_allocation(raw_allocation)
            if int(allocation["parentIndex"]) != NODE_ROOT_SENTINEL:
                continue
            if int(allocation["messageType"]) != message_type:
                continue
            if str(allocation["recipient"]).lower() != recipient:
                continue
            if _normalize_call_key(allocation["callKey"]) == wanted_call_key:
                return index, allocation
    return None


def _receipt_message_fee_payloads(
    accounting: dict[str, Any],
    receipt: Any | None,
) -> list[dict[str, Any]]:
    if receipt is None:
        return []
    if not _receipt_execution_allows_messages(receipt):
        return []

    payloads: list[dict[str, Any]] = []
    for raw in _receipt_pending_transactions(receipt):
        message = _receipt_pending_transaction_fee_payload(raw)
        if int(message["messageType"]) == MESSAGE_TYPE_INTERNAL and accounting.get(
            "message_allocations"
        ):
            message = fill_message_fee_payload_from_allocation(accounting, message)
        payloads.append(message)
    return payloads


def _receipt_execution_allows_messages(receipt: Any) -> bool:
    status = _receipt_value(receipt, "execution_result")
    if status is None:
        status = _receipt_value(receipt, "executionResult")
    if hasattr(status, "value"):
        status = status.value
    if status is None:
        return True
    return str(status).replace("_", "").upper() in {
        "SUCCESS",
        "FINISHEDWITHRETURN",
        "RETURN",
    }


def _receipt_messages_require_fee_validation(
    accounting: dict[str, Any],
    messages: list[dict[str, Any]],
) -> bool:
    if int(accounting.get("message_fee_budget", 0) or 0) > 0:
        return True
    if accounting.get("message_allocations"):
        return True
    return any(_message_has_fee_fields(message) for message in messages)


def _message_has_fee_fields(message: dict[str, Any]) -> bool:
    if int(message.get("declaredBudget", 0) or 0) > 0:
        return True
    return _message_has_fee_params(message)


def _message_has_fee_params(message: dict[str, Any]) -> bool:
    fee_params = message.get("feeParams", b"")
    if isinstance(fee_params, str):
        return fee_params not in {"", "0x"}
    return bool(fee_params)


def _receipt_pending_transaction_fee_payload(raw: Any) -> dict[str, Any]:
    message = _pending_transaction_dict(raw)
    message_type = _message_type(message)
    data = _bytes_field(
        _message_field(message, "calldata", "data", b"")
        or _message_field(message, "data", "calldata", b"")
    )
    call_key = _message_field(
        message,
        "call_key",
        "callKey",
        CALL_KEY_WILDCARD,
    )
    if message_type == MESSAGE_TYPE_EXTERNAL:
        call_key = derive_external_message_call_key(call_key, data)
        fee_params = b""
    else:
        fee_params = _bytes_field(
            _message_field(message, "fee_params", "feeParams", b"")
        )
    return {
        "messageType": message_type,
        "recipient": _abi_address(
            _message_field(message, "address", "recipient")
            or _message_field(message, "recipient", "address")
        ),
        "value": int(message.get("value", 0) or 0),
        "data": data,
        "onAcceptance": _message_on_acceptance(message),
        "saltNonce": int(_message_field(message, "salt_nonce", "saltNonce", 0) or 0),
        "feeParams": fee_params,
        "declaredBudget": int(
            _message_field(
                message,
                "declared_budget",
                "declaredBudget",
                0,
            )
            or 0
        ),
        "allocationSubtree": _message_field(
            message,
            "allocation_subtree",
            "allocationSubtree",
            [],
        ),
        "callKey": call_key,
        "gasUsed": int(_message_field(message, "gas_used", "gasUsed", 0) or 0),
    }


def _execution_fee_buckets(consumed: list[int]) -> list[int]:
    if len(consumed) <= 2:
        return consumed
    return consumed[:2]


def _chargeable_execution_fee_buckets(
    consumed: list[int],
    fee_report: dict[str, Any] | None,
    policy: StudioFeePolicy,
    receipt: Any | None = None,
) -> list[int]:
    storage_fee = _chargeable_storage_fee(receipt, consumed)
    if policy.receipt_gas_price <= 0 or not isinstance(fee_report, dict):
        return [
            _bucket_value(consumed, 0),
            storage_fee,
        ]

    return [
        _receipt_report_chargeable_fee(fee_report),
        storage_fee,
    ]


def _chargeable_storage_fee(receipt: Any | None, consumed: list[int]) -> int:
    if receipt is not None and not _receipt_execution_allows_messages(receipt):
        return 0
    return _bucket_value(consumed, 1)


def _receipt_report_chargeable_fee(fee_report: dict[str, Any]) -> int:
    proposal = fee_report.get("proposalReceipt")
    proposal_fee = int(proposal.get("fee", 0) or 0) if isinstance(proposal, dict) else 0
    message_reveal = fee_report.get("messageReveal")
    message_fee = (
        int(message_reveal.get("consensusAdditionalFee", 0) or 0)
        if isinstance(message_reveal, dict)
        else 0
    )
    return max(0, proposal_fee + message_fee)


def _bucket_value(consumed: list[int], index: int) -> int:
    return int(consumed[index]) if len(consumed) > index else 0


def _execution_budget_per_round(accounting: dict[str, Any]) -> int:
    try:
        fees = normalize_fees_distribution(accounting.get("fees_distribution") or {})
    except FeeValidationError:
        return 0
    return int(fees["executionBudgetPerRound"])


def _genvm_fee_bucket_report(
    consumed: list[int],
    *,
    execution_budget_per_round: int = 0,
) -> dict[str, Any]:
    receipt_and_nondet_output = _bucket_value(consumed, 0)
    storage = _bucket_value(consumed, 1)
    message = _bucket_value(consumed, 2)
    total_execution = receipt_and_nondet_output + storage
    buckets = [
        {
            "index": 0,
            "name": "receiptAndNondetOutput",
            "consumed": receipt_and_nondet_output,
        },
        {"index": 1, "name": "storage", "consumed": storage},
    ]
    if len(consumed) > 2:
        buckets.append({"index": 2, "name": "message", "consumed": message})
    report = {
        "receiptAndNondetOutput": receipt_and_nondet_output,
        "storage": storage,
        "message": message,
        "totalExecution": total_execution,
        "totalWithMessage": sum(int(value) for value in consumed),
        "buckets": buckets,
    }
    overrun = max(0, total_execution - execution_budget_per_round)
    report.update(
        {
            "executionBudgetPerRound": execution_budget_per_round,
            "executionBudgetRemaining": max(
                0, execution_budget_per_round - total_execution
            ),
            "executionBudgetOverrun": overrun,
            "executionBudgetExceeded": overrun > 0,
        }
    )
    return report


def _execution_metering_report(
    *,
    chargeable_bucket_report: dict[str, Any],
    genvm_bucket_report: dict[str, Any],
) -> dict[str, int]:
    chargeable = int(chargeable_bucket_report.get("totalExecution", 0) or 0)
    genvm_reported = int(genvm_bucket_report.get("totalExecution", 0) or 0)
    return {
        "chargeableExecutionFee": chargeable,
        "genvmReportedExecution": genvm_reported,
        "genvmDeltaFromChargeable": genvm_reported - chargeable,
    }


def _receipt_budget_exhaustion_reason(
    receipt: Any | None,
    bucket_report: dict[str, Any] | None = None,
) -> str | None:
    genvm_result = _receipt_genvm_result(receipt)
    if isinstance(genvm_result, dict):
        for key in ("budgetExhaustionReason", "budget_exhaustion_reason"):
            reason = genvm_result.get(key)
            if reason not in (None, "", "None"):
                return str(reason)

        error_code = genvm_result.get("error_code") or genvm_result.get("errorCode")
        if error_code in {"ExecutionBudgetExceeded", "MessageBudgetExceeded"}:
            return str(error_code)

    if bucket_report and bucket_report.get("executionBudgetExceeded"):
        return "ExecutionBudgetExceeded"

    return None


def _message_fee_accounting_report(accounting: dict[str, Any]) -> dict[str, int]:
    budget = int(accounting.get("message_fee_budget", 0) or 0)
    total_consumed = int(accounting.get("message_fee_consumed", 0) or 0)
    external_reserved = int(accounting.get("external_message_fee_reserved", 0) or 0)
    external_reimbursed = int(accounting.get("external_message_fee_reimbursed", 0) or 0)
    external_remainder = int(accounting.get("external_message_fee_remainder", 0) or 0)
    declared_consumed = max(0, total_consumed - external_reimbursed)
    declared_refunded = int(accounting.get("message_fee_refunded", 0) or 0)
    genvm_metered_consumed = int(accounting.get("genvm_message_fee_consumed", 0) or 0)
    report = {
        "budget": budget,
        "declaredConsumed": declared_consumed,
        "genvmMeteredConsumed": genvm_metered_consumed,
        "declaredRefunded": declared_refunded,
        "remaining": max(0, budget - total_consumed - declared_refunded),
        "meteringDelta": declared_consumed - genvm_metered_consumed,
    }
    if external_reserved or external_reimbursed or external_remainder:
        report["externalReserved"] = external_reserved
        report["externalReimbursed"] = external_reimbursed
        report["externalRemainder"] = external_remainder
        report["totalConsumed"] = total_consumed
    if accounting.get("reported_message_fees_total") is not None:
        report["reportedTotal"] = int(accounting["reported_message_fees_total"])
    return report


def _attach_message_fee_accounting_report(accounting: dict[str, Any]) -> None:
    report = dict(accounting.get("execution_fee_report") or {})
    report["messageFees"] = _message_fee_accounting_report(accounting)
    accounting["execution_fee_report"] = report


def _attach_recommended_fee_preset(
    accounting: dict[str, Any],
    policy: StudioFeePolicy,
) -> None:
    accounting["recommended_fee_preset"] = recommended_fee_preset(accounting, policy)


def recommended_fee_preset(
    accounting: dict[str, Any],
    policy: StudioFeePolicy | None = None,
    *,
    padding_bps: int = DEFAULT_PRICE_CAP_HEADROOM_BPS,
) -> dict[str, Any]:
    policy = _accounting_policy(accounting, policy)
    fees = normalize_fees_distribution(accounting.get("fees_distribution") or {})
    report = accounting.get("execution_fee_report") or {}
    message_report = (
        report.get("messageFees") if isinstance(report.get("messageFees"), dict) else {}
    )
    message_allocations = list(accounting.get("message_allocations") or [])
    num_validators = int(
        accounting.get("num_of_initial_validators") or VALIDATORS_PER_ROUND[0]
    )

    observed_execution = _observed_chargeable_execution_fee(accounting, report)
    recommended_execution = int(fees["executionBudgetPerRound"])
    if observed_execution > 0:
        recommended_execution = max(
            _with_padding(observed_execution, padding_bps),
            policy.message_fee_params_budget_floor(),
        )

    declared_message = _int_report_field(message_report, "declaredConsumed")
    external_reserved = int(accounting.get("external_message_fee_reserved", 0) or 0)
    observed_message_budget = declared_message + external_reserved
    recommended_message_budget = int(fees["totalMessageFees"])
    message_budget_mode = "current"
    if message_allocations:
        message_budget_mode = "allocation-preserved"
    elif observed_message_budget > 0:
        recommended_message_budget = _with_padding(observed_message_budget, padding_bps)
        message_budget_mode = "observed"

    distribution = _serializable_fees_distribution(
        {
            **fees,
            "rotations": _preset_rotations(fees),
            "executionBudgetPerRound": recommended_execution,
            "totalMessageFees": recommended_message_budget,
        }
    )
    fee_value = required_fee_deposit(
        distribution,
        num_validators,
        policy,
    )

    return {
        "source": "simulation",
        "paddingBps": int(padding_bps),
        "numOfInitialValidators": num_validators,
        "distribution": distribution,
        "feeValue": fee_value,
        "messageAllocations": message_allocations,
        "messageBudgetMode": message_budget_mode,
        "observed": {
            "executionFee": observed_execution,
            "messageFeeBudget": observed_message_budget,
            "declaredMessageFees": declared_message,
            "externalMessageReserved": external_reserved,
            "totalEstimatedFee": _int_report_field(report, "totalEstimatedFee"),
            "totalStudioMeteredFee": _int_report_field(report, "totalStudioMeteredFee"),
        },
    }


def _preset_rotations(fees: dict[str, Any]) -> list[int]:
    appeal_rounds = int(fees.get("appealRounds", 0) or 0)
    expected = appeal_rounds + 1
    rotations = [int(rotation) for rotation in fees.get("rotations", [])]
    if len(rotations) >= expected:
        return rotations[:expected]
    return rotations + ([0] * (expected - len(rotations)))


def _observed_chargeable_execution_fee(
    accounting: dict[str, Any],
    report: dict[str, Any],
) -> int:
    consumed = int(accounting.get("execution_fee_consumed", 0) or 0)
    if consumed > 0:
        return consumed

    chargeable = report.get("chargeableExecution")
    if isinstance(chargeable, dict):
        total = int(chargeable.get("totalExecution", 0) or 0)
        if total > 0:
            return total

    return _int_report_field(report, "totalEstimatedFee")


def _int_report_field(report: dict[str, Any], key: str) -> int:
    try:
        return int(report.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _refresh_message_fee_accounting_report_if_present(
    accounting: dict[str, Any],
    policy: StudioFeePolicy | None = None,
) -> None:
    if accounting.get("execution_fee_report"):
        policy = _accounting_policy(accounting, policy)
        _attach_message_fee_accounting_report(accounting)
        _attach_recommended_fee_preset(accounting, policy)


def _receipt_data_fees_consumed(receipt: Any | None) -> list[int] | None:
    if receipt is None:
        return None
    genvm_result = (
        getattr(receipt, "genvm_result", None)
        if not isinstance(receipt, dict)
        else receipt.get("genvm_result")
    )
    if not isinstance(genvm_result, dict):
        return None
    consumed = genvm_result.get("data_fees_consumed")
    if consumed is not None:
        return [int(value) for value in consumed]
    totals = genvm_result.get("data_fee_bucket_totals")
    remaining = genvm_result.get("data_fees_remaining")
    if totals is None or remaining is None:
        return None
    return [max(0, int(total) - int(rest)) for total, rest in zip(totals, remaining)]


def _receipt_reported_message_fees_total(receipt: Any | None) -> int | None:
    if receipt is None:
        return None
    for source in (receipt, _receipt_genvm_result(receipt) or {}):
        for key in (
            "reported_message_fees_total",
            "reportedMessageFeesTotal",
            "message_fees_consumed",
            "messageFeesConsumed",
        ):
            value = _receipt_value(source, key)
            if value is not None:
                return int(value)
    return None


def _receipt_fee_report(
    receipt: Any | None,
    policy: StudioFeePolicy,
    message_payloads: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if receipt is None:
        return None

    eq_outputs_length = _receipt_eq_blocks_outputs_length(receipt)
    receipt_bytes = policy.estimate_propose_receipt_bytes(eq_outputs_length)
    proposal_gas = policy.estimate_propose_receipt_gas(receipt_bytes)
    proposal_fee = proposal_gas * policy.receipt_gas_price
    report: dict[str, Any] = {
        "receiptGasPrice": policy.receipt_gas_price,
        "proposalReceipt": {
            "eqBlocksOutputsLength": eq_outputs_length,
            "receiptBytes": receipt_bytes,
            "estimatedGas": proposal_gas,
            "fee": proposal_fee,
        },
        "totalEstimatedFee": proposal_fee,
        "totalStudioMeteredFee": proposal_fee,
    }

    submitted_messages, message_reports = _receipt_submitted_messages_and_reports(
        receipt,
        message_payloads,
    )
    if submitted_messages:
        message_bytes = len(encode([SUBMITTED_MESSAGE_ABI_TYPE], [submitted_messages]))
        message_gas = policy.estimate_message_reveal_gas(
            message_bytes,
            len(submitted_messages),
        )
        consensus_message_gas = policy.estimate_consensus_message_reveal_gas(
            message_bytes,
            len(submitted_messages),
        )
        message_fee = message_gas * policy.receipt_gas_price
        consensus_message_fee = consensus_message_gas * policy.receipt_gas_price
        report["messageReveal"] = {
            "messageBytes": message_bytes,
            "messageCount": len(submitted_messages),
            "estimatedGas": message_gas,
            "fee": message_fee,
            "consensusAdditionalGas": consensus_message_gas,
            "consensusAdditionalFee": consensus_message_fee,
            "studioFixedOverheadGas": max(0, message_gas - consensus_message_gas),
            "studioFixedOverheadFee": max(0, message_fee - consensus_message_fee),
            "messages": message_reports,
        }
        report["totalEstimatedFee"] += consensus_message_fee
        report["totalStudioMeteredFee"] += message_fee

    return report


def _receipt_eq_blocks_outputs_length(receipt: Any) -> int:
    genvm_result = _receipt_genvm_result(receipt)
    if isinstance(genvm_result, dict):
        explicit = genvm_result.get("eq_blocks_outputs_length") or genvm_result.get(
            "eqBlocksOutputsLength"
        )
        if explicit is not None:
            return max(0, int(explicit))

    explicit_outputs = _receipt_value(receipt, "eq_blocks_outputs")
    if isinstance(explicit_outputs, str) and explicit_outputs.startswith("0x"):
        return len(bytes.fromhex(explicit_outputs.removeprefix("0x")))

    return len(_encode_eq_blocks_outputs(_receipt_eq_outputs(receipt)))


def _receipt_submitted_messages(receipt: Any) -> list[tuple[Any, ...]]:
    submitted, _ = _receipt_submitted_messages_and_reports(receipt)
    return submitted


def _receipt_submitted_messages_and_reports(
    receipt: Any,
    message_payloads: list[dict[str, Any]] | None = None,
) -> tuple[list[tuple[Any, ...]], list[dict[str, Any]]]:
    submitted = []
    reports = []
    raw_messages = (
        message_payloads
        if message_payloads is not None
        else [
            _pending_transaction_dict(raw)
            for raw in _receipt_pending_transactions(receipt)
        ]
    )
    for message in raw_messages:
        message_type = _message_type(message)
        recipient = _abi_address(
            _message_field(message, "address", "recipient")
            or _message_field(message, "recipient", "address")
        )
        value = int(message.get("value", 0) or 0)
        data = _bytes_field(
            _message_field(message, "calldata", "data", b"")
            or _message_field(message, "data", "calldata", b"")
        )
        on_acceptance = _message_on_acceptance(message)
        salt_nonce = int(_message_field(message, "salt_nonce", "saltNonce", 0) or 0)
        fee_params = _bytes_field(
            _message_field(message, "fee_params", "feeParams", b"")
        )
        if message_type == MESSAGE_TYPE_EXTERNAL:
            fee_params = b""
        declared_budget = int(
            _message_field(
                message,
                "declared_budget",
                "declaredBudget",
                0,
            )
            or 0
        )
        allocation_subtree = _allocation_subtree_bytes(
            _message_field(
                message,
                "allocation_subtree",
                "allocationSubtree",
            )
        )
        call_key_value = _message_field(
            message,
            "call_key",
            "callKey",
            CALL_KEY_WILDCARD,
        )
        if message_type == MESSAGE_TYPE_EXTERNAL:
            call_key_value = derive_external_message_call_key(call_key_value, data)
        call_key = _bytes32_field(call_key_value)
        submitted.append(
            (
                message_type,
                recipient,
                value,
                data,
                on_acceptance,
                salt_nonce,
                fee_params,
                declared_budget,
                allocation_subtree,
                call_key,
            )
        )
        reports.append(
            {
                "messageFeeMode": _message_fee_mode(
                    message_type,
                    allocation_subtree,
                    message.get("messageFeeMode"),
                ),
                "messageType": (
                    "External" if message_type == MESSAGE_TYPE_EXTERNAL else "Internal"
                ),
                "recipient": recipient,
                "value": value,
                "dataBytes": len(data),
                "onAcceptance": on_acceptance,
                "saltNonce": salt_nonce,
                "feeParams": _fee_params_hex(fee_params),
                "feeParamsDecoded": _message_fee_params_for_report(
                    message_type,
                    fee_params,
                ),
                "feeParamsBytes": len(fee_params),
                "declaredBudget": declared_budget,
                "allocationSubtree": "0x" + allocation_subtree.hex(),
                "allocationSubtreeBytes": len(allocation_subtree),
                "callKey": "0x" + call_key.hex(),
            }
        )
    return submitted, reports


def _message_fee_mode(
    message_type: int,
    allocation_subtree: bytes,
    explicit: Any = None,
) -> str:
    if explicit in {"mode1", "mode2", "external"}:
        return str(explicit)
    if message_type == MESSAGE_TYPE_EXTERNAL:
        return "external"
    return "mode2" if allocation_subtree else "mode1"


def _message_fee_params_for_report(
    message_type: int,
    fee_params: bytes,
) -> dict[str, Any] | None:
    if not fee_params:
        return None
    try:
        if message_type == MESSAGE_TYPE_EXTERNAL:
            return decode_external_message_fee_params(fee_params)
        return decode_internal_message_fee_params(fee_params)
    except FeeValidationError:
        return None


def _receipt_pending_transactions(receipt: Any) -> list[Any]:
    pending = _receipt_value(receipt, "pending_transactions", [])
    return pending if isinstance(pending, list) else list(pending or [])


def _pending_transaction_dict(pending_transaction: Any) -> dict[str, Any]:
    if isinstance(pending_transaction, dict):
        return pending_transaction
    if hasattr(pending_transaction, "to_dict"):
        return pending_transaction.to_dict()
    return {
        "address": getattr(pending_transaction, "address", ""),
        "calldata": getattr(
            pending_transaction,
            "calldata",
            getattr(pending_transaction, "data", b""),
        ),
        "code": getattr(pending_transaction, "code", b""),
        "salt_nonce": getattr(pending_transaction, "salt_nonce", 0),
        "on": getattr(pending_transaction, "on", "finalized"),
        "value": getattr(pending_transaction, "value", 0),
        "is_eth_send": getattr(
            pending_transaction,
            "is_eth_send",
            getattr(pending_transaction, "isEthSend", False),
        ),
        "fee_params": getattr(pending_transaction, "fee_params", b""),
        "declared_budget": getattr(pending_transaction, "declared_budget", 0),
        "call_key": getattr(pending_transaction, "call_key", CALL_KEY_WILDCARD),
        "allocation_subtree": getattr(pending_transaction, "allocation_subtree", []),
    }


def _message_field(
    message: dict[str, Any],
    snake_key: str,
    camel_key: str,
    default: Any = None,
) -> Any:
    if snake_key in message:
        return message[snake_key]
    return message.get(camel_key, default)


def _message_type(message: dict[str, Any]) -> int:
    explicit = _message_field(message, "message_type", "messageType")
    if explicit is not None:
        if isinstance(explicit, str) and not explicit.isdigit():
            return (
                MESSAGE_TYPE_EXTERNAL
                if explicit.lower() == "external"
                else MESSAGE_TYPE_INTERNAL
            )
        return int(explicit)
    is_eth_send = bool(_message_field(message, "is_eth_send", "isEthSend", False))
    return MESSAGE_TYPE_EXTERNAL if is_eth_send else MESSAGE_TYPE_INTERNAL


def _message_on_acceptance(message: dict[str, Any]) -> bool:
    explicit = _message_field(message, "on_acceptance", "onAcceptance")
    if explicit is not None:
        return bool(explicit)
    phase = str(message.get("on", "finalized")).lower()
    return phase == "accepted" or phase == "acceptance"


def _receipt_eq_outputs(receipt: Any) -> list[bytes]:
    eq_outputs = _receipt_value(receipt, "eq_outputs")
    if eq_outputs is None:
        eq_outputs = _receipt_value(receipt, "eqOutputs")
    if isinstance(eq_outputs, dict):

        def sort_key(item: tuple[Any, Any]) -> int:
            try:
                return int(item[0])
            except (TypeError, ValueError):
                return 0

        return [
            _eq_output_bytes(value)
            for _, value in sorted(eq_outputs.items(), key=sort_key)
        ]
    if isinstance(eq_outputs, list):
        return [_eq_output_bytes(value) for value in eq_outputs]
    return []


def _eq_output_bytes(value: Any) -> bytes:
    if isinstance(value, dict):
        value = value.get("data", value.get("output", value.get("value", b"")))
    return _bytes_field(value)


def _encode_eq_blocks_outputs(eq_outputs: list[bytes]) -> bytes:
    return rlp.encode([*eq_outputs, b"padded"])


def _receipt_genvm_result(receipt: Any) -> dict[str, Any] | None:
    genvm_result = _receipt_value(receipt, "genvm_result")
    return genvm_result if isinstance(genvm_result, dict) else None


def _receipt_value(receipt: Any, key: str, default: Any = None) -> Any:
    if isinstance(receipt, dict):
        return receipt.get(key, default)
    return getattr(receipt, key, default)


def _abi_address(value: Any) -> str:
    raw = str(value or "").lower()
    if raw.startswith("0x"):
        raw = raw[2:]
    if len(raw) == 40:
        try:
            bytes.fromhex(raw)
            return "0x" + raw
        except ValueError:
            pass
    return "0x" + ("0" * 40)


def _allocation_subtree_bytes(value: Any) -> bytes:
    if value is None or value == []:
        return b""
    if isinstance(value, list):
        nodes = [_submitted_allocation_node(node) for node in value]
        return encode([MESSAGE_ALLOCATION_NODE_ABI_TYPE], [nodes])
    if isinstance(value, dict):
        return encode(
            [MESSAGE_ALLOCATION_NODE_ABI_TYPE],
            [[_submitted_allocation_node(value)]],
        )
    return _bytes_field(value)


def _submitted_allocation_node(node: dict[str, Any]) -> tuple[Any, ...]:
    return (
        int(node.get("messageType", node.get("message_type", MESSAGE_TYPE_INTERNAL))),
        bool(node.get("onAcceptance", node.get("on_acceptance", False))),
        int(node.get("parentIndex", node.get("parent_index", NODE_ROOT_SENTINEL))),
        _abi_address(node.get("recipient")),
        _bytes32_field(node.get("callKey", node.get("call_key", CALL_KEY_WILDCARD))),
        int(node.get("budget", 0) or 0),
        _bytes_field(node.get("feeParams", node.get("fee_params", b""))),
    )


def _bytes32_field(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value.rjust(32, b"\x00")[-32:]
    raw = str(value or "").removeprefix("0x").lower()
    try:
        return bytes.fromhex(raw.rjust(64, "0")[-64:])
    except ValueError:
        return bytes(32)


def _bytes_field(value: Any) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, str):
        raw = value.removeprefix("0x")
        if value.startswith("0x"):
            try:
                return bytes.fromhex(raw)
            except ValueError:
                return b""
        if raw == "":
            return b""
        try:
            return base64.b64decode(raw, validate=True)
        except Exception:
            try:
                return bytes.fromhex(raw)
            except ValueError:
                return raw.encode("utf-8")
    return bytes(value)


def _fee_params_hex(fee_params: bytes | str) -> str:
    if isinstance(fee_params, str):
        return "0x" + fee_params.removeprefix("0x").lower()
    return "0x" + bytes(fee_params).hex()


def _normalize_call_key(call_key: bytes | str) -> str:
    if isinstance(call_key, bytes):
        raw = call_key.hex()
    else:
        raw = str(call_key).removeprefix("0x").lower()
    return "0x" + raw.rjust(64, "0")[-64:]


def derive_external_message_call_key(
    call_key: bytes | str | None, calldata: Any
) -> str:
    normalized = _normalize_call_key(call_key or CALL_KEY_WILDCARD)
    if normalized != CALL_KEY_WILDCARD:
        return normalized

    raw_calldata = _bytes_field(calldata)
    if len(raw_calldata) < 4:
        return CALL_KEY_WILDCARD

    return "0x" + raw_calldata[:4].hex().ljust(64, "0")
