from __future__ import annotations

import base64
import copy
import os
from dataclasses import dataclass, fields, replace
from typing import Any, Callable

import rlp
from eth_abi import decode, encode
from eth_hash.auto import keccak

from backend.consensus.history import (
    LEADER_APPEAL_CONSENSUS_ROUNDS,
    NON_ROUND_CONSENSUS_EVENTS,
    VALIDATOR_APPEAL_CONSENSUS_ROUNDS,
    actual_leader_rotations_by_round,
    logical_fee_round_entries,
)
from backend.consensus.types import ConsensusRound

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
MAX_ALLOCATED_MESSAGES_CAP = 20
NONDET_OUTPUT_LENGTH_BYTES = 32
GENVM_NONDET_OUTPUT_HEADER_BYTES = 64
NODE_ROOT_SENTINEL = (1 << 256) - 1
UINT256_MAX = (1 << 256) - 1
MAX_CONTRIBUTION_SEGMENTS = 64
EMPTY_CALL_KEY = "0x" + ("0" * 64)
# Consensus reserves keccak256("") as the allocation wildcard. bytes32(0)
# remains the call key for an empty method name and must not match every call.
CALL_KEY_WILDCARD = "0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
MESSAGE_TYPE_EXTERNAL = 0
MESSAGE_TYPE_INTERNAL = 1
FEE_ACCOUNTING_KEY = "fee_accounting"
FEE_POLICY_SNAPSHOT_KEY = "fee_policy_snapshot"

APPEAL_SUCCESS_ROUNDS = {
    ConsensusRound.VALIDATOR_APPEAL_SUCCESSFUL.value,
    ConsensusRound.LEADER_APPEAL_SUCCESSFUL.value,
    ConsensusRound.LEADER_TIMEOUT_APPEAL_SUCCESSFUL.value,
    ConsensusRound.VALIDATOR_TIMEOUT_APPEAL_SUCCESSFUL.value,
}
APPEAL_FAILED_ROUNDS = {
    ConsensusRound.VALIDATOR_APPEAL_FAILED.value,
    ConsensusRound.LEADER_APPEAL_FAILED.value,
    ConsensusRound.LEADER_TIMEOUT_APPEAL_FAILED.value,
    ConsensusRound.VALIDATORS_TIMEOUT_APPEAL_FAILED.value,
}
ROUND_LEADER_MULTIPLIERS = {
    ConsensusRound.LEADER_TIMEOUT.value: (1, 2),
}

INTERNAL_MESSAGE_FEE_PARAMS_ABI_TYPE = (
    "(uint256,uint256,uint256,uint256,uint256[],uint256,uint256,uint256)"
)
EXTERNAL_MESSAGE_FEE_PARAMS_ABI_TYPE = "(uint256,uint256)"
MESSAGE_ALLOCATION_NODE_ABI_TYPE = (
    "(uint8,bool,uint256,address,bytes32,uint256,bytes)[]"
)
SUBMITTED_MESSAGE_ABI_TYPE = (
    "(uint8,address,uint256,bytes,bool,uint256,bytes,uint256,bytes,bytes32,bool)[]"
)

WEI_PER_GEN = 10**18
# Keep the standalone Studio policy identical to the v0.6 deployment defaults.
# The multiplier is deliberately the neutral value 1 while the storage and
# receipt gas prices use the deployment's 0.25 gwei fallback price.
DEFAULT_GEN_PER_TIME_UNIT = 1
DEFAULT_STORAGE_UNIT_PRICE = 250_000_000
DEFAULT_RECEIPT_GAS_PRICE = 250_000_000
DEFAULT_TRANSACTION_EXECUTION_BUDGET_PER_ROUND = 100_000_000
DEFAULT_LEADER_TIMEUNITS_ALLOCATION = 100
DEFAULT_VALIDATOR_TIMEUNITS_ALLOCATION = 200
DEFAULT_PRICE_CAP_HEADROOM_BPS = 12_000
DEFAULT_PARENT_MESSAGE_RECEIPT_HEADROOM = 10_000
# Conservative off-chain quote for an allocated EVM message. Consensus refunds
# unused reservation, while 21k only covers a plain value transfer and would
# make common contract calls (for example ERC-20 transfer) deterministically OOG.
DEFAULT_EXTERNAL_MESSAGE_GAS_LIMIT = 500_000
DEFAULT_TIME_UNIT_OVERLAY_BPS = 1_500
DEFAULT_MIN_PROPOSE_TIMEUNITS = 30
DEFAULT_MAX_PROPOSE_TIMEUNITS = 600
DEFAULT_MIN_COMMIT_TIMEUNITS = 30
DEFAULT_MAX_COMMIT_TIMEUNITS = 600
GENVM_UNMETERED_DATA_FEE_BUCKET = (1 << 256) - 1
# GenVM v0.3's fee config deliberately shares one reservoir for all execution
# costs. The remaining fee bucket and metadata counters are stable parts of
# the manager API pinned in third_party/genvm/version:
#   0: storage + receipt + nondeterministic output + event costs
#   1: outbound-message fee reservations
#   2: raw nondeterministic-output bytes (metadata, not a fee)
#   3: canonical SubmittedMessage payload bytes (metadata, not a fee)
#   4: canonical SubmittedMessage count (metadata, not a fee)
GENVM_EXECUTION_FEE_BUCKET = 0
GENVM_MESSAGE_FEE_BUCKET = 1
GENVM_NONDET_OUTPUT_BYTES_BUCKET = 2
GENVM_SUBMITTED_MESSAGE_BYTES_BUCKET = 3
GENVM_SUBMITTED_MESSAGE_COUNT_BUCKET = 4
# GenVM addresses the same buckets by name once the manager API supports it.
GENVM_FEE_BUCKET_NAMES: tuple[str, ...] = (
    "execution_data_gas",
    "message_fee",
    "nondet_outputs",
    "submitted_messages",
    "submitted_messages_count",
)

type FeeBucketConsumption = dict[str, int] | list[int]


class FeeValidationError(ValueError):
    pass


class ArithmeticOverflow(FeeValidationError):
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


class FeeValueMustBeNonZero(FeeValidationError):
    pass


class PhaseTimeoutOutOfBounds(FeeValidationError):
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


class TopUpCannotExtendSchedule(FeeValidationError):
    pass


class MessageDeclaredBudgetInsufficient(FeeValidationError):
    pass


class MessageFeesReportMismatch(FeeValidationError):
    pass


class MessageBudgetExceeded(FeeValidationError):
    pass


class MessageAllocationsRestricted(FeeValidationError):
    pass


class ContributionSegmentsFull(FeeValidationError):
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


class EqOutputsTooLarge(FeeValidationError):
    pass


class SubmittedMessagesTooLarge(FeeValidationError):
    pass


class MessageEffectDescriptorMismatch(FeeValidationError):
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
    max_allocated_messages: int = MAX_ALLOCATED_MESSAGES_CAP
    max_messages_per_tx: int = 0
    min_external_gas_limit: int = 0
    default_external_gas_limit: int = DEFAULT_EXTERNAL_MESSAGE_GAS_LIMIT
    max_eq_outputs_bytes: int = 0
    max_submitted_messages_bytes: int = 0
    time_unit_overlay_bps: int = 0
    enforce_v06_submission_config: bool = False
    # Pure/test policies retain the permissive contract-initializer bounds.
    # from_env() below uses the v0.6 deployment defaults (30..600).
    min_propose_timeunits: int = 1
    max_propose_timeunits: int = (1 << 128) - 1
    min_commit_timeunits: int = 1
    max_commit_timeunits: int = (1 << 128) - 1

    def __post_init__(self) -> None:
        if not 0 <= int(self.max_allocated_messages) <= MAX_ALLOCATED_MESSAGES_CAP:
            raise ValueError(
                "invalid max allocated messages: "
                f"{self.max_allocated_messages} (protocol cap "
                f"{MAX_ALLOCATED_MESSAGES_CAP})"
            )
        if not 0 <= int(self.time_unit_overlay_bps) < 10_000:
            raise ValueError(
                "invalid time-unit overlay bps: "
                f"{self.time_unit_overlay_bps} (expected 0..9999)"
            )
        if int(self.default_external_gas_limit) <= 0:
            raise ValueError(
                "invalid default external gas limit: "
                f"{self.default_external_gas_limit} (expected > 0)"
            )
        for minimum, maximum, label in (
            (
                self.min_propose_timeunits,
                self.max_propose_timeunits,
                "propose",
            ),
            (
                self.min_commit_timeunits,
                self.max_commit_timeunits,
                "commit",
            ),
        ):
            if int(minimum) <= 0 or int(maximum) < int(minimum):
                raise ValueError(
                    f"invalid {label} timeunit bounds: {minimum}..{maximum}"
                )

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
            max_allocated_messages=_env_int(
                "GENLAYER_STUDIO_MAX_ALLOCATED_MESSAGES",
                MAX_ALLOCATED_MESSAGES_CAP,
            ),
            max_messages_per_tx=_env_int("GENLAYER_STUDIO_MAX_MESSAGES_PER_TX", 0),
            min_external_gas_limit=_env_int(
                "GENLAYER_STUDIO_MIN_EXTERNAL_GAS_LIMIT", 0
            ),
            default_external_gas_limit=_env_int(
                "GENLAYER_STUDIO_DEFAULT_EXTERNAL_GAS_LIMIT",
                DEFAULT_EXTERNAL_MESSAGE_GAS_LIMIT,
            ),
            max_eq_outputs_bytes=_env_int("GENLAYER_STUDIO_MAX_EQ_OUTPUTS_BYTES", 0),
            max_submitted_messages_bytes=_env_int(
                "GENLAYER_STUDIO_MAX_SUBMITTED_MESSAGES_BYTES", 0
            ),
            time_unit_overlay_bps=_env_int(
                "GENLAYER_STUDIO_TIME_UNIT_OVERLAY_BPS",
                DEFAULT_TIME_UNIT_OVERLAY_BPS,
            ),
            enforce_v06_submission_config=True,
            min_propose_timeunits=_env_int(
                "GENLAYER_STUDIO_MIN_PROPOSE_TIMEUNITS",
                DEFAULT_MIN_PROPOSE_TIMEUNITS,
            ),
            max_propose_timeunits=_env_int(
                "GENLAYER_STUDIO_MAX_PROPOSE_TIMEUNITS",
                DEFAULT_MAX_PROPOSE_TIMEUNITS,
            ),
            min_commit_timeunits=_env_int(
                "GENLAYER_STUDIO_MIN_COMMIT_TIMEUNITS",
                DEFAULT_MIN_COMMIT_TIMEUNITS,
            ),
            max_commit_timeunits=_env_int(
                "GENLAYER_STUDIO_MAX_COMMIT_TIMEUNITS",
                DEFAULT_MAX_COMMIT_TIMEUNITS,
            ),
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
        return (
            self.fixed_message_reveal_gas
            + self.intrinsic_gas
            + self.bootloader_overhead
            + (max(0, int(message_bytes)) * self.calldata_gas_per_byte)
            + (max(0, int(message_count)) * self.gas_per_changed_slot)
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
        return (
            self.receipt_wrapper_bytes + GENVM_NONDET_OUTPUT_HEADER_BYTES
        ) * self.calldata_gas_per_byte

    def message_fee_params_budget_floor(self) -> int:
        return self.minimum_execution_budget_per_round()

    def minimum_execution_budget_per_round(self) -> int:
        if self.receipt_gas_price <= 0:
            return 0
        # FeeManager.messageFeeParamsBudgetFloor() prices only a minimum-size
        # proposed receipt. Message reveal and nondeterministic-output charges
        # remain ordinary per-round consumption, not admission requirements.
        return (
            self.estimate_propose_receipt_gas(MIN_RECEIPT_BYTES)
            * self.receipt_gas_price
        )

    def genvm_start_budget_floor(self) -> int:
        """Budget GenVM must reserve before contract code begins executing."""
        if self.receipt_gas_price <= 0:
            return 0
        start_gas = (
            self.fixed_propose_receipt_gas
            + self.intrinsic_gas
            + self.bootloader_overhead
            + (PROPOSE_RECEIPT_SLOTS * self.gas_per_changed_slot)
            + self.estimate_nondet_output_start_gas()
        )
        return start_gas * self.receipt_gas_price

    def _legacy_genvm_start_budget_floor(self) -> int:
        if self.receipt_gas_price <= 0:
            return 0
        start_gas = (
            self.fixed_propose_receipt_gas
            + self.intrinsic_gas
            + self.bootloader_overhead
            + (PROPOSE_RECEIPT_SLOTS * self.gas_per_changed_slot)
            + self.fixed_message_reveal_gas
            + self.intrinsic_gas
            + self.bootloader_overhead
            + (MESSAGE_REVEAL_LENGTH_SLOTS * self.gas_per_changed_slot)
            + (NONDET_OUTPUT_LENGTH_BYTES * self.calldata_gas_per_byte)
        )
        return start_gas * self.receipt_gas_price

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
        # Snapshots created before the overlay field was introduced remain
        # valid and preserve the economics under which they were funded.
        defaults = cls()
        return cls(
            **{
                field.name: int(snapshot.get(field.name, getattr(defaults, field.name)))
                for field in fields(cls)
            }
        )


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


def _live_policy_for_accounting(
    accounting: dict[str, Any] | None,
    override: StudioFeePolicy | None = None,
) -> StudioFeePolicy:
    """Return the policy applicable to this transaction's next operation.

    Before activation an explicit/live policy is authoritative. Activation
    commits the complete receipt formula, limits, and prices so later config
    changes cannot reprice or invalidate work already admitted under escrow.
    """
    if accounting and accounting.get("activation_prices_locked"):
        return _accounting_policy(accounting)
    if override is not None:
        return override
    return _accounting_policy(accounting)


def execution_policy_for_accounting(
    accounting: dict[str, Any] | None,
    live_policy: StudioFeePolicy | None = None,
) -> StudioFeePolicy:
    """Return the activation-committed execution policy when available."""
    return _live_policy_for_accounting(accounting, live_policy)


def funding_policy_for_accounting(
    accounting: dict[str, Any] | None,
    live_policy: StudioFeePolicy | None = None,
) -> StudioFeePolicy:
    """Return execution prices/config plus the transaction's funding-time split."""
    policy = _live_policy_for_accounting(accounting, live_policy)
    return replace(
        policy,
        time_unit_overlay_bps=int(
            (accounting or {}).get(
                "funding_overlay_bps",
                policy.time_unit_overlay_bps,
            )
        ),
    )


def stamp_receipt_execution_policy(
    receipt: Any,
    accounting: dict[str, Any] | None,
    live_policy: StudioFeePolicy | None = None,
) -> StudioFeePolicy:
    """Persist the exact proposal-time metering inputs on a Studio receipt."""
    policy = execution_policy_for_accounting(accounting, live_policy)
    genvm_result = _receipt_genvm_result(receipt)
    if genvm_result is None:
        genvm_result = {}
        if isinstance(receipt, dict):
            receipt["genvm_result"] = genvm_result
        else:
            setattr(receipt, "genvm_result", genvm_result)
    genvm_result[FEE_POLICY_SNAPSHOT_KEY] = policy.to_snapshot()
    return policy


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


def _require_uint256(value: int) -> int:
    value = int(value)
    if value < 0:
        raise InvalidFeeParams("InvalidFeeParams")
    if value > UINT256_MAX:
        raise ArithmeticOverflow("ArithmeticOverflow")
    return value


def _u256_add(*values: int) -> int:
    total = 0
    for value in values:
        total += _require_uint256(value)
        if total > UINT256_MAX:
            raise ArithmeticOverflow("ArithmeticOverflow")
    return total


def _u256_mul(*values: int) -> int:
    total = 1
    for value in values:
        value = _require_uint256(value)
        if value != 0 and total > UINT256_MAX // value:
            raise ArithmeticOverflow("ArithmeticOverflow")
        total *= value
    return total


def normalize_fees_distribution(
    fees_distribution: dict[str, Any],
) -> dict[str, int | list[int]]:
    normalized = {
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
    for field, value in normalized.items():
        if field == "rotations":
            for rotation in value:
                _require_uint256(rotation)
        else:
            _require_uint256(value)
    return normalized


def get_leader_rounds(fees_distribution: dict[str, Any]) -> int:
    fees = normalize_fees_distribution(fees_distribution)
    rotations = fees["rotations"]
    assert isinstance(rotations, list)
    leader_attempts = 0
    for rotation in rotations:
        leader_attempts = _u256_add(leader_attempts, _u256_add(rotation, 1))
    return _u256_add(
        leader_attempts,
        int(fees["appealRounds"]),
    )


def get_leader_rounds_through_round(
    fees_distribution: dict[str, Any],
    final_round: int,
    consensus_history: dict[str, Any] | None = None,
) -> int:
    fees = normalize_fees_distribution(fees_distribution)
    rotations = fees["rotations"]
    if not isinstance(rotations, list) or len(rotations) == 0:
        raise InvalidAppealRounds("InvalidAppealRounds")

    final_round = max(0, int(final_round))
    actual_rotations = actual_leader_rotations_by_round(consensus_history)
    total = _leader_slots_for_round(rotations, 0, actual_rotations)
    rotations_index = 1
    for offset in range(1, min(final_round, int(fees["appealRounds"]) * 2) + 1):
        if offset % 2 == 1:
            total = _u256_add(total, 1)
        elif rotations_index < len(rotations):
            total = _u256_add(
                total,
                _leader_slots_for_round(
                    rotations, rotations_index, actual_rotations, round_index=offset
                ),
            )
            rotations_index += 1
    return total


def calculate_time_unit_fees_through_round(
    fees_distribution: dict[str, Any],
    num_of_validators: int,
    final_round: int,
    policy: StudioFeePolicy | None = None,
    consensus_history: dict[str, Any] | None = None,
) -> int:
    fees = normalize_fees_distribution(fees_distribution)
    policy = policy or StudioFeePolicy()
    validator_index = _validator_index(num_of_validators)
    rotations = fees["rotations"]
    if not isinstance(rotations, list) or len(rotations) == 0:
        raise InvalidAppealRounds("InvalidAppealRounds")

    capped_final_round = min(max(0, int(final_round)), int(fees["appealRounds"]) * 2)
    max_price = int(fees["maxPriceGenPerTimeUnit"])
    if policy.gen_per_time_unit > 0:
        if max_price > 0 and policy.gen_per_time_unit > max_price:
            raise MaxPriceExceeded("MaxPriceExceeded")
        leader_timeunits = _u256_mul(
            int(fees["leaderTimeunitsAllocation"]), policy.gen_per_time_unit
        )
        validator_timeunits = _u256_mul(
            int(fees["validatorTimeunitsAllocation"]), policy.gen_per_time_unit
        )
    else:
        leader_timeunits = int(fees["leaderTimeunitsAllocation"])
        validator_timeunits = int(fees["validatorTimeunitsAllocation"])
    actual_rotations = actual_leader_rotations_by_round(consensus_history)
    round_outcomes = _round_outcomes(consensus_history)
    total = _calculate_fee_for_round(
        VALIDATORS_PER_ROUND[validator_index],
        _leader_slots_for_round(rotations, 0, actual_rotations),
        leader_timeunits,
        validator_timeunits,
        leader_multiplier=_round_leader_multiplier(round_outcomes.get(0)),
    )
    rotations_index = 1
    for offset in range(1, capped_final_round + 1):
        if offset % 2 == 0 and rotations_index < len(rotations):
            rotations_this_round = _leader_slots_for_round(
                rotations,
                rotations_index,
                actual_rotations,
                round_index=offset,
            )
            rotations_index += 1
        else:
            rotations_this_round = 1
        total = _u256_add(
            total,
            _calculate_fee_for_round(
                _validators_per_round_safe(offset),
                rotations_this_round,
                leader_timeunits,
                validator_timeunits,
                leader_multiplier=_round_leader_multiplier(round_outcomes.get(offset)),
            ),
        )

    return total


def _calculate_settled_time_unit_fees(
    fees_distribution: dict[str, Any],
    num_of_validators: int,
    final_round: int,
    policy: StudioFeePolicy,
    consensus_history: dict[str, Any] | None,
    execution_mode: str = "NORMAL",
    terminal_electorate_size: int | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    """Aggregate the fee-funded round payouts made by FeesProcessor.

    Submission quotes reserve every configured seat and leader attempt. At
    settlement Consensus pays by round type instead: a bare leader timeout
    pays only half a leader allocation, appeal rounds never pay a leader, and
    a successful appeal retroactively skips the appealed round. Prior leader
    rotations pay the non-leader committee plus half of the rotated leader.
    """
    fees = normalize_fees_distribution(fees_distribution)
    _validator_index(num_of_validators)
    logical_entries = logical_fee_round_entries(consensus_history)
    entries = {round_index: entry for round_index, entry in logical_entries}
    if not entries:
        return (
            calculate_time_unit_fees_through_round(
                fees,
                num_of_validators,
                final_round,
                policy,
                consensus_history=consensus_history,
            ),
            [],
        )

    capped_final_round = min(
        max(0, int(final_round)),
        int(fees["appealRounds"]) * 2,
        max(entries),
    )
    outcomes = {
        round_index: str(entry.get("consensus_round") or "")
        for round_index, entry in entries.items()
    }
    previous_completed_round: dict[int, int] = {
        logical_entries[index][0]: logical_entries[index - 1][0]
        for index in range(1, len(logical_entries))
    }
    skipped_rounds = set()
    for round_index, outcome in outcomes.items():
        if outcome not in APPEAL_SUCCESS_ROUNDS:
            continue
        predecessor = previous_completed_round.get(round_index)
        if predecessor is None:
            continue
        # A chained validator appeal is separated by an empty logical round.
        # Consensus leaves the preceding appeal round's fee type intact; only
        # a directly appealed execution round is retroactively skipped.
        if outcomes.get(predecessor) in (
            VALIDATOR_APPEAL_CONSENSUS_ROUNDS | LEADER_APPEAL_CONSENSUS_ROUNDS
        ):
            continue
        # A successful leader replay does not erase a raw deterministic-
        # violation predecessor: those validators correctly established the
        # separate fault and retain their fee treatment on-chain.
        if (
            outcome == ConsensusRound.LEADER_APPEAL_SUCCESSFUL.value
            and _fee_alignment_result(entries.get(predecessor))
            == "deterministic_violation"
        ):
            continue
        skipped_rounds.add(predecessor)
    validator_appeal_outcomes = {
        ConsensusRound.VALIDATOR_APPEAL_SUCCESSFUL.value,
        ConsensusRound.VALIDATOR_APPEAL_FAILED.value,
        ConsensusRound.VALIDATOR_TIMEOUT_APPEAL_SUCCESSFUL.value,
        ConsensusRound.VALIDATORS_TIMEOUT_APPEAL_FAILED.value,
    }
    leader_appeal_outcomes = APPEAL_SUCCESS_ROUNDS.union(APPEAL_FAILED_ROUNDS) - (
        validator_appeal_outcomes
    )
    rotations_by_round = actual_leader_rotations_by_round(consensus_history)
    attempts_by_round = _consensus_attempt_entries_by_round(consensus_history)
    outcomes = _round_outcomes(consensus_history)
    terminal_rounds = {
        round_index
        for round_index, predecessor in previous_completed_round.items()
        if outcomes.get(predecessor)
        in {
            ConsensusRound.VALIDATOR_APPEAL_SUCCESSFUL.value,
            ConsensusRound.VALIDATOR_TIMEOUT_APPEAL_SUCCESSFUL.value,
        }
    }
    if policy.gen_per_time_unit > 0:
        leader_timeunits = _u256_mul(
            int(fees["leaderTimeunitsAllocation"]), policy.gen_per_time_unit
        )
        validator_timeunits = _u256_mul(
            int(fees["validatorTimeunitsAllocation"]), policy.gen_per_time_unit
        )
    else:
        leader_timeunits = int(fees["leaderTimeunitsAllocation"])
        validator_timeunits = int(fees["validatorTimeunitsAllocation"])
    base_total = 0
    by_round: list[dict[str, Any]] = []
    leader_only = str(execution_mode).upper() == "LEADER_ONLY"

    for round_index in range(capped_final_round + 1):
        round_electorate_size = (
            terminal_electorate_size if round_index in terminal_rounds else None
        )
        alignment_result: str | None = None
        outcome = outcomes.get(round_index, "")
        is_leader_appeal_replay = outcome in LEADER_APPEAL_CONSENSUS_ROUNDS
        is_leader_appeal_placeholder = (
            round_index + 1 in outcomes
            and outcomes[round_index + 1] in LEADER_APPEAL_CONSENSUS_ROUNDS
        )
        validators = (
            int(num_of_validators)
            if round_index == 0
            else _validators_per_round_safe(round_index)
        )
        rotations = max(0, int(rotations_by_round.get(round_index, 0)))
        attempts = attempts_by_round.get(round_index, [])
        prior_attempts = attempts[:rotations]
        current_attempt = attempts[-1] if attempts else None
        prior_rotation_fee = 0
        # FeesRecorder keeps a proposal-timeout rotation with an empty
        # validator array. FeesProcessor therefore pays that rotated-out
        # leader one half allocation but pays zero validator seats. Treat an
        # explicitly recorded empty array as material evidence, not as missing
        # history that should be expanded to a full committee.
        for attempt in prior_attempts:
            aligned = _aligned_validator_count(
                attempt,
                exclude_leader=True,
                electorate_size=round_electorate_size,
            )
            rotated_leader_fee = (
                0
                if _fee_alignment_result(attempt, electorate_size=round_electorate_size)
                == "deterministic_violation"
                else leader_timeunits // 2
            )
            prior_rotation_fee += aligned * validator_timeunits + rotated_leader_fee
        # Legacy/synthetic histories can expose only a rotation count. Preserve
        # the configured upper-bound fallback for slots with no attempt record.
        missing_rotation_attempts = max(0, rotations - len(prior_attempts))
        if missing_rotation_attempts:
            prior_rotation_fee += missing_rotation_attempts * _calculate_fee_for_round(
                max(0, validators - 1),
                1,
                leader_timeunits,
                validator_timeunits,
                leader_multiplier=(1, 2),
            )

        if round_index not in entries and not is_leader_appeal_placeholder:
            round_fee = 0
            rule = "empty_round"
        elif is_leader_appeal_placeholder:
            round_fee = 0
            rule = "leader_appeal"
        elif round_index in skipped_rounds:
            round_fee = 0
            rule = "successful_appeal_skip"
        elif is_leader_appeal_replay:
            replay_result = (
                _fee_alignment_result(current_attempt)
                if current_attempt is not None
                else "unknown"
            )
            aligned = (
                _aligned_validator_count(current_attempt)
                if current_attempt is not None
                and _entry_validator_receipts(current_attempt)
                else validators
            )
            if outcome == ConsensusRound.LEADER_TIMEOUT_APPEAL_FAILED.value:
                # The replay timed out again. Its leader is paid from the
                # forfeited appeal bond, not from the sender's time-unit pool.
                round_fee = prior_rotation_fee
                rule = "post_timeout_appeal_repeated_timeout"
            else:
                leader_fee = (
                    0
                    if replay_result == "deterministic_violation"
                    else leader_timeunits
                )
                if (
                    outcome == ConsensusRound.LEADER_TIMEOUT_APPEAL_SUCCESSFUL.value
                    and replay_result != "deterministic_violation"
                ):
                    leader_fee += leader_timeunits // 2
                    rule = "post_timeout_appeal_150_percent"
                elif replay_result == "deterministic_violation":
                    rule = "deterministic_violation_leader_withheld"
                else:
                    rule = "leader_appeal_replay"
                round_fee = (
                    prior_rotation_fee + aligned * validator_timeunits + leader_fee
                )
        elif outcome == ConsensusRound.LEADER_TIMEOUT.value:
            previous = outcomes.get(round_index - 1, "")
            # After a successful leader-timeout appeal, a repeated timeout's
            # leader is paid from the prior appeal bond rather than fee money.
            current_fee = (
                0
                if previous == ConsensusRound.LEADER_TIMEOUT_APPEAL_SUCCESSFUL.value
                else leader_timeunits // 2
            )
            round_fee = prior_rotation_fee + current_fee
            rule = "leader_timeout"
        elif outcome in VALIDATOR_APPEAL_CONSENSUS_ROUNDS:
            if (
                outcome in APPEAL_SUCCESS_ROUNDS
                and current_attempt is not None
                and _entry_validator_receipts(current_attempt)
            ):
                appeal_result = _fee_alignment_result(current_attempt)
                aligned = _aligned_validator_count(
                    current_attempt,
                    forced_result=appeal_result,
                )
                vindicated = 0
                previous_attempts = _previous_non_appeal_attempts(
                    round_index,
                    attempts_by_round,
                    outcomes,
                )
                # Consensus pays vindication only when the successful appeal
                # establishes one of the four concrete result buckets. A
                # NoMajority appeal committee is itself paid equally, but it
                # does not prove any side of the overturned round correct.
                if (
                    appeal_result
                    in {
                        "agree",
                        "disagree",
                        "timeout",
                        "deterministic_violation",
                    }
                    and previous_attempts
                    and _entry_validator_receipts(previous_attempts[-1])
                ):
                    vindicated = _aligned_validator_count(
                        previous_attempts[-1],
                        forced_result=appeal_result,
                    )
                round_fee = (aligned + vindicated) * validator_timeunits
                rule = "validator_appeal_success_with_vindication"
            else:
                # An unsuccessful validator appeal redistributes the complete
                # fee pool for the seats that actually revealed (plus the
                # forfeited bond) among aligned voters. Consensus records
                # validators on reveal, so unrecorded committee seats remain
                # refundable rather than being charged from the sender pool.
                receipts = (
                    _entry_validator_receipts(current_attempt)
                    if current_attempt is not None
                    else []
                )
                revealed = len(receipts) if receipts else validators
                aligned = (
                    _aligned_validator_count(current_attempt)
                    if receipts
                    else validators
                )
                round_fee = revealed * validator_timeunits if aligned > 0 else 0
                rule = "validator_appeal_failed_redistribution"
        elif outcome in leader_appeal_outcomes:
            round_fee = 0
            rule = "leader_appeal"
        elif (
            outcome == ConsensusRound.UNDETERMINED.value
            and current_attempt is not None
            and not _entry_leader_address(current_attempt)
            and not _entry_validator_receipts(current_attempt)
        ):
            # An activation-time committee shortfall materializes an explicit
            # Undetermined round without selecting a leader or validators.
            # Empty participant evidence is authoritative: charge no invented
            # seats, while retaining any real prior-rotation work.
            round_fee = prior_rotation_fee
            rule = "committee_unavailable"
        else:
            current_result = (
                _fee_alignment_result(
                    current_attempt,
                    electorate_size=round_electorate_size,
                )
                if current_attempt is not None
                else "unknown"
            )
            alignment_result = current_result
            leader_fee = (
                0 if current_result == "deterministic_violation" else leader_timeunits
            )
            if (
                outcomes.get(round_index - 1, "")
                == ConsensusRound.LEADER_TIMEOUT_APPEAL_SUCCESSFUL.value
            ):
                if current_result != "deterministic_violation":
                    leader_fee += leader_timeunits // 2
                rule = "post_timeout_appeal_150_percent"
            elif current_result == "deterministic_violation":
                rule = "deterministic_violation_leader_withheld"
            else:
                rule = "normal"
            aligned = (
                _aligned_validator_count(
                    current_attempt,
                    forced_result=current_result,
                    electorate_size=round_electorate_size,
                )
                if current_attempt is not None
                and _entry_validator_receipts(current_attempt)
                else (0 if leader_only else validators)
            )
            round_fee = prior_rotation_fee + aligned * validator_timeunits + leader_fee

        base_total += round_fee
        settlement_round = {
            "round": round_index,
            "outcome": outcome,
            "rule": rule,
            "rotations": rotations,
            "timeUnitAmount": round_fee,
        }
        # Preserve the existing receipt shape for ordinary rounds. The
        # terminal result is material because it was derived against the
        # frozen transaction electorate rather than the local committee.
        if round_electorate_size is not None and alignment_result is not None:
            settlement_round["alignmentResult"] = alignment_result
        by_round.append(settlement_round)

    return base_total, by_round


def _consensus_attempt_entries_by_round(
    consensus_history: dict[str, Any] | None,
) -> dict[int, list[dict[str, Any]]]:
    if not isinstance(consensus_history, dict):
        return {}
    results = consensus_history.get("consensus_results")
    if not isinstance(results, list):
        return {}

    grouped: dict[int, list[dict[str, Any]]] = {}
    pending: list[dict[str, Any]] = []
    previous_round: int | None = None
    for entry in results:
        if not isinstance(entry, dict):
            continue
        outcome = str(entry.get("consensus_round") or "")
        if outcome in NON_ROUND_CONSENSUS_EVENTS:
            pending.append(entry)
            continue
        if previous_round is None:
            round_index = 0
        elif outcome in LEADER_APPEAL_CONSENSUS_ROUNDS:
            round_index = previous_round + 2
        elif outcome in VALIDATOR_APPEAL_CONSENSUS_ROUNDS:
            round_index = previous_round + (1 if previous_round % 2 == 0 else 2)
        else:
            round_index = previous_round + 1
        grouped[round_index] = [*pending, entry]
        pending = []
        previous_round = round_index
    return grouped


def _previous_non_appeal_attempts(
    round_index: int,
    attempts_by_round: dict[int, list[dict[str, Any]]],
    outcomes: dict[int, str],
) -> list[dict[str, Any]]:
    """Mirror FeesProcessor._findPreviousOriginalRound for vindication."""
    appeal_outcomes = VALIDATOR_APPEAL_CONSENSUS_ROUNDS | LEADER_APPEAL_CONSENSUS_ROUNDS
    for candidate in sorted(
        (index for index in attempts_by_round if index < int(round_index)),
        reverse=True,
    ):
        if outcomes.get(candidate) in appeal_outcomes:
            continue
        attempts = attempts_by_round.get(candidate) or []
        if attempts:
            return attempts
    return []


def _entry_validator_receipts(entry: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(entry, dict):
        return []
    receipts: list[dict[str, Any]] = []
    for key in ("leader_result", "validator_results"):
        value = entry.get(key)
        candidates = value if isinstance(value, list) else [value]
        for receipt in candidates:
            if not isinstance(receipt, dict):
                continue
            mode = receipt.get("mode")
            if hasattr(mode, "value"):
                mode = mode.value
            if str(mode or "").lower() == "validator":
                receipts.append(receipt)
    return receipts


def _receipt_validator_address(receipt: dict[str, Any]) -> str:
    node_config = receipt.get("node_config") or receipt.get("nodeConfig") or {}
    if not isinstance(node_config, dict):
        return ""
    return str(node_config.get("address") or "").lower()


def _entry_leader_address(entry: dict[str, Any] | None) -> str:
    if not isinstance(entry, dict):
        return ""
    value = entry.get("leader_result")
    candidates = value if isinstance(value, list) else [value]
    for receipt in candidates:
        if not isinstance(receipt, dict):
            continue
        mode = receipt.get("mode")
        if hasattr(mode, "value"):
            mode = mode.value
        if str(mode or "").lower() == "leader":
            return _receipt_validator_address(receipt)
    return ""


def _fee_alignment_result(
    entry: dict[str, Any] | None,
    *,
    electorate_size: int | None = None,
) -> str:
    votes = [
        str(receipt.get("vote") or "").lower()
        for receipt in _entry_validator_receipts(entry)
    ]
    total = len(votes)
    if total == 0:
        return "unknown"
    threshold_base = (
        total if electorate_size is None else max(total, int(electorate_size))
    )
    majority = threshold_base / 2
    if votes.count("agree") > majority:
        return "agree"
    if votes.count("disagree") > majority:
        return "disagree"
    # RevealingState classifies Studio's local IDLE sentinel as the on-chain
    # Timeout ballot before emitting/recording it. Fee alignment must derive
    # the result from that classified ballot too, not from Studio's separate
    # state-transition convention where IDLE contributes to disagreement.
    if votes.count("timeout") + votes.count("idle") > majority:
        return "timeout"
    if votes.count("deterministic_violation") > majority:
        return "deterministic_violation"
    return "no_majority"


def _aligned_validator_count(
    entry: dict[str, Any] | None,
    *,
    exclude_leader: bool = False,
    forced_result: str | None = None,
    electorate_size: int | None = None,
) -> int:
    receipts = _entry_validator_receipts(entry)
    if exclude_leader:
        leader = _entry_leader_address(entry)
        removed = False
        filtered: list[dict[str, Any]] = []
        for receipt in receipts:
            if not removed and leader and _receipt_validator_address(receipt) == leader:
                removed = True
                continue
            filtered.append(receipt)
        receipts = filtered

    result = forced_result or _fee_alignment_result(
        entry, electorate_size=electorate_size
    )
    if result == "no_majority":
        return len(receipts)
    if result == "unknown":
        return len(receipts)

    aligned = 0
    for receipt in receipts:
        vote = str(receipt.get("vote") or "").lower()
        # Studio emits an IDLE reveal to Consensus as Timeout. Its local state
        # transition tally treats IDLE as disagreement, but strict fee
        # alignment uses the classified on-chain ballot.
        if vote == result or (result == "timeout" and vote == "idle"):
            aligned += 1
    return aligned


def _settlement_storage_recipient_count(
    consensus_history: dict[str, Any] | None,
    settlement_rounds: list[dict[str, Any]],
    fees_distribution: dict[str, Any],
    policy: StudioFeePolicy,
    execution_mode: str,
    bond_settlements: list[dict[str, Any]] | None = None,
    terminal_electorate_size: int | None = None,
) -> int:
    """Count the unique fee-ledger recipients that share persistent storage.

    Consensus distributes storage over FeesProcessor's tracked validator set,
    not the configured committee size. Rebuild the same tracking side effects:
    paid recipients and nonzero penalties are tracked, as is a withheld DV
    leader, while skipped/placeholder rounds and zero-value entries add nobody.
    """
    attempts_by_round = _consensus_attempt_entries_by_round(consensus_history)
    outcomes = _round_outcomes(consensus_history)
    settlement_by_round = {
        int(item.get("round", 0)): item for item in settlement_rounds
    }
    non_material_rules = {
        "empty_round",
        "successful_appeal_skip",
        "leader_appeal",
    }
    normal_rules = {
        "normal",
        "deterministic_violation_leader_withheld",
        "leader_appeal_replay",
        "post_timeout_appeal_150_percent",
    }
    leader_timeout_rules = {
        "leader_timeout",
        "post_timeout_appeal_repeated_timeout",
    }
    fees = normalize_fees_distribution(fees_distribution)
    price = int(policy.gen_per_time_unit)
    validator_value = int(fees["validatorTimeunitsAllocation"]) * (
        price if price > 0 else 1
    )
    leader_value = int(fees["leaderTimeunitsAllocation"]) * (price if price > 0 else 1)
    leader_only = str(execution_mode).upper() == "LEADER_ONLY"
    recipients: set[str] = set()

    def bond_distribution_for_round(round_index: int, key: str) -> int:
        return sum(
            max(0, int(item.get(key, 0) or 0))
            for item in (bond_settlements or [])
            if item.get("outcomeRound") is not None
            and int(item["outcomeRound"]) == int(round_index)
        )

    def add_leader(
        attempt: dict[str, Any],
        *,
        payable_value: int,
        electorate_size: int | None = None,
    ) -> None:
        leader = _entry_leader_address(attempt)
        if leader and (
            payable_value > 0
            or _fee_alignment_result(attempt, electorate_size=electorate_size)
            == "deterministic_violation"
        ):
            recipients.add(leader)

    def validator_addresses(
        attempt: dict[str, Any],
        *,
        aligned_only: bool = False,
        exclude_leader: bool = False,
        forced_result: str | None = None,
        electorate_size: int | None = None,
    ) -> list[str]:
        receipts = _entry_validator_receipts(attempt)
        if exclude_leader:
            leader = _entry_leader_address(attempt)
            removed = False
            filtered: list[dict[str, Any]] = []
            for receipt in receipts:
                if (
                    not removed
                    and leader
                    and _receipt_validator_address(receipt) == leader
                ):
                    removed = True
                    continue
                filtered.append(receipt)
            receipts = filtered
        result = forced_result or _fee_alignment_result(
            attempt, electorate_size=electorate_size
        )
        addresses: list[str] = []
        for receipt in receipts:
            vote = str(receipt.get("vote") or "").lower()
            aligned = (
                result in {"no_majority", "unknown"}
                or vote == result
                or (result == "timeout" and vote == "idle")
            )
            if aligned_only and not aligned:
                continue
            address = _receipt_validator_address(receipt)
            if address:
                addresses.append(address)
        return addresses

    for round_index, attempts in attempts_by_round.items():
        settlement = settlement_by_round.get(round_index) or {}
        rule = str(settlement.get("rule") or "")
        outcome = outcomes.get(round_index, "")
        if rule in non_material_rules:
            continue

        current_attempt = attempts[-1] if attempts else None
        rotation_attempts = attempts[:-1] if attempts else []
        predecessor = max(
            (candidate for candidate in outcomes if candidate < round_index),
            default=None,
        )
        round_electorate_size = (
            terminal_electorate_size
            if predecessor is not None
            and outcomes.get(predecessor)
            in {
                ConsensusRound.VALIDATOR_APPEAL_SUCCESSFUL.value,
                ConsensusRound.VALIDATOR_TIMEOUT_APPEAL_SUCCESSFUL.value,
            }
            else None
        )
        if int(settlement.get("rotations", 0) or 0) > 0:
            for attempt in rotation_attempts:
                add_leader(
                    attempt,
                    payable_value=leader_value // 2,
                    electorate_size=round_electorate_size,
                )
                if validator_value > 0:
                    recipients.update(
                        validator_addresses(
                            attempt,
                            exclude_leader=True,
                            electorate_size=round_electorate_size,
                        )
                    )

        if current_attempt is None:
            continue
        if rule in normal_rules:
            alignment_result = settlement.get("alignmentResult")
            add_leader(
                current_attempt,
                payable_value=leader_value,
                electorate_size=round_electorate_size,
            )
            if validator_value > 0 and not leader_only:
                recipients.update(
                    validator_addresses(
                        current_attempt,
                        forced_result=(
                            str(alignment_result) if alignment_result else None
                        ),
                        electorate_size=round_electorate_size,
                    )
                )
            elif outcome == ConsensusRound.LEADER_APPEAL_FAILED.value:
                # A failed leader appeal redistributes its forfeited bond to
                # aligned replay voters. Even when validator time-unit work is
                # explicitly zero, recipients enter FeesProcessor's index only
                # when integer division actually gives them nonzero principal.
                if bond_distribution_for_round(round_index, "bondDistributed") > 0:
                    recipients.update(
                        validator_addresses(
                            current_attempt,
                            aligned_only=True,
                            forced_result=(
                                str(alignment_result) if alignment_result else None
                            ),
                            electorate_size=round_electorate_size,
                        )
                    )
        elif rule in leader_timeout_rules:
            payable_value = (
                bond_distribution_for_round(round_index, "leaderPayout")
                if rule == "post_timeout_appeal_repeated_timeout"
                else leader_value // 2
            )
            add_leader(current_attempt, payable_value=payable_value)
        elif rule == "validator_appeal_success_with_vindication":
            if validator_value > 0:
                recipients.update(validator_addresses(current_attempt))
        elif rule == "validator_appeal_failed_redistribution":
            # With a nonzero validator allocation every revealer is either
            # paid or penalized. If it is zero, only aligned recipients of the
            # forfeited bond enter the distribution index.
            if (
                validator_value > 0
                or bond_distribution_for_round(round_index, "bondDistributed") > 0
            ):
                recipients.update(
                    validator_addresses(
                        current_attempt,
                        aligned_only=validator_value == 0,
                    )
                )

    # A successful validator appeal also tracks each vindicated voter from the
    # skipped original round, even when that address is not on the appeal jury.
    for item in settlement_rounds:
        if item.get("rule") != "validator_appeal_success_with_vindication":
            continue
        round_index = int(item.get("round", 0))
        appeal_attempts = attempts_by_round.get(round_index) or []
        original_attempts = _previous_non_appeal_attempts(
            round_index,
            attempts_by_round,
            outcomes,
        )
        if not appeal_attempts or not original_attempts:
            continue
        result = _fee_alignment_result(appeal_attempts[-1])
        if validator_value <= 0 or result not in {
            "agree",
            "disagree",
            "timeout",
            "deterministic_violation",
        }:
            continue
        recipients.update(
            validator_addresses(
                original_attempts[-1],
                aligned_only=True,
                forced_result=result,
            )
        )

    return len(recipients)


def calculate_round_fees(
    fees_distribution: dict[str, Any],
    num_of_validators: int,
    round: int = 0,
    policy: StudioFeePolicy | None = None,
    *,
    enforce_gen_price_cap: bool = False,
) -> int:
    fees = normalize_fees_distribution(fees_distribution)
    policy = policy or StudioFeePolicy()

    if round == 0:
        time_unit_work = _calculate_initial_round_total(fees, num_of_validators)
        appeal_profit_reserve = _calculate_appeal_profit_reserve(
            fees,
            gen_per_time_unit=int(fees["maxPriceGenPerTimeUnit"]),
        )
    else:
        time_unit_work = _calculate_appeal_round_total(fees, round)
        appeal_profit_reserve = 0

    max_price = int(fees["maxPriceGenPerTimeUnit"])
    priced_work = _apply_time_unit_price(
        time_unit_work,
        max_price,
        policy,
        enforce_cap=enforce_gen_price_cap,
    )
    total = _u256_add(
        priced_work,
        _time_unit_overlay(priced_work, policy.time_unit_overlay_bps),
        appeal_profit_reserve,
    )
    _enforce_gas_price_cap(
        policy.storage_unit_price, int(fees["storageFeeMaxGasPrice"])
    )
    _enforce_gas_price_cap(policy.receipt_gas_price, int(fees["receiptFeeMaxGasPrice"]))

    if round == 0:
        total = _u256_add(
            total,
            _u256_mul(int(fees["executionBudgetPerRound"]), get_leader_rounds(fees)),
        )

    return total


def required_fee_deposit(
    fees_distribution: dict[str, Any],
    num_of_validators: int,
    policy: StudioFeePolicy | None = None,
) -> int:
    fees = normalize_fees_distribution(fees_distribution)
    return _u256_add(
        calculate_round_fees(fees, num_of_validators, 0, policy),
        int(fees["totalMessageFees"]),
    )


def default_transaction_fees_for_policy(
    policy: StudioFeePolicy | None = None,
) -> tuple[dict[str, int | list[int]], int]:
    policy = policy or StudioFeePolicy()
    enabled = policy.fee_accounting_enabled()
    execution_budget_per_round = (
        max(
            DEFAULT_TRANSACTION_EXECUTION_BUDGET_PER_ROUND,
            policy.message_fee_params_budget_floor(),
        )
        if enabled
        else 0
    )
    distribution = _serializable_fees_distribution(
        {
            "leaderTimeunitsAllocation": (
                DEFAULT_LEADER_TIMEUNITS_ALLOCATION if enabled else 0
            ),
            "validatorTimeunitsAllocation": (
                DEFAULT_VALIDATOR_TIMEUNITS_ALLOCATION if enabled else 0
            ),
            "appealRounds": 0,
            "executionBudgetPerRound": execution_budget_per_round,
            "executionConsumed": 0,
            "totalMessageFees": 0,
            "rotations": [0],
            "maxPriceGenPerTimeUnit": (
                max(1, _with_cap_headroom(policy.gen_per_time_unit)) if enabled else 0
            ),
            "storageFeeMaxGasPrice": (
                max(1, _with_cap_headroom(policy.storage_unit_price)) if enabled else 0
            ),
            "receiptFeeMaxGasPrice": (
                max(1, _with_cap_headroom(policy.receipt_gas_price)) if enabled else 0
            ),
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
            "genvmStartBudgetFloor": str(policy.genvm_start_budget_floor()),
            "maxAllocationTreeDepth": str(policy.max_allocation_tree_depth),
            "maxAllocatedMessages": str(policy.max_allocated_messages),
            "maxMessagesPerTx": str(policy.max_messages_per_tx),
            "minExternalGasLimit": str(policy.min_external_gas_limit),
            "defaultExternalGasLimit": str(policy.default_external_gas_limit),
            "maxEqOutputsBytes": str(policy.max_eq_outputs_bytes),
            "maxSubmittedMessagesBytes": str(policy.max_submitted_messages_bytes),
            "timeUnitOverlayBps": str(policy.time_unit_overlay_bps),
            "minProposeTimeunits": str(policy.min_propose_timeunits),
            "maxProposeTimeunits": str(policy.max_propose_timeunits),
            "minCommitTimeunits": str(policy.min_commit_timeunits),
            "maxCommitTimeunits": str(policy.max_commit_timeunits),
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


def _require_nonzero_fee_config(fees_distribution: dict[str, Any]) -> None:
    # ConsensusHelpers.requireNonZeroFeeConfig assigns these stable field
    # indices to the six mandatory fields on every fee-aware user submission.
    required_fields = (
        (1, "leaderTimeunitsAllocation"),
        (2, "validatorTimeunitsAllocation"),
        (3, "executionBudgetPerRound"),
        (4, "maxPriceGenPerTimeUnit"),
        (5, "storageFeeMaxGasPrice"),
        (6, "receiptFeeMaxGasPrice"),
    )
    for field_index, field_name in required_fields:
        if int(fees_distribution[field_name]) <= 0:
            raise FeeValueMustBeNonZero(f"FeeValueMustBeNonZero({field_index})")


def _validate_phase_timeout_bounds(
    leader_timeunits: int,
    validator_timeunits: int,
    policy: StudioFeePolicy,
    *,
    allow_zero: bool = False,
) -> None:
    checks = (
        (
            int(leader_timeunits),
            int(policy.min_propose_timeunits),
            int(policy.max_propose_timeunits),
        ),
        (
            int(validator_timeunits),
            int(policy.min_commit_timeunits),
            int(policy.max_commit_timeunits),
        ),
    )
    for value, minimum, maximum in checks:
        if allow_zero and value == 0:
            continue
        if value < minimum or value > maximum:
            raise PhaseTimeoutOutOfBounds(
                f"PhaseTimeoutOutOfBounds({value},{minimum},{maximum})"
            )


def validate_transaction_fee_deposit(
    *,
    fees_distribution: dict[str, Any],
    message_allocations: list[dict[str, Any]] | None = None,
    num_of_validators: int,
    submitted_value: int,
    user_value: int,
    policy: StudioFeePolicy | None = None,
    allow_low_execution_budget: bool = False,
) -> int:
    policy = policy or StudioFeePolicy()
    fees = normalize_fees_distribution(fees_distribution)
    if policy.fee_accounting_enabled() and policy.enforce_v06_submission_config:
        _require_nonzero_fee_config(fees)
        _validate_phase_timeout_bounds(
            int(fees["leaderTimeunitsAllocation"]),
            int(fees["validatorTimeunitsAllocation"]),
            policy,
        )
    execution_budget_per_round = int(fees["executionBudgetPerRound"])
    if (
        not allow_low_execution_budget
        and execution_budget_per_round > 0
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
    allow_low_execution_budget: bool = False,
) -> dict[str, Any]:
    policy = policy or StudioFeePolicy()
    required = validate_transaction_fee_deposit(
        fees_distribution=fees_distribution,
        message_allocations=message_allocations or [],
        num_of_validators=num_of_validators,
        submitted_value=submitted_value,
        user_value=user_value,
        policy=policy,
        allow_low_execution_budget=allow_low_execution_budget,
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


def activate_fee_accounting(
    accounting: dict[str, Any],
    policy: StudioFeePolicy | None = None,
    *,
    selection_pool_count: int | None = None,
    selection_pool_addresses: list[str] | None = None,
) -> tuple[dict[str, Any], bool]:
    """Validate ceilings and commit the complete v0.6 fee policy at activation."""
    updated = copy.deepcopy(accounting)
    if updated.get("activation_prices_locked"):
        return updated, False

    if selection_pool_addresses is not None:
        # Consensus pins the activation selection-pool authority, including
        # identities. Later registry additions must not become eligible for an
        # already-activated transaction.
        frozen_addresses = list(
            dict.fromkeys(
                str(address).lower() for address in selection_pool_addresses if address
            )
        )
        updated["selection_pool_addresses"] = frozen_addresses
        updated["selection_pool_count"] = len(frozen_addresses)
    elif selection_pool_count is not None:
        # Consensus appeal reservations use the activation epoch's frozen
        # selection-pool authority, never the live staking population.
        updated["selection_pool_count"] = max(0, int(selection_pool_count))

    live_policy = policy or StudioFeePolicy.from_env()
    fees = normalize_fees_distribution(updated.get("fees_distribution") or {})
    cap_checks = (
        (
            "genPerTimeUnit",
            int(live_policy.gen_per_time_unit),
            int(fees["maxPriceGenPerTimeUnit"]),
        ),
        (
            "storageUnitPrice",
            int(live_policy.storage_unit_price),
            int(fees["storageFeeMaxGasPrice"]),
        ),
        (
            "receiptGasPrice",
            int(live_policy.receipt_gas_price),
            int(fees["receiptFeeMaxGasPrice"]),
        ),
    )
    for cap_type, actual, maximum in cap_checks:
        if maximum > 0 and actual > maximum:
            updated["activation_price_cap_exceeded"] = {
                "actual": actual,
                "maximum": maximum,
            }
            updated["activation_price_cap_type"] = cap_type
            updated["activation_cancel_reason"] = (
                "activation_price_cap_exceeded:" + cap_type
            )
            return updated, True

    execution_budget_per_round = int(fees["executionBudgetPerRound"])
    activation_floor = int(live_policy.message_fee_params_budget_floor())
    if execution_budget_per_round > 0 and execution_budget_per_round < activation_floor:
        updated["activation_budget_floor_not_met"] = {
            "actual": execution_budget_per_round,
            "minimum": activation_floor,
        }
        updated["activation_cancel_reason"] = "activation_budget_floor_not_met"
        return updated, True

    # Receipt execution inputs are activation-pinned. The overlay split is a
    # funding-ownership term and was already committed when escrow began.
    locked_policy = replace(
        live_policy,
        time_unit_overlay_bps=int(
            updated.get("funding_overlay_bps", live_policy.time_unit_overlay_bps)
        ),
    )
    updated["policy_snapshot"] = locked_policy.to_snapshot()
    updated["activation_prices_locked"] = True
    updated["locked_prices"] = {
        "genPerTimeUnit": int(live_policy.gen_per_time_unit),
        "storageUnitPrice": int(live_policy.storage_unit_price),
        "receiptGasPrice": int(live_policy.receipt_gas_price),
    }
    return updated, False


def create_child_fee_accounting(
    *,
    message: dict[str, Any],
    parent_fees_distribution: dict[str, Any] | None,
    message_allocations: list[dict[str, Any]] | str | None = None,
    sender: str | None = None,
    policy: StudioFeePolicy | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    policy = policy or StudioFeePolicy()
    declared_budget = int(message.get("declaredBudget", 0) or 0)
    # Consensus permits a zero-budget child when its complete calculated
    # primary obligation and allocation subtree are also zero. Negative
    # values are impossible in the ABI and remain invalid locally.
    if declared_budget < 0:
        raise MessageDeclaredBudgetInsufficient("MessageDeclaredBudgetInsufficient")

    fee_params = decode_internal_message_fee_params(message.get("feeParams", b""))
    _validate_internal_message_price_caps(fee_params)
    _validate_phase_timeout_bounds(
        int(fee_params["leaderTimeunitsAllocation"]),
        int(fee_params["validatorTimeunitsAllocation"]),
        policy,
        allow_zero=True,
    )
    funding_child_fees = _fees_distribution_from_internal_params(
        fee_params,
        total_message_fees=0,
        parent_fees_distribution=normalize_fees_distribution({}),
    )
    # Consensus prices child funding at the child's declared GEN ceiling. The
    # live GEN price is checked only when the child activates, while storage
    # and receipt ceilings guard the child's later work and therefore do not
    # participate in this floor calculation.
    funding_child_fees["storageFeeMaxGasPrice"] = 0
    funding_child_fees["receiptFeeMaxGasPrice"] = 0
    execution_budget_per_round = int(funding_child_fees["executionBudgetPerRound"])
    if (
        execution_budget_per_round > 0
        and execution_budget_per_round < policy.message_fee_params_budget_floor()
    ):
        raise BudgetTooLow("BudgetTooLow")
    child_primary = calculate_round_fees(
        funding_child_fees,
        VALIDATORS_PER_ROUND[0],
        0,
        policy,
        enforce_gen_price_cap=False,
    )
    if declared_budget < child_primary:
        raise MessageDeclaredBudgetInsufficient("MessageDeclaredBudgetInsufficient")

    parent_fees = (
        normalize_fees_distribution(parent_fees_distribution)
        if parent_fees_distribution
        else normalize_fees_distribution({})
    )
    # Old GenVM receipts may expose the allocation subtree as its encoded hex
    # form. Preserve that raw value in the receipt/hash path, but never iterate
    # its characters as FlatArrays allocation nodes here.
    allocation_nodes = (
        message_allocations if isinstance(message_allocations, list) else []
    )
    child_message_allocations = _child_allocations_from_message_subtree(
        message,
        allocation_nodes,
    )
    # Mode 1 children have no allocation subtree but still receive the remainder
    # of their declared budget as a message-fee bucket for their own children.
    child_message_budget = declared_budget - child_primary
    child_fees = _fees_distribution_from_internal_params(
        fee_params,
        total_message_fees=child_message_budget,
        parent_fees_distribution=parent_fees,
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
) -> tuple[dict[str, int] | None, dict[str, str] | None]:
    if not accounting:
        return None, None

    policy = execution_policy_for_accounting(accounting, policy)
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
        "lockedReceiptGasPrice": str(policy.receipt_gas_price),
        "receiptWrapperBytes": str(policy.receipt_wrapper_bytes),
        "overlaySplitBps": str(policy.time_unit_overlay_bps),
        "minProposeTimeout": str(policy.min_propose_timeunits),
        "maxProposeTimeout": str(policy.max_propose_timeunits),
        "minCommitTimeout": str(policy.min_commit_timeunits),
        "maxCommitTimeout": str(policy.max_commit_timeunits),
        "genPerTimeUnit": str(policy.gen_per_time_unit),
        "messageBudgetFloor": str(policy.message_fee_params_budget_floor()),
    }
    message_bucket_total = int(accounting.get("message_fee_budget", 0) or 0)
    has_fee_budgets = bucket_total > 0 or message_bucket_total > 0
    data_bucket_total = (
        bucket_total if bucket_total > 0 else GENVM_UNMETERED_DATA_FEE_BUCKET
    )
    bucket_totals = {
        "execution_data_gas": data_bucket_total,
        "message_fee": (
            message_bucket_total if has_fee_budgets else GENVM_UNMETERED_DATA_FEE_BUCKET
        ),
        "nondet_outputs": GENVM_UNMETERED_DATA_FEE_BUCKET,
        "submitted_messages": GENVM_UNMETERED_DATA_FEE_BUCKET,
        "submitted_messages_count": _message_count_cap(policy),
    }
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
                "Mode1MessageFeesRequireGenVMPerEmissionSupport: fee-bearing "
                "GenVM messages require a message allocation tree"
            )
        return _genvm_unmetered_message_fee_allocation()

    fees_distribution = normalize_fees_distribution(
        accounting.get("fees_distribution") or {}
    )
    studio_nodes = [
        _serializable_message_allocation(raw_node)
        for raw_node in accounting.get("message_allocations") or []
    ]
    genvm_nodes = [
        _genvm_message_allocation_node(
            node,
            address_factory,
            fees_distribution,
        )
        for node in studio_nodes
    ]
    roots: list[dict[str, Any]] = []
    for index, node in enumerate(studio_nodes):
        parent_index = int(node["parentIndex"])
        if parent_index == NODE_ROOT_SENTINEL:
            roots.append(genvm_nodes[index])
            continue
        if 0 <= parent_index < len(genvm_nodes):
            genvm_nodes[parent_index]["children"].append(genvm_nodes[index])

    return roots


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
    # After activation the complete fee formula and its budget floor are part
    # of the transaction's committed economics.
    policy = funding_policy_for_accounting(accounting, policy)
    amount = int(amount)
    if amount <= 0:
        raise InsufficientFees("InsufficientFees")
    incoming = normalize_fees_distribution(fees_distribution)
    cap_owner = accounting.get("sender")
    if sender and cap_owner and str(sender).lower() != str(cap_owner).lower():
        # Any account may fund an in-flight transaction, but Consensus pins
        # price-cap authority to the first depositor.
        for cap in (
            "maxPriceGenPerTimeUnit",
            "storageFeeMaxGasPrice",
            "receiptFeeMaxGasPrice",
        ):
            incoming[cap] = 0
    incoming_message_fees = int(incoming["totalMessageFees"])
    if incoming_message_fees > 0 and bool(
        accounting.get("message_allocations_restricted", False)
    ):
        raise MessageAllocationsRestricted("MessageAllocationsRestricted")
    if incoming_message_fees > amount:
        raise InsufficientFees("InsufficientFees")

    primary_amount = amount - incoming_message_fees
    updated = copy.deepcopy(accounting)
    current = normalize_fees_distribution(updated.get("fees_distribution") or {})
    merged = merge_fees_distribution(current, incoming)
    if perform_fee_checks:
        quote_before = (
            calculate_round_fees(
                current,
                num_of_validators,
                0,
                policy,
                enforce_gen_price_cap=False,
            )
            if current["rotations"]
            else 0
        )
        quote_after = calculate_round_fees(
            merged,
            num_of_validators,
            0,
            policy,
            enforce_gen_price_cap=False,
        )
        if quote_after - quote_before > primary_amount:
            raise InsufficientFees("InsufficientFeesForRound")

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
    current_overlay_reserve = int(updated.get("time_unit_overlay_budget", 0) or 0)
    updated["time_unit_overlay_budget"] = _carve_time_unit_overlay_reserve(
        current_reserve=current_overlay_reserve,
        cumulative_primary=int(updated["primary_fee_budget"]),
        execution_budget=int(updated["execution_budget_total"]),
        incoming_primary=primary_amount,
        split_bps=policy.time_unit_overlay_bps,
    )
    overlay_reserve_delta = (
        int(updated["time_unit_overlay_budget"]) - current_overlay_reserve
    )
    _record_fee_contribution(
        updated,
        sender,
        primary=primary_amount - overlay_reserve_delta,
        overlay=overlay_reserve_delta,
        message=incoming_message_fees,
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
    terminal_committee_upper_bound: int | None = None,
    available_appeal_validators: int | None = None,
    replacement_rotations: int | None = None,
    leader_timeout_live_seats: int | None = None,
    time_unit_overlay_bps: int | None = None,
    policy: StudioFeePolicy | None = None,
) -> dict[str, Any]:
    updated = copy.deepcopy(accounting)
    admission_rollback = {
        key: copy.deepcopy(updated.get(key))
        for key in (
            "fees_distribution",
            "execution_budget_total",
            "primary_fee_budget",
            "paid_fee_value",
            "appeal_funding_total",
            "time_unit_overlay_budget",
            "appeal_bonds_total",
            "contributions",
            "untracked_contributions",
            "untracked_contribution_pools",
        )
    }
    policy = funding_policy_for_accounting(updated, policy)
    amount = int(amount)

    if status is None:
        raise InvalidAppealBond("InvalidAppealBond")
    charge = calculate_appeal_charge(
        updated.get("fees_distribution") or {},
        current_round=current_round,
        status=status,
        terminal_committee_upper_bound=terminal_committee_upper_bound,
        available_appeal_validators=available_appeal_validators,
        replacement_rotations=replacement_rotations,
        leader_timeout_live_seats=leader_timeout_live_seats,
        policy=policy,
    )
    bond = int(charge["bond"])
    funding = int(charge["funding"])
    total_required = bond + funding
    if (
        str(status).upper() == "LEADER_TIMEOUT"
        and leader_timeout_live_seats is not None
        and int(leader_timeout_live_seats) <= 1
    ):
        raise InvalidAppealBond("InvalidAppealBond")
    # Zero is a valid economic bond when an internal child explicitly opts
    # out of both phase-time allocations. Consensus still admits that appeal
    # (and records zero-valued custody); only an underpayment is invalid.
    if bond < 0 or amount < bond:
        raise InvalidAppealBond("InvalidAppealBond")
    if amount < total_required:
        raise InsufficientFees("InsufficientFees")

    if bool(charge["extendsSchedule"]):
        merged = normalize_fees_distribution(updated.get("fees_distribution") or {})
        merged["appealRounds"] = int(merged["appealRounds"]) + 1
        rotations = list(merged["rotations"])
        while len(rotations) < int(merged["appealRounds"]) + 1:
            rotations.append(int(charge["replacementRotations"]))
        merged["rotations"] = rotations
        updated["fees_distribution"] = merged
        updated["execution_budget_total"] = int(
            merged["executionBudgetPerRound"]
        ) * get_leader_rounds(merged)

    # Bond principal stays in its own custody. Only the typed funding leg is
    # spendable fee budget; this prevents topUpAndSubmitAppeal from counting
    # the same payment as both bond and primary fees.
    updated["primary_fee_budget"] = int(updated.get("primary_fee_budget", 0)) + funding
    updated["paid_fee_value"] = int(updated.get("paid_fee_value", 0)) + funding
    updated["appeal_funding_total"] = (
        int(updated.get("appeal_funding_total", 0)) + funding
    )
    updated["time_unit_overlay_budget"] = int(
        updated.get("time_unit_overlay_budget", 0) or 0
    ) + int(charge["fundingBreakdown"]["overlay"])
    _record_fee_contribution(
        updated,
        appealer,
        primary=funding - int(charge["fundingBreakdown"]["overlay"]),
        overlay=int(charge["fundingBreakdown"]["overlay"]),
    )
    updated["appeal_bonds_total"] = int(updated.get("appeal_bonds_total", 0)) + bond
    surplus = amount - total_required
    updated["appeal_charge_surplus_refunded"] = (
        int(updated.get("appeal_charge_surplus_refunded", 0)) + surplus
    )
    updated.setdefault("appeal_bonds", []).append(
        {
            "appealer": appealer,
            "amount": bond,
            "submittedAmount": amount,
            "funding": funding,
            "fundingBreakdown": charge["fundingBreakdown"],
            "requiredCharge": total_required,
            "surplusRefund": surplus,
            # Keep the legacy source-round field, and also persist the raw
            # round that Consensus creates at admission. The RPC compatibility
            # view needs the latter to expose RoundData.appealBond correctly.
            "round": current_round if round is None else round,
            "sourceRound": current_round,
            "appealRound": int(charge["appealRound"]),
            "juryCount": int(charge["juryCount"]),
            "status": status,
            "minimumRequired": bond,
            "topUpAndSubmit": bool(top_up_and_submit),
            "feesDistributionIgnored": fees_distribution is not None,
            "extendsSchedule": bool(charge["extendsSchedule"]),
            # Studio forms the actual jury asynchronously. If the frozen/live
            # pool changes before the worker can start, Consensus would have
            # reverted the atomic submission. Preserve the exact pre-admission
            # accounting state so Studio can make that failure equally
            # non-value-bearing instead of silently forfeiting the bond.
            "admissionRollback": admission_rollback,
        }
    )
    _refresh_message_fee_accounting_report_if_present(updated, policy)
    return updated


def abort_latest_appeal_admission(
    accounting: dict[str, Any],
    *,
    reason: str,
) -> tuple[dict[str, Any], str | None, int]:
    """Undo an admitted appeal that never formed a committee."""

    updated = copy.deepcopy(accounting)
    bonds = updated.get("appeal_bonds")
    if not isinstance(bonds, list) or not bonds:
        return updated, None, 0
    bond = bonds[-1]
    if not isinstance(bond, dict):
        return updated, None, 0
    rollback = bond.get("admissionRollback")
    if not isinstance(rollback, dict):
        return updated, None, 0

    recipient = bond.get("appealer")
    refund = max(0, int(bond.get("amount", 0) or 0)) + max(
        0, int(bond.get("funding", 0) or 0)
    )
    for key, value in rollback.items():
        updated[key] = copy.deepcopy(value)
    updated["appeal_bonds"] = bonds[:-1]
    updated.setdefault("aborted_appeals", []).append(
        {
            "appealer": recipient,
            "amount": int(bond.get("amount", 0) or 0),
            "funding": int(bond.get("funding", 0) or 0),
            "sourceRound": int(bond.get("sourceRound", 0) or 0),
            "reason": str(reason),
            "refund": refund,
        }
    )
    updated["total_refunded"] = int(updated.get("total_refunded", 0) or 0) + refund
    updated.setdefault("refunds", []).append(
        {
            "reason": str(reason),
            "primary": int(bond.get("funding", 0) or 0),
            "message": 0,
            "appealBond": int(bond.get("amount", 0) or 0),
            "amount": refund,
        }
    )
    _refresh_message_fee_accounting_report_if_present(updated)
    return updated, recipient, refund


def calculate_appeal_charge(
    fees_distribution: dict[str, Any],
    *,
    current_round: int,
    status: str,
    terminal_committee_upper_bound: int | None = None,
    available_appeal_validators: int | None = None,
    replacement_rotations: int | None = None,
    leader_timeout_live_seats: int | None = None,
    policy: StudioFeePolicy | None = None,
) -> dict[str, Any]:
    """Quote the same bond + typed funding split used by Consensus admission."""
    policy = policy or StudioFeePolicy()
    fees = normalize_fees_distribution(fees_distribution)
    current_round = max(0, int(current_round))
    status_value = str(status).upper()
    bond = calculate_min_appeal_bond(
        fees,
        current_round=current_round,
        status=status_value,
        leader_timeout_rotations_left=(
            replacement_rotations if status_value == "LEADER_TIMEOUT" else None
        ),
        leader_timeout_live_seats=leader_timeout_live_seats,
        policy=policy,
    )

    validator_appeal = status_value in {"VALIDATORS_TIMEOUT", "ACCEPTED"}
    leader_appeal = status_value in {"LEADER_TIMEOUT", "UNDETERMINED"}
    if not validator_appeal and not leader_appeal:
        raise InvalidAppealBond("InvalidAppealBond")

    if validator_appeal:
        appeal_round = (
            current_round + 1 if current_round % 2 == 0 else current_round + 2
        )
        scheduled_jury_count = _validators_per_round_safe(current_round + 1)
        jury_count = (
            scheduled_jury_count
            if available_appeal_validators is None
            else min(
                scheduled_jury_count,
                max(0, int(available_appeal_validators)),
            )
        )
        replacement_rotations = 0
    else:
        appeal_round = current_round + 2
        jury_count = 0
        if replacement_rotations is None:
            replacement_rotations = _appeal_rotation_allowance(fees, current_round)
        replacement_rotations = max(0, int(replacement_rotations))

    pre_funded = appeal_round + (appeal_round & 1) <= int(fees["appealRounds"]) * 2
    taxable_work = 0
    if leader_appeal:
        if not pre_funded:
            taxable_work = bond
    else:
        scheduled_replacement = _validators_per_round_safe(appeal_round + 1)
        replacement_count = max(
            0,
            int(
                terminal_committee_upper_bound
                if terminal_committee_upper_bound is not None
                else scheduled_replacement
            ),
        )
        if pre_funded:
            time_units = _u256_mul(
                max(0, replacement_count - scheduled_replacement),
                int(fees["validatorTimeunitsAllocation"]),
            )
        else:
            time_units = _u256_add(
                _u256_mul(jury_count, int(fees["validatorTimeunitsAllocation"])),
                _u256_mul(
                    replacement_count,
                    int(fees["validatorTimeunitsAllocation"]),
                ),
                int(fees["leaderTimeunitsAllocation"]),
            )
        taxable_work = (
            _u256_mul(time_units, policy.gen_per_time_unit)
            if policy.gen_per_time_unit > 0
            else _require_uint256(time_units)
        )

    appellant_profit = 0 if pre_funded else successful_appeal_profit(bond)
    execution_slots = 0
    if not pre_funded:
        execution_slots = _u256_add(replacement_rotations, 2) if leader_appeal else 2
    execution_backing = _u256_mul(execution_slots, int(fees["executionBudgetPerRound"]))
    overlay = _time_unit_overlay_muldiv(taxable_work, policy.time_unit_overlay_bps)
    funding = _u256_add(taxable_work, overlay, appellant_profit, execution_backing)
    return {
        "bond": bond,
        "funding": funding,
        "appealRound": appeal_round,
        "juryCount": jury_count,
        "replacementRotations": replacement_rotations,
        "extendsSchedule": not pre_funded,
        "fundingBreakdown": {
            "bondPrincipal": bond,
            "taxableWork": taxable_work,
            "overlay": overlay,
            "appellantProfit": appellant_profit,
            "executionBacking": execution_backing,
        },
    }


def calculate_min_appeal_bond(
    fees_distribution: dict[str, Any],
    *,
    current_round: int,
    status: str,
    leader_timeout_rotations_left: int | None = None,
    leader_timeout_live_seats: int | None = None,
    policy: StudioFeePolicy | None = None,
) -> int:
    policy = policy or StudioFeePolicy()
    fees = normalize_fees_distribution(fees_distribution)
    current_round = max(0, int(current_round))
    status_value = str(status).upper()
    if status_value == "LEADER_TIMEOUT":
        if (
            leader_timeout_live_seats is not None
            and int(leader_timeout_live_seats) <= 1
        ):
            # The live committee only gates whether a replacement leader can
            # be induced. When it can, pricing still uses the configured round
            # size below, even though the timed-out leader was removed.
            return 0
        rotations_left = (
            _appeal_rotation_allowance(fees, current_round)
            if leader_timeout_rotations_left is None
            else max(0, int(leader_timeout_rotations_left))
        )
        total = _calculate_fee_for_round(
            _validators_per_round_safe(current_round),
            _u256_add(rotations_left, 1),
            int(fees["leaderTimeunitsAllocation"]),
            int(fees["validatorTimeunitsAllocation"]),
        )
        return (
            _u256_mul(total, policy.gen_per_time_unit)
            if policy.gen_per_time_unit > 0
            else total
        )

    if status_value == "UNDETERMINED":
        target_round = current_round + 2
        target_normal_index = target_round // 2
        rotations = (
            int(fees["rotations"][target_normal_index])
            if target_normal_index < len(fees["rotations"])
            else 0
        )
        total = _calculate_fee_for_round(
            _validators_per_round_safe(target_round),
            _u256_add(rotations, 1),
            int(fees["leaderTimeunitsAllocation"]),
            int(fees["validatorTimeunitsAllocation"]),
        )
        return (
            _u256_mul(total, policy.gen_per_time_unit)
            if policy.gen_per_time_unit > 0
            else total
        )

    if status_value in {"VALIDATORS_TIMEOUT", "ACCEPTED"}:
        target_round = current_round + 1
        total = _validators_per_round_safe(target_round) * int(
            fees["validatorTimeunitsAllocation"]
        )
        return (
            _u256_mul(total, policy.gen_per_time_unit)
            if policy.gen_per_time_unit > 0
            else total
        )

    return 0


def _appeal_rotation_allowance(
    fees: dict[str, int | list[int]],
    current_round: int,
) -> int:
    rotations = fees["rotations"]
    if not isinstance(rotations, list) or not rotations:
        return 0
    next_normal_index = max(0, int(current_round)) // 2 + 1
    if next_normal_index < len(rotations):
        return int(rotations[next_normal_index])
    return 0


def runtime_rotations_for_round(
    fees_distribution: dict[str, Any],
    transaction_rotation_cap: int | None,
    raw_round: int,
) -> int:
    """Return the exact funded runtime allowance for one raw normal round."""

    raw_round = max(0, int(raw_round))
    if raw_round % 2 != 0:
        return 0
    fees = normalize_fees_distribution(fees_distribution)
    normal_ordinal = raw_round // 2
    rotations = fees["rotations"]
    funded = (
        int(rotations[normal_ordinal])
        if isinstance(rotations, list) and normal_ordinal < len(rotations)
        else 0
    )
    return min(max(0, int(transaction_rotation_cap or 0)), max(0, funded))


def fill_message_fee_payload_from_allocation(
    accounting: dict[str, Any],
    message: dict[str, Any],
) -> dict[str, Any]:
    if bool(message.get("useBalance", False)):
        # Contract-funded messages carry their own canonical fee payload and
        # bypass the sender's allocation tree in Consensus.
        return copy.deepcopy(message)
    allocations = accounting.get("message_allocations") or []
    if not allocations:
        return copy.deepcopy(message)

    resolved = _resolve_allocation(allocations, message)
    if resolved is None:
        raise MessageNoMatchingAllocation("MessageNoMatchingAllocation")

    index, allocation = resolved
    updated = copy.deepcopy(message)
    message_type = int(updated.get("messageType", MESSAGE_TYPE_INTERNAL))
    if message_type == MESSAGE_TYPE_EXTERNAL:
        # External messages have no accepted/finalized lifecycle. GenVM main
        # carries `on: finalized` on external allocation nodes only to satisfy
        # the request schema, so do not phase-check them here.
        if not _message_has_fee_params(updated):
            updated["feeParams"] = allocation["feeParams"]
        updated["callKey"] = _normalize_call_key(
            updated.get("callKey", allocation["callKey"])
        )
        updated["messageFeeMode"] = "external"
        return updated

    if bool(allocation["onAcceptance"]) != bool(message.get("onAcceptance", False)):
        raise MessageEmissionPhaseMismatch("MessageEmissionPhaseMismatch")

    if not _message_has_fee_params(updated):
        updated["feeParams"] = allocation["feeParams"]
    if int(updated.get("declaredBudget", 0) or 0) == 0:
        # The allocation budget is a cumulative ceiling for every occurrence
        # with this key. It is not the declared budget of each occurrence.
        # GenVM/Consensus assigns an occurrence its minimum primary funding
        # plus the allocation subtree it must carry into the child. Using the
        # aggregate ceiling here overcharges the first message and makes a
        # second same-key occurrence fail with MessageBudgetExceeded.
        fee_params = decode_internal_message_fee_params(updated["feeParams"])
        policy = execution_policy_for_accounting(accounting)
        updated["declaredBudget"] = _internal_allocation_min_required(
            allocation,
            fee_params,
            policy,
        ) + _child_allocation_budget_sum(allocations, index)
    updated["callKey"] = _normalize_call_key(
        updated.get("callKey", allocation["callKey"])
    )
    expected_subtree = _allocation_subtree(allocations, index)
    # The deployed/default Consensus storage mode is FlatArrays. In that mode
    # allocationSubtree is receipt data only: MessagePayments ignores it and
    # inherits the canonical subtree from the parent's stored allocation tree.
    # Keep the leader/GenVM bytes untouched for the SubmittedMessage descriptor,
    # while carrying Studio's local FlatArrays resolution out-of-band for child
    # materialization. In particular, a missing or malformed supplied subtree
    # must not invalidate the reveal or skip a child.
    updated["_studioResolvedAllocationSubtree"] = expected_subtree
    updated["messageFeeMode"] = "mode2"
    return updated


def consume_message_fees(
    accounting: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    reported_total: int | None = None,
    policy: StudioFeePolicy | None = None,
    reimburse_external: bool = True,
    external_executor: str | None = None,
) -> dict[str, Any]:
    live_policy = _live_policy_for_accounting(accounting, policy)
    execution_policy = execution_policy_for_accounting(accounting, live_policy)
    if len(messages) > _message_count_cap(live_policy):
        raise TooManyMessages("TooManyMessages")

    updated = copy.deepcopy(accounting)
    recalculated_total = 0
    external_consumption_total = 0
    initial_external_reimbursed = int(
        updated.get("external_message_fee_reimbursed", 0) or 0
    )
    initial_external_remainder = int(
        updated.get("external_message_fee_remainder", 0) or 0
    )

    # Attribute the independently consumed message pool deterministically:
    # child escrows consume first, then external reservations in reveal order.
    # This keeps contributor ownership independent from receipt message order.
    for message in messages:
        message_type = _message_type_value(message)
        if message_type == MESSAGE_TYPE_INTERNAL:
            recalculated_total += _consume_internal_message_fee(
                updated,
                message,
                live_policy,
            )

    funding_offset = _next_external_funding_offset(
        updated,
        int(updated.get("message_fee_consumed", 0)) + recalculated_total,
    )
    external_reserved_total = 0
    for message in messages:
        if _message_type_value(message) != MESSAGE_TYPE_EXTERNAL:
            continue
        consumed, reserved = _consume_external_message_fee(
            updated,
            message,
            execution_policy,
            reimburse_external,
            external_executor,
            funding_offset=funding_offset + external_reserved_total,
        )
        external_consumption_total += consumed
        external_reserved_total += reserved

    if reported_total is not None and int(reported_total) < recalculated_total:
        raise MessageFeesReportMismatch("MessageFeesReportMismatch")

    attempted = (
        int(updated.get("message_fee_consumed", 0))
        + recalculated_total
        + external_consumption_total
    )
    message_budget = int(updated.get("message_fee_budget", 0))
    if attempted > message_budget:
        raise MessageBudgetExceeded("MessageBudgetExceeded")

    updated["message_fee_consumed"] = attempted
    consumption_event = {
        "consumed": recalculated_total + external_consumption_total,
        "internalConsumed": recalculated_total,
        "externalReimbursed": int(
            updated.get("external_message_fee_reimbursed", 0) or 0
        )
        - initial_external_reimbursed,
        "remaining": message_budget - attempted,
    }
    external_remainder_settled = (
        int(updated.get("external_message_fee_remainder", 0) or 0)
        - initial_external_remainder
    )
    if external_remainder_settled > 0:
        consumption_event["externalRemainderSettled"] = external_remainder_settled
    updated.setdefault("message_consumption_events", []).append(consumption_event)
    _refresh_message_fee_accounting_report_if_present(updated, execution_policy)
    return updated


def _message_count_cap(policy: StudioFeePolicy) -> int:
    cap = max(0, int(policy.max_allocated_messages))
    fee_cap = int(policy.max_messages_per_tx)
    if fee_cap > 0:
        cap = min(cap, fee_cap)
    return cap


def _message_type_value(message: dict[str, Any]) -> int:
    return int(message.get("messageType", MESSAGE_TYPE_INTERNAL))


def _consume_external_message_fee(
    accounting: dict[str, Any],
    message: dict[str, Any],
    policy: StudioFeePolicy,
    reimburse_external: bool,
    external_executor: str | None,
    *,
    funding_offset: int,
) -> tuple[int, int]:
    if int(message.get("declaredBudget", 0) or 0) != 0:
        raise MessageDeclaredBudgetInsufficient("MessageDeclaredBudgetInsufficient")
    event_count = len(accounting.get("external_message_events") or [])
    consumed = _reserve_external_execution(
        accounting,
        message,
        policy,
        reimburse=reimburse_external,
        executor=external_executor,
        funding_offset=funding_offset,
    )
    events = accounting.get("external_message_events") or []
    if len(events) == event_count:
        return consumed, 0
    event = events[-1]
    return consumed, int(event.get("reservation", 0) or 0)


def _consume_internal_message_fee(
    accounting: dict[str, Any],
    message: dict[str, Any],
    policy: StudioFeePolicy,
) -> int:
    declared_budget = int(message.get("declaredBudget", 0) or 0)
    fee_params = decode_internal_message_fee_params(message.get("feeParams", b""))
    _validate_internal_message_price_caps(fee_params)
    _validate_internal_execution_budget_floor(fee_params, policy)

    min_required = min_message_primary_fees(fee_params, policy)
    if declared_budget < min_required:
        raise MessageDeclaredBudgetInsufficient("MessageDeclaredBudgetInsufficient")

    if bool(message.get("useBalance", False)):
        # Consensus reserves this budget from the emitting contract (ghost),
        # outside the sender-funded message bucket and allocation tree.
        return 0

    _consume_against_allocation(accounting, message, declared_budget)
    return declared_budget


def _validate_internal_execution_budget_floor(
    fee_params: dict[str, Any],
    policy: StudioFeePolicy,
) -> None:
    execution_budget_per_round = int(fee_params["executionBudgetPerRound"])
    if (
        execution_budget_per_round > 0
        and execution_budget_per_round < policy.message_fee_params_budget_floor()
    ):
        raise BudgetTooLow("BudgetTooLow")


def record_reveal_message_fees(
    accounting: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    reported_total: int | None = None,
    policy: StudioFeePolicy | None = None,
) -> dict[str, Any]:
    live_policy = _live_policy_for_accounting(accounting, policy)
    _enforce_submitted_messages_cap(messages, live_policy)
    updated = consume_message_fees(
        accounting,
        messages,
        reported_total=reported_total,
        policy=live_policy,
        reimburse_external=False,
    )
    updated["message_fees_recorded_at_reveal"] = True
    return updated


def record_external_message_execution_fees(
    accounting: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    policy: StudioFeePolicy | None = None,
    executor: str | None = None,
) -> dict[str, Any]:
    updated = copy.deepcopy(accounting)
    policy = execution_policy_for_accounting(updated, policy)
    consumed_total = 0
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
            int(updated.get("message_fee_consumed", 0)) + consumed_total + reservation
        )
        message_budget = int(updated.get("message_fee_budget", 0))
        if attempted > message_budget:
            raise MessageBudgetExceeded("MessageBudgetExceeded")

        event["gasUsed"] = gas_used
        event["reimbursement"] = reimbursement
        event["remainder"] = remainder
        event["executionRecorded"] = True
        _record_external_message_fee_payouts(updated, event, executor)
        consumed_total += reservation
        reimbursement_total += reimbursement
        remainder_total += remainder
        updated_any = True

    if updated_any:
        updated["message_fee_consumed"] = (
            int(updated.get("message_fee_consumed", 0)) + consumed_total
        )
        updated["external_message_fee_settled"] = (
            int(updated.get("external_message_fee_settled", 0)) + consumed_total
        )
        updated["external_message_fee_reimbursed"] = (
            int(updated.get("external_message_fee_reimbursed", 0)) + reimbursement_total
        )
        updated["external_message_fee_remainder"] = (
            int(updated.get("external_message_fee_remainder", 0)) + remainder_total
        )
        updated.setdefault("message_consumption_events", []).append(
            {
                "consumed": consumed_total,
                "internalConsumed": 0,
                "externalReimbursed": reimbursement_total,
                "externalRemainderSettled": remainder_total,
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


def refund_failed_internal_message_fee(
    accounting: dict[str, Any],
    message: dict[str, Any],
) -> dict[str, Any]:
    """Return a skipped internal child's allowance to the parent bucket.

    Consensus does not create a transaction for a non-ghost internal
    recipient. Its declared budget therefore never moves to a child escrow.
    Contract-funded (useBalance) budgets live outside the parent bucket and
    are refunded by the account-value bridge instead.
    """

    updated = copy.deepcopy(accounting)
    if int(message.get("messageType", MESSAGE_TYPE_INTERNAL)) != MESSAGE_TYPE_INTERNAL:
        return updated
    if bool(message.get("useBalance", False)):
        return updated

    declared_budget = int(message.get("declaredBudget", 0) or 0)
    if declared_budget <= 0:
        return updated
    _decrement_allocation_consumed(updated, message, declared_budget)
    updated["message_fee_consumed"] = max(
        0,
        int(updated.get("message_fee_consumed", 0)) - declared_budget,
    )
    updated.setdefault("failed_internal_message_refunds", []).append(
        {
            "recipient": str(message.get("recipient", "")).lower(),
            "callKey": _normalize_call_key(message.get("callKey", EMPTY_CALL_KEY)),
            "declaredBudget": declared_budget,
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
        if bool(message.get("useBalance", False)):
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
        _reindex_unexecuted_external_funding(updated)

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
    fallback_policy = execution_policy_for_accounting(updated, policy)
    policy = _receipt_execution_policy(receipt, fallback_policy)
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
    execution_bucket_report = _chargeable_execution_bucket_report(
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
    if _has_bucket(consumed, GENVM_MESSAGE_FEE_BUCKET):
        updated["genvm_message_fee_consumed"] = _bucket_value(
            consumed, GENVM_MESSAGE_FEE_BUCKET
        )
    _attach_message_fee_accounting_report(updated)
    _attach_recommended_fee_preset(updated, policy)
    return updated


def validate_receipt_admission_caps(
    receipt: Any,
    policy: StudioFeePolicy | None = None,
) -> None:
    policy = policy or StudioFeePolicy.from_env()
    eq_outputs_length = _receipt_eq_blocks_outputs_length(receipt)
    if (
        policy.max_eq_outputs_bytes > 0
        and eq_outputs_length > policy.max_eq_outputs_bytes
    ):
        raise EqOutputsTooLarge(
            f"EqOutputsTooLarge({eq_outputs_length},{policy.max_eq_outputs_bytes})"
        )

    message_payloads = _receipt_message_fee_payloads({}, receipt)
    if message_payloads and _receipt_execution_allows_messages(receipt):
        if len(message_payloads) > _message_count_cap(policy):
            raise TooManyMessages("TooManyMessages")
        _enforce_submitted_messages_cap(message_payloads, policy)


def settle_fee_accounting(
    accounting: dict[str, Any],
    *,
    receipt: Any | None = None,
    reason: str = "finalized",
    actual_final_round: int | None = None,
    num_of_validators: int | None = None,
    consensus_history: dict[str, Any] | None = None,
    execution_mode: str = "NORMAL",
    policy: StudioFeePolicy | None = None,
) -> tuple[dict[str, Any], int]:
    policy_override = policy
    locked_policy = _accounting_policy(
        accounting,
        policy_override if not accounting.get("activation_prices_locked") else None,
    )
    live_policy = _live_policy_for_accounting(accounting, policy_override)
    execution_policy = execution_policy_for_accounting(accounting, live_policy)
    updated = record_execution_fee_consumption(accounting, receipt, live_policy)
    if updated.get("status") in {"settled", "canceled"}:
        return updated, 0
    updated = _record_historical_execution_fee_consumption(
        updated,
        receipt=receipt,
        consensus_history=consensus_history,
        policy=execution_policy,
    )

    primary_budget = int(updated.get("primary_fee_budget", 0))
    execution_budget = int(updated.get("execution_budget_total", 0))
    primary_required = int(updated.get("primary_fee_required", 0))
    fees_distribution = updated.get("fees_distribution") or {}
    settlement_rounds: list[dict[str, Any]] = []
    if actual_final_round is not None:
        validators = int(
            num_of_validators or updated.get("num_of_initial_validators") or 0
        )
        time_unit_budget, settlement_rounds = _calculate_settled_time_unit_fees(
            fees_distribution,
            validators,
            actual_final_round,
            locked_policy,
            consensus_history,
            execution_mode,
            terminal_electorate_size=(
                int(updated["selection_pool_count"])
                if updated.get("selection_pool_count") is not None
                else None
            ),
        )
        # Consensus settles storage + receipt/write consumption against the
        # complete unified reserve quoted at submission:
        #
        #   executionBudgetPerRound * all configured leader slots
        #
        # Unused future slots are a refund breakdown only; they do not shrink
        # the cap available to real cumulative execution. In particular, an
        # early round-0 finish may consume more than one per-round slice (for
        # example through persistent storage) while remaining fully backed by
        # the transaction's multi-round reserve.
        updated["actual_final_round"] = int(actual_final_round)
        if settlement_rounds:
            updated["settlement_rounds"] = settlement_rounds
    else:
        time_unit_budget = max(0, primary_required - execution_budget)
    execution_consumed = int(updated.get("execution_fee_consumed", 0))
    execution_spent = min(execution_consumed, execution_budget)
    storage_fee = _bucket_value(
        list(updated.get("execution_fee_consumed_buckets") or []),
        1,
    )
    bond_settlements, bond_payout = _settle_appeal_bonds(
        updated,
        consensus_history=consensus_history,
        cancel=False,
    )
    storage_recipients = _settlement_storage_recipient_count(
        consensus_history,
        settlement_rounds if actual_final_round is not None else [],
        fees_distribution,
        locked_policy,
        execution_mode,
        bond_settlements=bond_settlements,
        terminal_electorate_size=(
            int(updated["selection_pool_count"])
            if updated.get("selection_pool_count") is not None
            else None
        ),
    )
    if storage_recipients == 0 and time_unit_budget > 0 and not settlement_rounds:
        # Legacy/synthetic histories without receipt identities retain the
        # configured initial committee as the best available denominator.
        storage_recipients = int(
            num_of_validators or updated.get("num_of_initial_validators") or 0
        )
    capped_storage_fee = min(storage_fee, execution_budget)
    storage_dust = (
        capped_storage_fee % storage_recipients
        if storage_recipients > 0
        else capped_storage_fee
    )
    execution_spent = max(0, execution_spent - storage_dust)
    appeal_profit_requested = sum(
        max(0, int(item.get("payout", 0)) - int(item.get("amount", 0)))
        for item in bond_settlements
        if item.get("status") == "successful"
    )
    appeal_bond_sender_refund = sum(
        int(item.get("senderRefund", 0) or 0) for item in bond_settlements
    )
    current_overlay_bps = int(
        updated.get("funding_overlay_bps", locked_policy.time_unit_overlay_bps)
    )
    time_unit_overlay_requested = _time_unit_overlay(
        time_unit_budget,
        current_overlay_bps,
    )
    overlay_budget = min(
        primary_budget,
        max(0, int(updated.get("time_unit_overlay_budget", 0) or 0)),
    )
    primary_core_budget = max(0, primary_budget - overlay_budget)
    time_unit_overlay_spent = min(time_unit_overlay_requested, overlay_budget)
    primary_core_non_profit_spend = time_unit_budget + execution_spent
    if primary_core_non_profit_spend > primary_core_budget:
        raise InsufficientFees("InsufficientFeesForRound")
    appeal_profit_spent = (
        appeal_profit_requested
        if primary_core_non_profit_spend + appeal_profit_requested
        <= primary_core_budget
        else 0
    )
    if appeal_profit_requested > 0 and appeal_profit_spent == 0:
        # Consensus never partially promises a successful-appeal profit. If
        # legacy/malformed accounting cannot fund the complete profit leg, all
        # successful payouts are clamped to principal and that fee money stays
        # in the sender residual.
        for item in bond_settlements:
            if item.get("status") != "successful":
                continue
            amount = int(item.get("amount", 0) or 0)
            payout = int(item.get("payout", 0) or 0)
            bond_payout -= max(0, payout - amount)
            item["payout"] = amount
            item["profitFunded"] = False
    primary_core_spent = primary_core_non_profit_spend + appeal_profit_spent
    primary_spent = primary_core_spent + time_unit_overlay_spent
    primary_refund = max(
        0, primary_budget - primary_spent - int(updated.get("primary_fee_refunded", 0))
    )

    message_refund = max(
        0,
        int(updated.get("message_fee_budget", 0))
        - int(updated.get("message_fee_consumed", 0))
        - int(updated.get("message_fee_refunded", 0)),
    )
    refund = primary_refund + message_refund + appeal_bond_sender_refund
    updated["status"] = "settled"
    updated["settlement_reason"] = reason
    updated["primary_fee_spent"] = primary_spent
    updated["time_unit_overlay_spent"] = time_unit_overlay_spent
    updated["time_unit_overlay_requested"] = time_unit_overlay_requested
    updated["time_unit_overlay_settlement_bps"] = current_overlay_bps
    updated["appeal_profit_requested"] = appeal_profit_requested
    updated["appeal_profit_spent"] = appeal_profit_spent
    updated["storage_fee_recipient_count"] = storage_recipients
    updated["storage_fee_dust_refunded"] = storage_dust
    updated["primary_fee_refunded"] = (
        int(updated.get("primary_fee_refunded", 0)) + primary_refund
    )
    updated["message_fee_refunded"] = (
        int(updated.get("message_fee_refunded", 0)) + message_refund
    )
    updated["appeal_bond_sender_refunded"] = (
        int(updated.get("appeal_bond_sender_refunded", 0)) + appeal_bond_sender_refund
    )
    updated["total_refunded"] = int(updated.get("total_refunded", 0)) + refund
    updated["appeal_bonds_payout_total"] = (
        int(updated.get("appeal_bonds_payout_total", 0)) + bond_payout
    )
    updated["appeal_bond_settlements"] = bond_settlements
    primary_core_refund = max(0, primary_core_budget - primary_core_spent)
    overlay_refund = max(0, overlay_budget - time_unit_overlay_spent)
    updated["fee_refund_settlements"] = _allocate_fee_refund(
        updated,
        primary=primary_core_refund,
        overlay=overlay_refund,
        message=message_refund,
        unattributed=(
            appeal_bond_sender_refund
            + max(0, primary_refund - primary_core_refund - overlay_refund)
        ),
    )
    updated.setdefault("refunds", []).append(
        {
            "reason": reason,
            "primary": primary_refund,
            "message": message_refund,
            "appealBond": appeal_bond_sender_refund,
            "amount": refund,
        }
    )
    _refresh_message_fee_accounting_report_if_present(updated, execution_policy)
    return updated, refund


def _record_historical_execution_fee_consumption(
    accounting: dict[str, Any],
    *,
    receipt: Any | None,
    consensus_history: dict[str, Any] | None,
    policy: StudioFeePolicy,
) -> dict[str, Any]:
    attempts = _historical_leader_proposal_receipts(
        consensus_history,
        terminal_electorate_size=(
            int(accounting["selection_pool_count"])
            if accounting.get("selection_pool_count") is not None
            else None
        ),
    )
    if not attempts:
        return accounting

    updated = copy.deepcopy(accounting)
    receipt_total = 0
    attempt_report: list[dict[str, Any]] = []
    for (
        round_index,
        attempt_index,
        attempt_receipt,
        deterministic_violation,
    ) in attempts:
        timed_out = _receipt_is_leader_timeout(attempt_receipt)
        attempt_policy = _receipt_execution_policy(attempt_receipt, policy)
        report = (
            None
            if timed_out or deterministic_violation
            else _receipt_fee_report(attempt_receipt, attempt_policy)
        )
        charged = _receipt_report_chargeable_fee(report) if report is not None else 0
        receipt_total += charged
        attempt_report.append(
            {
                "round": round_index,
                "attempt": attempt_index,
                "leaderTimeout": timed_out,
                "deterministicViolation": deterministic_violation,
                "receiptFee": charged,
                "receiptGasPrice": int(attempt_policy.receipt_gas_price),
            }
        )

    final_consumed = _receipt_data_fees_consumed(receipt)
    final_fee_report = (
        _receipt_fee_report(receipt, policy) if final_consumed is not None else None
    )
    storage_fee = (
        _chargeable_storage_fee(receipt, final_consumed, final_fee_report)
        if final_consumed is not None
        else _bucket_value(
            list(updated.get("execution_fee_consumed_buckets") or []),
            1,
        )
    )
    updated["execution_fee_consumed"] = receipt_total + storage_fee
    updated["execution_fee_consumed_buckets"] = [receipt_total, storage_fee]
    updated["historical_execution_attempts"] = attempt_report
    updated["execution_fee_report"] = {
        **(updated.get("execution_fee_report") or {}),
        "historicalReceiptFees": {
            "attempts": attempt_report,
            "receiptFeeTotal": receipt_total,
            "finalStorageFee": storage_fee,
            "totalExecution": receipt_total + storage_fee,
        },
    }
    return updated


def _historical_leader_proposal_receipts(
    consensus_history: dict[str, Any] | None,
    *,
    terminal_electorate_size: int | None = None,
) -> list[tuple[int, int, dict[str, Any], bool]]:
    grouped = _consensus_attempt_entries_by_round(consensus_history)
    outcomes = _round_outcomes(consensus_history)
    proposals: list[tuple[int, int, dict[str, Any], bool]] = []
    for round_index in sorted(grouped):
        predecessor = max(
            (candidate for candidate in outcomes if candidate < round_index),
            default=None,
        )
        round_electorate_size = (
            terminal_electorate_size
            if predecessor is not None
            and outcomes.get(predecessor)
            in {
                ConsensusRound.VALIDATOR_APPEAL_SUCCESSFUL.value,
                ConsensusRound.VALIDATOR_TIMEOUT_APPEAL_SUCCESSFUL.value,
            }
            else None
        )
        for attempt_index, entry in enumerate(grouped[round_index]):
            deterministic_violation = (
                _fee_alignment_result(entry, electorate_size=round_electorate_size)
                == "deterministic_violation"
            )
            value = entry.get("leader_result")
            candidates = value if isinstance(value, list) else [value]
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                mode = candidate.get("mode")
                if hasattr(mode, "value"):
                    mode = mode.value
                if str(mode or "").lower() == "leader":
                    proposals.append(
                        (
                            round_index,
                            attempt_index,
                            candidate,
                            deterministic_violation,
                        )
                    )
                    break
    return proposals


def _receipt_is_leader_timeout(receipt: Any) -> bool:
    genvm_result = _receipt_genvm_result(receipt)
    if isinstance(genvm_result, dict) and genvm_result.get("error_code") == (
        "CONSENSUS_LEADER_EXEC_TIMEOUT"
    ):
        return True

    result = _receipt_value(receipt, "result")
    if isinstance(result, str):
        try:
            result = base64.b64decode(result)
        except (ValueError, TypeError):
            result = b""
    return isinstance(result, (bytes, bytearray)) and bytes(result).endswith(b"timeout")


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
    bond_settlements, bond_payout = _settle_appeal_bonds(
        updated,
        consensus_history=None,
        cancel=True,
    )
    updated["status"] = "canceled"
    updated["settlement_reason"] = reason
    updated["primary_fee_refunded"] = (
        int(updated.get("primary_fee_refunded", 0)) + primary_refund
    )
    updated["message_fee_refunded"] = (
        int(updated.get("message_fee_refunded", 0)) + message_refund
    )
    updated["total_refunded"] = int(updated.get("total_refunded", 0)) + refund
    updated["appeal_bonds_payout_total"] = (
        int(updated.get("appeal_bonds_payout_total", 0)) + bond_payout
    )
    updated["appeal_bond_settlements"] = bond_settlements
    updated["canceled_message_allocations"] = copy.deepcopy(
        updated.get("message_allocations") or []
    )
    updated["message_allocations"] = []
    updated["allocation_consumed"] = {}
    updated["message_allocations_invalidated"] = True
    overlay_budget = min(
        primary_refund,
        max(
            0,
            int(updated.get("time_unit_overlay_budget", 0) or 0)
            - int(updated.get("time_unit_overlay_spent", 0) or 0),
        ),
    )
    updated["fee_refund_settlements"] = _allocate_fee_refund(
        updated,
        primary=primary_refund - overlay_budget,
        overlay=overlay_budget,
        message=message_refund,
    )
    updated.setdefault("refunds", []).append(
        {
            "reason": reason,
            "primary": primary_refund,
            "message": message_refund,
            "appealBond": 0,
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
        merged["rotations"] = list(incoming_fees["rotations"])
    elif incoming_fees["rotations"] or int(incoming_fees["appealRounds"]) != 0:
        # Consensus treats an established schedule as immutable. Appeals are
        # the only operation allowed to append one appeal slot plus its paired
        # rotation entry; ordinary top-ups are pure funding.
        raise TopUpCannotExtendSchedule("TopUpCannotExtendSchedule")

    merged["executionBudgetPerRound"] = int(merged["executionBudgetPerRound"]) + int(
        incoming_fees["executionBudgetPerRound"]
    )
    merged["totalMessageFees"] = int(merged["totalMessageFees"]) + int(
        incoming_fees["totalMessageFees"]
    )
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
        root_sum += _validate_message_allocation_node(
            index,
            raw_node,
            message_allocations,
            root_keys,
            external_keys,
            min_required_by_index,
            policy,
        )

    if root_sum != total_message_fees:
        raise MessageAllocationsNotEqualBudget("MessageAllocationsNotEqualBudget")

    _validate_child_budget_consistency(message_allocations, min_required_by_index)
    _validate_allocation_tree_depth(message_allocations, policy)
    _validate_sibling_duplicates(message_allocations)


def _validate_message_allocation_node(
    index: int,
    raw_node: dict[str, Any],
    message_allocations: list[dict[str, Any]],
    root_keys: set[tuple[int, str, str]],
    external_keys: set[tuple[str, str]],
    min_required_by_index: dict[int, int],
    policy: StudioFeePolicy,
) -> int:
    node = _normalize_message_allocation(raw_node)
    _validate_allocation_parent(index, node, message_allocations)

    if int(node["messageType"]) == MESSAGE_TYPE_EXTERNAL:
        _validate_external_allocation(node, external_keys, policy)
        return int(node["budget"])

    if int(node["messageType"]) != MESSAGE_TYPE_INTERNAL:
        raise AllocationTreeMalformed("AllocationTreeMalformed")

    min_required = _validate_internal_allocation_budget(node, policy)
    min_required_by_index[index] = min_required
    return _root_allocation_budget(node, root_keys)


def _validate_allocation_parent(
    index: int,
    node: dict[str, Any],
    message_allocations: list[dict[str, Any]],
) -> None:
    parent_index = int(node["parentIndex"])
    if parent_index == NODE_ROOT_SENTINEL:
        return
    if parent_index >= index:
        raise AllocationTreeMalformed("AllocationTreeMalformed")

    parent_node = _normalize_message_allocation(message_allocations[parent_index])
    if int(parent_node["messageType"]) == MESSAGE_TYPE_EXTERNAL:
        raise AllocationTreeMalformed("AllocationTreeMalformed")


def _validate_internal_allocation_budget(
    node: dict[str, Any],
    policy: StudioFeePolicy,
) -> int:
    internal_fee_params = decode_internal_message_fee_params(node["feeParams"])
    _validate_phase_timeout_bounds(
        int(internal_fee_params["leaderTimeunitsAllocation"]),
        int(internal_fee_params["validatorTimeunitsAllocation"]),
        policy,
        allow_zero=True,
    )
    min_required = _internal_allocation_min_required(node, internal_fee_params, policy)
    if int(node["budget"]) < min_required:
        raise AllocationLifecycleBudgetInsufficient(
            "AllocationLifecycleBudgetInsufficient"
        )

    _validate_internal_execution_budget_floor(internal_fee_params, policy)
    return min_required


def _validate_internal_message_price_caps(
    internal_fee_params: dict[str, Any],
) -> None:
    # Consensus rejects a leader reveal whose child declares any zero price
    # cap. Validate the identical canonical bytes at submission/allocation
    # time too, so Studio cannot accept a message shape that v0.6 rejects.
    for field_index, field in (
        (4, "maxPriceGenPerTimeUnit"),
        (5, "storageFeeMaxGasPrice"),
        (6, "receiptFeeMaxGasPrice"),
    ):
        if int(internal_fee_params[field]) <= 0:
            raise FeeValueMustBeNonZero(f"FeeValueMustBeNonZero({field_index})")


def _internal_allocation_min_required(
    node: dict[str, Any],
    internal_fee_params: dict[str, Any],
    policy: StudioFeePolicy,
) -> int:
    min_primary = min_message_primary_fees(internal_fee_params, policy)
    lifecycle_multiplier = (
        int(internal_fee_params["appealRounds"]) + 1
        if bool(node["onAcceptance"])
        else 1
    )
    return min_primary * lifecycle_multiplier


def _root_allocation_budget(
    node: dict[str, Any],
    root_keys: set[tuple[int, str, str]],
) -> int:
    if int(node["parentIndex"]) != NODE_ROOT_SENTINEL:
        return 0

    key = _allocation_key(node)
    if key in root_keys:
        raise AllocationDuplicateKey("AllocationDuplicateKey")
    root_keys.add(key)
    return int(node["budget"])


def _validate_child_budget_consistency(
    message_allocations: list[dict[str, Any]],
    min_required_by_index: dict[int, int],
) -> None:
    for index, raw_node in enumerate(message_allocations):
        node = _normalize_message_allocation(raw_node)
        if int(node["messageType"]) == MESSAGE_TYPE_EXTERNAL:
            continue
        child_sum = _child_allocation_budget_sum(message_allocations, index)
        if int(node["budget"]) < min_required_by_index[index] + child_sum:
            raise AllocationTreeBudgetInconsistent("AllocationTreeBudgetInconsistent")


def _child_allocation_budget_sum(
    message_allocations: list[dict[str, Any]],
    parent_index: int,
) -> int:
    child_sum = 0
    for raw_child in message_allocations[parent_index + 1 :]:
        child = _normalize_message_allocation(raw_child)
        if int(child["parentIndex"]) == parent_index:
            child_sum += int(child["budget"])
    return child_sum


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
        "maxPriceGenPerTimeUnit": int(decoded[5]),
        "storageFeeMaxGasPrice": int(decoded[6]),
        "receiptFeeMaxGasPrice": int(decoded[7]),
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
            "maxPriceGenPerTimeUnit": int(
                internal_fee_params["maxPriceGenPerTimeUnit"]
            ),
            "storageFeeMaxGasPrice": 0,
            "receiptFeeMaxGasPrice": 0,
        },
        VALIDATORS_PER_ROUND[0],
        0,
        policy,
        enforce_gen_price_cap=False,
    )


def _calculate_initial_round_total(
    fees: dict[str, int | list[int]],
    num_of_validators: int,
) -> int:
    validator_index = _validator_index(num_of_validators)
    if int(fees["appealRounds"]) != len(fees["rotations"]) - 1:
        raise InvalidAppealRounds("InvalidAppealRounds")
    return _calculate_fees(fees, validator_index)


def _calculate_appeal_round_total(
    fees: dict[str, int | list[int]],
    round: int,
) -> int:
    # Fee schedules index rotations by normal-round ordinal, whereas callers
    # use raw consensus rounds (0, 2, 4, ... for normal rounds).
    rotations_index = round // 2
    rotations = (
        int(fees["rotations"][rotations_index])
        if rotations_index < len(fees["rotations"])
        else 0
    )
    return _calculate_fee_for_round(
        _validators_per_round_safe(round),
        _u256_add(rotations, 1),
        int(fees["leaderTimeunitsAllocation"]),
        int(fees["validatorTimeunitsAllocation"]),
    )


def _calculate_appeal_profit_reserve(
    fees: dict[str, int | list[int]],
    *,
    gen_per_time_unit: int = 0,
) -> int:
    rotations = fees["rotations"]
    if not isinstance(rotations, list):
        raise InvalidAppealRounds("InvalidAppealRounds")

    reserve = 0
    for appeal_ordinal in range(int(fees["appealRounds"])):
        rotations_index = appeal_ordinal + 1
        funded_rotations = (
            int(rotations[rotations_index]) if rotations_index < len(rotations) else 0
        )
        next_normal_bond = _calculate_fee_for_round(
            _validators_per_round_safe((appeal_ordinal + 1) * 2),
            _u256_add(funded_rotations, 1),
            int(fees["leaderTimeunitsAllocation"]),
            int(fees["validatorTimeunitsAllocation"]),
        )
        if gen_per_time_unit > 0:
            next_normal_bond = _u256_mul(next_normal_bond, gen_per_time_unit)
        reserve = _u256_add(reserve, successful_appeal_profit(next_normal_bond))
    return reserve


def _apply_time_unit_price(
    total: int,
    max_price: int,
    policy: StudioFeePolicy,
    *,
    enforce_cap: bool = True,
) -> int:
    if enforce_cap and max_price > 0 and policy.gen_per_time_unit > max_price:
        raise MaxPriceExceeded("MaxPriceExceeded")
    # Consensus reserves at the caller's ceiling. The live price is checked
    # against that ceiling and locked separately for settlement.
    return _u256_mul(total, max_price) if max_price > 0 else _require_uint256(total)


def _time_unit_overlay(time_unit_work: int, split_bps: int) -> int:
    split_bps = int(split_bps)
    if time_unit_work <= 0 or split_bps <= 0:
        return 0
    if split_bps >= 10_000:
        raise FeeValidationError("InvalidTimeUnitOverlayBps")
    return _u256_mul(time_unit_work, split_bps) // (10_000 - split_bps)


def _time_unit_overlay_muldiv(time_unit_work: int, split_bps: int) -> int:
    """Mirror AppealEconomics' full-precision Math.mulDiv overlay quote."""
    time_unit_work = _require_uint256(time_unit_work)
    split_bps = int(split_bps)
    if time_unit_work <= 0 or split_bps <= 0:
        return 0
    if split_bps >= 10_000:
        raise FeeValidationError("InvalidTimeUnitOverlayBps")
    return _require_uint256(time_unit_work * split_bps // (10_000 - split_bps))


def _carve_time_unit_overlay_reserve(
    *,
    current_reserve: int,
    cumulative_primary: int,
    execution_budget: int,
    incoming_primary: int,
    split_bps: int,
) -> int:
    """Mirror FeeManagerHelpers.carveOverlayReserve for one funding call."""
    current_reserve = max(0, int(current_reserve))
    incoming_primary = max(0, int(incoming_primary))
    split_bps = int(split_bps)
    if split_bps <= 0:
        return current_reserve
    if split_bps >= 10_000:
        raise FeeValidationError("InvalidTimeUnitOverlayBps")

    taxable_time_unit = max(
        0,
        int(cumulative_primary) - int(execution_budget),
    )
    target_reserve = taxable_time_unit * split_bps // 10_000
    reserve_delta = max(0, target_reserve - current_reserve)
    return current_reserve + min(reserve_delta, incoming_primary)


def successful_appeal_reward(appeal_bond: int) -> int:
    appeal_bond = _require_uint256(appeal_bond)
    # AppealEconomics deliberately arranges floor(5*bond/2) this way so an
    # otherwise representable result does not overflow in bond * 5.
    return _u256_add(
        _u256_mul(appeal_bond, 2),
        appeal_bond // 2,
    )


def successful_appeal_profit(appeal_bond: int) -> int:
    return successful_appeal_reward(appeal_bond) - _require_uint256(appeal_bond)


def _validators_per_round_safe(round: int) -> int:
    index = min(max(0, int(round)), len(VALIDATORS_PER_ROUND) - 1)
    return VALIDATORS_PER_ROUND[index]


def _enforce_gas_price_cap(actual_price: int, max_price: int) -> None:
    if max_price > 0 and actual_price > max_price:
        raise MaxPriceExceeded("MaxPriceExceeded")


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
        _u256_add(int(rotations[0]), 1),
        leader_timeunits,
        validator_timeunits,
    )

    rotations_index = 1
    rotations_this_round = 1
    appeal_rounds = int(fees_distribution["appealRounds"])
    for offset in range(1, (appeal_rounds * 2) + 1):
        # Consensus uses the configured round ladder by absolute round after
        # the caller-selected initial committee. Rounds beyond the published
        # ladder saturate at its final size.
        round_validators = _validators_per_round_safe(offset)
        if offset % 2 == 0 and rotations_index < len(rotations):
            rotations_this_round = _u256_add(int(rotations[rotations_index]), 1)
            rotations_index += 1
        elif offset % 2 == 1:
            rotations_this_round = 1

        calculated_fees = _u256_add(
            calculated_fees,
            _calculate_fee_for_round(
                round_validators,
                rotations_this_round,
                leader_timeunits,
                validator_timeunits,
            ),
        )

    return calculated_fees


def _calculate_fee_for_round(
    num_of_validators: int,
    rotations: int,
    leader_timeunits_allocation: int,
    validator_timeunits_allocation: int,
    leader_multiplier: tuple[int, int] = (1, 1),
) -> int:
    leader_num, leader_den = leader_multiplier
    leader_total = _u256_mul(rotations, leader_timeunits_allocation)
    leader_fee = _u256_mul(leader_total, leader_num) // leader_den
    validator_fee = _u256_mul(
        rotations,
        num_of_validators,
        validator_timeunits_allocation,
    )
    return _u256_add(leader_fee, validator_fee)


def _leader_slots_for_round(
    funded_rotations: list[int],
    funded_index: int,
    actual_rotations: dict[int, int],
    *,
    round_index: int | None = None,
) -> int:
    funded_slots = _u256_add(int(funded_rotations[funded_index]), 1)
    if not actual_rotations:
        return funded_slots
    actual_round_index = funded_index if round_index is None else round_index
    actual_slots = _u256_add(int(actual_rotations.get(actual_round_index, 0)), 1)
    return min(funded_slots, actual_slots)


def _round_outcomes(consensus_history: dict[str, Any] | None) -> dict[int, str]:
    return {
        round_index: str(entry.get("consensus_round") or "")
        for round_index, entry in logical_fee_round_entries(consensus_history)
    }


def _round_leader_multiplier(outcome: str | None) -> tuple[int, int]:
    return ROUND_LEADER_MULTIPLIERS.get(str(outcome or ""), (1, 1))


def _settle_appeal_bonds(
    accounting: dict[str, Any],
    *,
    consensus_history: dict[str, Any] | None,
    cancel: bool,
) -> tuple[list[dict[str, Any]], int]:
    existing = accounting.get("appeal_bond_settlements")
    if isinstance(existing, list) and existing:
        return copy.deepcopy(existing), 0

    outcomes = _round_outcomes(consensus_history)
    entries = dict(logical_fee_round_entries(consensus_history))
    fees = normalize_fees_distribution(accounting.get("fees_distribution") or {})
    validator_timeunits = int(fees["validatorTimeunitsAllocation"])
    locked_policy = _accounting_policy(accounting)
    if locked_policy.gen_per_time_unit > 0:
        validator_timeunits *= int(locked_policy.gen_per_time_unit)
    settlements: list[dict[str, Any]] = []
    payout_total = 0
    for index, bond in enumerate(accounting.get("appeal_bonds") or []):
        if not isinstance(bond, dict):
            continue
        amount = int(bond.get("amount", 0) or 0)
        appealer = bond.get("appealer")
        appealed_round = int(bond.get("round", 0) or 0)
        outcome_index, outcome = _appeal_outcome_after_round(outcomes, appealed_round)
        if cancel and outcome is None:
            status = "returned"
            payout = amount
        elif outcome in APPEAL_SUCCESS_ROUNDS:
            status = "successful"
            payout = successful_appeal_reward(amount)
        else:
            status = "forfeited"
            payout = 0
        payout_total += payout
        entry = {
            "bondIndex": index,
            "appealer": appealer,
            "amount": amount,
            "round": appealed_round,
            "status": status,
            "payout": payout,
        }
        if outcome is not None:
            entry["outcomeRound"] = outcome_index
            entry["outcome"] = outcome
        if status == "forfeited":
            entry["bond_forfeited"] = amount
            if outcome == ConsensusRound.LEADER_TIMEOUT_APPEAL_FAILED.value:
                leader_share = amount // 2
                entry["distribution"] = "leader-timeout-split"
                entry["leaderPayout"] = leader_share
                entry["senderRefund"] = amount - leader_share
            elif outcome in {
                ConsensusRound.VALIDATOR_APPEAL_FAILED.value,
                ConsensusRound.VALIDATORS_TIMEOUT_APPEAL_FAILED.value,
            }:
                outcome_entry = entries.get(int(outcome_index or 0))
                receipts = _entry_validator_receipts(outcome_entry)
                revealed = (
                    len(receipts)
                    if receipts
                    else _validators_per_round_safe(int(outcome_index or 0))
                )
                aligned = (
                    _aligned_validator_count(outcome_entry) if receipts else revealed
                )
                pool = revealed * validator_timeunits + amount
                dust = pool if aligned <= 0 else pool % aligned
                sender_refund = min(amount, dust)
                entry["distribution"] = "appeal-validators"
                entry["bondDistributed"] = amount - sender_refund
                if sender_refund:
                    entry["senderRefund"] = sender_refund
            elif outcome == ConsensusRound.LEADER_APPEAL_FAILED.value:
                outcome_entry = entries.get(int(outcome_index or 0))
                receipts = _entry_validator_receipts(outcome_entry)
                aligned = _aligned_validator_count(outcome_entry) if receipts else 0
                sender_refund = amount if aligned <= 0 else amount % aligned
                entry["distribution"] = "replay-validators"
                entry["bondDistributed"] = amount - sender_refund
                if sender_refund:
                    entry["senderRefund"] = sender_refund
        settlements.append(entry)
    return settlements, payout_total


def _appeal_outcome_after_round(
    outcomes: dict[int, str], appealed_round: int
) -> tuple[int | None, str | None]:
    for round_index in sorted(outcomes):
        if round_index <= appealed_round:
            continue
        outcome = outcomes[round_index]
        if outcome in APPEAL_SUCCESS_ROUNDS or outcome in APPEAL_FAILED_ROUNDS:
            return round_index, outcome
    return None, None


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
    policy: StudioFeePolicy,
) -> None:
    if int(node["parentIndex"]) != NODE_ROOT_SENTINEL:
        raise AllocationTreeMalformed("AllocationTreeMalformed")
    if bool(node["onAcceptance"]):
        raise ExternalAllocationInvalid("ExternalOnAcceptanceNotSupported")

    external_fee_params = decode_external_message_fee_params(node["feeParams"])
    gas_limit = int(external_fee_params["gasLimit"])
    max_gas_price = int(external_fee_params["maxGasPrice"])
    if gas_limit == 0 or max_gas_price == 0:
        raise ExternalAllocationInvalid("ExternalAllocationInvalid")
    if gas_limit < int(policy.min_external_gas_limit):
        raise ExternalAllocationInvalid("ExternalGasLimitBelowMinimum")

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


def _record_fee_contribution(
    accounting: dict[str, Any],
    depositor: str | None,
    *,
    primary: int = 0,
    overlay: int = 0,
    message: int = 0,
) -> None:
    pools = {
        "primary": max(0, int(primary)),
        "overlay": max(0, int(overlay)),
        "message": max(0, int(message)),
    }
    amount = sum(pools.values())
    if amount == 0:
        return
    if not depositor:
        accounting["untracked_contributions"] = (
            int(accounting.get("untracked_contributions", 0) or 0) + amount
        )
        untracked_pools = accounting.setdefault("untracked_contribution_pools", {})
        for pool, pool_amount in pools.items():
            untracked_pools[pool] = int(untracked_pools.get(pool, 0) or 0) + pool_amount
        return

    contributions = accounting.setdefault("contributions", [])
    if (
        contributions
        and str(contributions[-1].get("depositor", "")).lower()
        == str(depositor).lower()
        and all(pool in contributions[-1] for pool in pools)
    ):
        contributions[-1]["amount"] = int(contributions[-1].get("amount", 0)) + amount
        for pool, pool_amount in pools.items():
            contributions[-1][pool] = (
                int(contributions[-1].get(pool, 0) or 0) + pool_amount
            )
        return
    if len(contributions) >= MAX_CONTRIBUTION_SEGMENTS:
        raise ContributionSegmentsFull("ContributionSegmentsFull")
    contributions.append({"depositor": depositor, "amount": amount, **pools})


def _allocate_fee_refund(
    accounting: dict[str, Any],
    *,
    primary: int = 0,
    overlay: int = 0,
    message: int = 0,
    unattributed: int = 0,
) -> list[dict[str, Any]]:
    refunds_by_pool = {
        "primary": max(0, int(primary)),
        "overlay": max(0, int(overlay)),
        "message": max(0, int(message)),
    }
    unattributed = max(0, int(unattributed))
    refundable = sum(refunds_by_pool.values()) + unattributed
    if refundable == 0:
        return []

    raw_contributions = [
        item
        for item in accounting.get("contributions") or []
        if isinstance(item, dict) and item.get("depositor")
    ]
    typed = bool(raw_contributions) and all(
        all(pool in item for pool in refunds_by_pool) for item in raw_contributions
    )
    if not typed:
        return _allocate_legacy_fee_refund(accounting, refundable)

    settlements: list[dict[str, Any]] = []
    for pool, pool_refundable in refunds_by_pool.items():
        if pool_refundable <= 0:
            continue
        contributions = [
            {
                "depositor": item.get("depositor"),
                "amount": max(0, int(item.get(pool, 0) or 0)),
            }
            for item in raw_contributions
        ]
        tracked_total = sum(item["amount"] for item in contributions)
        tracked_refundable = min(pool_refundable, tracked_total)
        consumed = tracked_total - tracked_refundable
        cumulative = 0
        for item in contributions:
            end = cumulative + item["amount"]
            amount = 0
            if cumulative >= consumed:
                amount = item["amount"]
            elif end > consumed:
                amount = end - consumed
            if amount > 0:
                settlements.append(
                    {
                        "recipient": item["depositor"],
                        "amount": amount,
                        "source": f"{pool}-fifo",
                    }
                )
            cumulative = end
    remainder = refundable - sum(item["amount"] for item in settlements)
    fallback = accounting.get("sender")
    if remainder > 0 and fallback:
        settlements.append(
            {"recipient": fallback, "amount": remainder, "source": "remainder"}
        )
    return settlements


def _allocate_legacy_fee_refund(
    accounting: dict[str, Any],
    refundable: int,
) -> list[dict[str, Any]]:
    contributions = [
        {
            "depositor": item.get("depositor"),
            "amount": max(0, int(item.get("amount", 0) or 0)),
        }
        for item in accounting.get("contributions") or []
        if isinstance(item, dict) and item.get("depositor")
    ]
    tracked_total = sum(item["amount"] for item in contributions)
    tracked_refundable = min(refundable, tracked_total)
    consumed = tracked_total - tracked_refundable
    settlements: list[dict[str, Any]] = []
    cumulative = 0
    for item in contributions:
        end = cumulative + item["amount"]
        amount = 0
        if cumulative >= consumed:
            amount = item["amount"]
        elif end > consumed:
            amount = end - consumed
        if amount > 0:
            settlements.append(
                {"recipient": item["depositor"], "amount": amount, "source": "fifo"}
            )
        cumulative = end

    remainder = refundable - sum(item["amount"] for item in settlements)
    fallback = accounting.get("sender")
    if remainder > 0 and fallback:
        settlements.append(
            {"recipient": fallback, "amount": remainder, "source": "remainder"}
        )
    return settlements


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
    # FeeManager owns this counter. A caller may include the field in the ABI
    # struct, but addFeesDistribution deliberately never copies it into
    # storage; only receipt/storage charging advances it later.
    fees["executionConsumed"] = 0
    total_message_fees = int(fees["totalMessageFees"])
    execution_budget_total = int(fees["executionBudgetPerRound"]) * get_leader_rounds(
        fees
    )
    primary_required = max(0, int(required_fee_value) - total_message_fees)
    primary_budget = max(0, int(fee_value) - total_message_fees)
    overlay_budget = _carve_time_unit_overlay_reserve(
        current_reserve=0,
        cumulative_primary=primary_budget,
        execution_budget=execution_budget_total,
        incoming_primary=primary_budget,
        split_bps=policy.time_unit_overlay_bps,
    )
    return {
        "version": 2,
        "source": source,
        "status": "active",
        "policy_snapshot": policy.to_snapshot(),
        "sender": sender,
        "user_value": int(user_value),
        "num_of_initial_validators": int(num_of_validators),
        "paid_fee_value": int(fee_value),
        "required_fee_value": int(required_fee_value),
        "primary_fee_required": primary_required,
        "primary_fee_budget": primary_budget,
        "primary_fee_spent": 0,
        "primary_fee_refunded": 0,
        "execution_budget_total": execution_budget_total,
        "time_unit_overlay_budget": overlay_budget,
        "funding_overlay_bps": int(policy.time_unit_overlay_bps),
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
        "external_message_fee_settled": 0,
        "external_message_events": [],
        "external_message_fee_payouts": [],
        "appeal_bonds": [],
        "appeal_bonds_total": 0,
        "appeal_bonds_payout_total": 0,
        "appeal_bond_sender_refunded": 0,
        "appeal_funding_total": 0,
        "appeal_charge_surplus_refunded": 0,
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
        "contributions": (
            [
                {
                    "depositor": sender,
                    "amount": primary_budget + total_message_fees,
                    "primary": primary_budget - overlay_budget,
                    "overlay": overlay_budget,
                    "message": total_message_fees,
                }
            ]
            if sender and primary_budget + total_message_fees > 0
            else []
        ),
        "untracked_contributions": 0,
        "untracked_contribution_pools": {},
        "fees_distribution": fees,
        "message_allocations": [
            _serializable_message_allocation(allocation)
            for allocation in message_allocations
        ],
        # Studio resolves the canonical FlatArrays subtree locally, so an
        # ordinary child with no descendants is open Mode 1, not Consensus'
        # distinct sealed-child failure state. Keep the explicit bit for
        # imported/recovered sealed state without conflating the two.
        "message_allocations_restricted": False,
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
        "maxPriceGenPerTimeUnit": int(fee_params["maxPriceGenPerTimeUnit"]),
        "storageFeeMaxGasPrice": int(fee_params["storageFeeMaxGasPrice"]),
        "receiptFeeMaxGasPrice": int(fee_params["receiptFeeMaxGasPrice"]),
    }


def _genvm_message_fee_params(
    node: dict[str, Any],
    fees_distribution: dict[str, Any],
) -> dict[str, Any]:
    if int(node["messageType"]) == MESSAGE_TYPE_EXTERNAL:
        decoded = decode_external_message_fee_params(node["feeParams"])
        return {
            "External": {
                "gas_limit": int(decoded["gasLimit"]),
                "max_gas_price": int(decoded["maxGasPrice"]),
            },
        }

    decoded = decode_internal_message_fee_params(node["feeParams"])
    return {
        "Internal": {
            "leader_timeunits_allocation": int(decoded["leaderTimeunitsAllocation"]),
            "validator_timeunits_allocation": int(
                decoded["validatorTimeunitsAllocation"]
            ),
            "execution_budget_per_round": int(decoded["executionBudgetPerRound"]),
            "rotations": [int(rotation) for rotation in decoded["rotations"]],
            "max_price_gen_per_time_unit": int(decoded["maxPriceGenPerTimeUnit"]),
            "storage_fee_max_gas_price": int(decoded["storageFeeMaxGasPrice"]),
            "receipt_fee_max_gas_price": int(decoded["receiptFeeMaxGasPrice"]),
        },
    }


def _genvm_message_allocation_node(
    node: dict[str, Any],
    address_factory: Callable[[str], Any] | None,
    fees_distribution: dict[str, Any],
) -> dict[str, Any]:
    return {
        "recipient": _genvm_recipient(node, address_factory),
        "call_key": _genvm_call_key(node),
        "budget": int(node["budget"]),
        "on": _genvm_message_on(node),
        "fee_params": _genvm_message_fee_params(node, fees_distribution),
        "children": [],
    }


def _genvm_message_on(node: dict[str, Any]) -> str:
    # `decided` is GenVM's name for the lifecycle Studio calls `accepted`.
    if int(node["messageType"]) == MESSAGE_TYPE_EXTERNAL:
        return "finalized"
    return "decided" if bool(node["onAcceptance"]) else "finalized"


def _genvm_recipient(
    node: dict[str, Any],
    address_factory: Callable[[str], Any] | None,
) -> Any | None:
    recipient = str(node["recipient"]).lower()
    if recipient == "":
        return None
    return address_factory(recipient) if address_factory else recipient


def _genvm_call_key(node: dict[str, Any]) -> bytes | None:
    call_key = _normalize_call_key(node["callKey"])
    if call_key == CALL_KEY_WILDCARD:
        return None
    return bytes.fromhex(call_key.removeprefix("0x"))


def _genvm_unmetered_message_fee_allocation() -> list[dict[str, Any]]:
    budget = 2**200
    internal_fee_params = {
        "Internal": {
            "leader_timeunits_allocation": 0,
            "validator_timeunits_allocation": 0,
            "execution_budget_per_round": 0,
            "rotations": [0],
            "max_price_gen_per_time_unit": 1,
            "storage_fee_max_gas_price": 2**200,
            "receipt_fee_max_gas_price": 2**200,
        },
    }
    return [
        {
            "recipient": None,
            "call_key": None,
            "budget": budget,
            "on": "finalized",
            "fee_params": {
                "External": {
                    "gas_limit": 2**200,
                    "max_gas_price": 0,
                },
            },
            "children": [],
        },
        {
            "recipient": None,
            "call_key": None,
            "budget": budget,
            "on": "finalized",
            "fee_params": {
                "Internal": {
                    **internal_fee_params["Internal"],
                    "storage_fee_max_gas_price": 20,
                    "receipt_fee_max_gas_price": 20,
                },
            },
            "children": [],
        },
        {
            "recipient": None,
            "call_key": None,
            "budget": budget,
            "on": "decided",
            "fee_params": internal_fee_params,
            "children": [],
        },
    ]


def _genvm_external_legacy_fallback_message_fee_allocation() -> dict[str, Any]:
    return {
        "recipient": None,
        "call_key": None,
        "budget": 2**200,
        "on": "finalized",
        "fee_params": {
            "External": {
                "gas_limit": 2**200,
                "max_gas_price": 0,
            },
        },
        "children": [],
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
        message.get("callKey", EMPTY_CALL_KEY)
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
    executor: str | None = None,
    funding_offset: int = 0,
) -> int:
    if bool(message.get("onAcceptance", False)):
        raise ExternalAllocationInvalid("ExternalOnAcceptanceNotSupported")

    allocations = accounting.get("message_allocations") or []
    if not allocations:
        return 0

    candidates = list(_matching_root_allocations(allocations, message))
    if not candidates:
        raise MessageNoMatchingAllocation("MessageNoMatchingAllocation")

    selected: tuple[int, dict[str, Any], int, int, int] | None = None
    for index, allocation in candidates:
        external_fee_params = decode_external_message_fee_params(
            allocation["feeParams"]
        )
        gas_limit = int(external_fee_params["gasLimit"])
        max_gas_price = int(external_fee_params["maxGasPrice"])
        locked_price = (
            min(policy.receipt_gas_price, max_gas_price)
            if policy.receipt_gas_price > 0
            else 0
        )
        if locked_price <= 0:
            raise ExternalAllocationInvalid("ExternalExecutionPriceUnavailable")
        reservation = gas_limit * locked_price
        key = str(index)
        consumed = int(accounting.setdefault("allocation_consumed", {}).get(key, 0))
        attempted = consumed + reservation
        if attempted <= int(allocation["budget"]):
            selected = (index, allocation, gas_limit, locked_price, reservation)
            break

    # Consensus falls through exact -> wildcard when the exact allocation is
    # exhausted. If records existed but neither has room, the reveal fails.
    if selected is None:
        raise MessageBudgetExceeded("MessageBudgetExceeded")

    index, _allocation, gas_limit, locked_price, reservation = selected
    key = str(index)
    consumed = int(accounting.setdefault("allocation_consumed", {}).get(key, 0))
    attempted = consumed + reservation
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
    event = {
        "recipient": str(message.get("recipient", "")).lower(),
        "callKey": _normalize_call_key(message.get("callKey", EMPTY_CALL_KEY)),
        "allocationIndex": index,
        "gasLimit": gas_limit,
        "lockedGasPrice": locked_price,
        "reservation": reservation,
        "gasUsed": gas_used if reimburse else 0,
        "reimbursement": reimbursement if reimburse else 0,
        "remainder": remainder if reimburse else 0,
        "executionRecorded": bool(reimburse),
        "fundingOffset": max(0, int(funding_offset)),
        "fundingOwners": _message_pool_owner_slices(
            accounting,
            max(0, int(funding_offset)),
            reservation,
        ),
    }
    accounting.setdefault("external_message_events", []).append(event)
    if reimburse:
        accounting["external_message_fee_settled"] = (
            int(accounting.get("external_message_fee_settled", 0)) + reservation
        )
        _record_external_message_fee_payouts(accounting, event, executor)
    return reservation if reimburse else 0


def _record_external_message_fee_payouts(
    accounting: dict[str, Any],
    event: dict[str, Any],
    executor: str | None,
) -> None:
    reimbursement = int(event.get("reimbursement", 0) or 0)
    remainder = int(event.get("remainder", 0) or 0)
    fallback_depositor = accounting.get("sender")
    executor_recipient = executor or fallback_depositor
    payouts = accounting.setdefault("external_message_fee_payouts", [])
    if reimbursement > 0 and executor_recipient:
        payouts.append(
            {
                "recipient": executor_recipient,
                "amount": reimbursement,
                "source": "external-executor-reimbursement",
            }
        )
    if remainder > 0:
        owner_refunds = _external_remainder_owner_slices(event, reimbursement)
        if not owner_refunds and fallback_depositor:
            owner_refunds = [{"recipient": fallback_depositor, "amount": remainder}]
        for owner_refund in owner_refunds:
            recipient = owner_refund.get("recipient")
            amount = int(owner_refund.get("amount", 0) or 0)
            if recipient and amount > 0:
                payouts.append(
                    {
                        "recipient": recipient,
                        "amount": amount,
                        "source": "external-execution-remainder",
                    }
                )


def _message_pool_owner_slices(
    accounting: dict[str, Any],
    offset: int,
    amount: int,
) -> list[dict[str, Any]]:
    """Resolve an exact message-pool range to its ordered funders."""
    remaining_offset = max(0, int(offset))
    remaining = max(0, int(amount))
    owners: list[dict[str, Any]] = []
    for contribution in accounting.get("contributions") or []:
        if not isinstance(contribution, dict):
            continue
        pool_amount = max(0, int(contribution.get("message", 0) or 0))
        if remaining_offset >= pool_amount:
            remaining_offset -= pool_amount
            continue
        available = pool_amount - remaining_offset
        owned = min(remaining, available)
        recipient = contribution.get("depositor")
        if owned > 0 and recipient:
            owners.append({"recipient": recipient, "amount": owned})
        remaining -= owned
        remaining_offset = 0
        if remaining == 0:
            break

    if remaining > 0 and accounting.get("sender"):
        owners.append({"recipient": accounting["sender"], "amount": remaining})
    return owners


def _external_remainder_owner_slices(
    event: dict[str, Any],
    reimbursement: int,
) -> list[dict[str, Any]]:
    """The executor consumes the reservation prefix; its tail returns to funders."""
    skip = max(0, int(reimbursement))
    refunds: list[dict[str, Any]] = []
    for owner in event.get("fundingOwners") or []:
        if not isinstance(owner, dict):
            continue
        owned = max(0, int(owner.get("amount", 0) or 0))
        if skip >= owned:
            skip -= owned
            continue
        refundable = owned - skip
        skip = 0
        recipient = owner.get("recipient")
        if refundable > 0 and recipient:
            refunds.append({"recipient": recipient, "amount": refundable})
    return refunds


def _next_external_funding_offset(
    accounting: dict[str, Any],
    baseline: int,
) -> int:
    next_offset = max(0, int(baseline))
    for event in accounting.get("external_message_events") or []:
        if not isinstance(event, dict):
            continue
        if event.get("executionRecorded") or event.get("unreserved"):
            continue
        next_offset = max(
            next_offset,
            int(event.get("fundingOffset", 0) or 0)
            + int(event.get("reservation", 0) or 0),
        )
    return next_offset


def _reindex_unexecuted_external_funding(accounting: dict[str, Any]) -> None:
    cursor = max(0, int(accounting.get("message_fee_consumed", 0) or 0))
    for event in accounting.get("external_message_events") or []:
        if not isinstance(event, dict):
            continue
        if event.get("executionRecorded") or event.get("unreserved"):
            continue
        reservation = max(0, int(event.get("reservation", 0) or 0))
        event["fundingOffset"] = cursor
        event["fundingOwners"] = _message_pool_owner_slices(
            accounting,
            cursor,
            reservation,
        )
        cursor += reservation


def _find_unrefunded_external_message_event(
    accounting: dict[str, Any],
    message: dict[str, Any],
) -> int | None:
    recipient = str(message.get("recipient", "")).lower()
    call_key = _normalize_call_key(message.get("callKey", EMPTY_CALL_KEY))
    for index, event in enumerate(accounting.get("external_message_events") or []):
        if (
            event.get("failureRefunded")
            or event.get("refunded")
            or event.get("unreserved")
        ):
            continue
        if str(event.get("recipient", "")).lower() != recipient:
            continue
        if _normalize_call_key(event.get("callKey", EMPTY_CALL_KEY)) != call_key:
            continue
        return index
    return None


def _find_unexecuted_external_message_event(
    accounting: dict[str, Any],
    message: dict[str, Any],
) -> int | None:
    recipient = str(message.get("recipient", "")).lower()
    call_key = _normalize_call_key(message.get("callKey", EMPTY_CALL_KEY))
    for index, event in enumerate(accounting.get("external_message_events") or []):
        if event.get("executionRecorded") or event.get("unreserved"):
            continue
        if str(event.get("recipient", "")).lower() != recipient:
            continue
        if _normalize_call_key(event.get("callKey", EMPTY_CALL_KEY)) != call_key:
            continue
        return index
    return None


def _unreserve_external_message_fee(
    accounting: dict[str, Any],
    message: dict[str, Any],
) -> tuple[int, int, int]:
    # Reveal unwind applies only to a reservation that has not executed yet.
    # Once execution is recorded Consensus consumes the whole reservation and
    # routes its reimbursement/remainder payouts; rolling that event back here
    # would leave those terminal payouts inconsistent with the fee ledger.
    event_index = _find_unexecuted_external_message_event(accounting, message)
    if event_index is None:
        return 0, 0, 0

    event = accounting.setdefault("external_message_events", [])[event_index]
    reservation = int(event.get("reservation", 0) or 0)
    reimbursement = int(event.get("reimbursement", 0) or 0)
    remainder = int(event.get("remainder", 0) or 0)
    allocation_index = str(event.get("allocationIndex"))

    allocation_consumed = accounting.setdefault("allocation_consumed", {})
    consumed = int(allocation_consumed.get(allocation_index, 0) or 0)
    remaining = max(0, consumed - reservation)
    if remaining > 0:
        allocation_consumed[allocation_index] = remaining
    else:
        allocation_consumed.pop(allocation_index, None)
    accounting["message_fee_consumed"] = max(
        0,
        int(accounting.get("message_fee_consumed", 0)) - reservation,
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
    accounting["external_message_fee_settled"] = max(
        0,
        int(accounting.get("external_message_fee_settled", 0)) - reservation,
    )
    event["unreserved"] = True
    _reindex_unexecuted_external_funding(accounting)
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
    remaining = max(0, consumed - int(amount))
    if remaining > 0:
        allocation_consumed[key] = remaining
    else:
        allocation_consumed.pop(key, None)


def _resolve_allocation(
    allocations: list[dict[str, Any]],
    message: dict[str, Any],
) -> tuple[int, dict[str, Any]] | None:
    return next(_matching_root_allocations(allocations, message), None)


def _matching_root_allocations(
    allocations: list[dict[str, Any]],
    message: dict[str, Any],
):
    message_type = int(message.get("messageType", MESSAGE_TYPE_INTERNAL))
    recipient = str(message.get("recipient", "")).lower()
    call_key = _normalize_call_key(message.get("callKey", EMPTY_CALL_KEY))

    wanted_call_keys = [call_key]
    if call_key != CALL_KEY_WILDCARD:
        wanted_call_keys.append(CALL_KEY_WILDCARD)
    for wanted_call_key in wanted_call_keys:
        for index, raw_allocation in enumerate(allocations):
            allocation = _serializable_message_allocation(raw_allocation)
            if int(allocation["parentIndex"]) != NODE_ROOT_SENTINEL:
                continue
            if int(allocation["messageType"]) != message_type:
                continue
            if str(allocation["recipient"]).lower() != recipient:
                continue
            if _normalize_call_key(allocation["callKey"]) == wanted_call_key:
                yield index, allocation


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
        if accounting.get("message_allocations") and not bool(
            message.get("useBalance", False)
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
    if _receipt_budget_exhaustion_reason(receipt) in {
        "ExecutionBudgetExceeded",
        "MessageBudgetExceeded",
    }:
        return False
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
        EMPTY_CALL_KEY,
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
        "useBalance": bool(_message_field(message, "use_balance", "useBalance", False)),
        "gasUsed": int(_message_field(message, "gas_used", "gasUsed", 0) or 0),
    }


def discovered_message_fee_allocations(
    receipt: Any,
    fees_distribution: dict[str, Any],
    policy: StudioFeePolicy | None = None,
) -> list[dict[str, Any]]:
    """Build an exact root allocation tree from an unmetered simulation.

    GenVM must receive an allocation before it can emit a fee-aware message,
    which otherwise makes a transaction-specific estimate circular.  Studio's
    estimate endpoint first executes without fee metering, then converts the
    observed recipient/call-key pairs into the same FlatArrays roots accepted
    by Consensus and reruns the transaction under those exact roots.

    This intentionally does not create a wildcard recipient or call key.  The
    returned preset therefore funds only the messages the representative
    execution actually emitted.
    """
    policy = policy or StudioFeePolicy()
    fees = normalize_fees_distribution(fees_distribution)
    observed_messages = [
        _receipt_pending_transaction_fee_payload(raw)
        for raw in _receipt_pending_transactions(receipt)
    ]
    if not observed_messages:
        return []
    if len(observed_messages) > int(
        policy.max_allocated_messages or MAX_ALLOCATED_MESSAGES_CAP
    ):
        raise TooManyMessages("TooManyMessages")
    # Balance-funded internal messages reserve their declared budget from the
    # emitting contract. They are deliberately outside the sender-funded
    # message bucket and must not gain a root allocation during discovery.
    messages = [
        message
        for message in observed_messages
        if not (
            int(message["messageType"]) == MESSAGE_TYPE_INTERNAL
            and bool(message.get("useBalance", False))
        )
    ]
    if not messages:
        return []

    leader_timeunits = int(fees["leaderTimeunitsAllocation"])
    validator_timeunits = int(fees["validatorTimeunitsAllocation"])
    if not (
        int(policy.min_propose_timeunits)
        <= leader_timeunits
        <= int(policy.max_propose_timeunits)
    ):
        leader_timeunits = DEFAULT_LEADER_TIMEUNITS_ALLOCATION
    if not (
        int(policy.min_commit_timeunits)
        <= validator_timeunits
        <= int(policy.max_commit_timeunits)
    ):
        validator_timeunits = DEFAULT_VALIDATOR_TIMEUNITS_ALLOCATION

    rotations = [int((fees.get("rotations") or [0])[0])]
    # The allocation becomes the child transaction's complete per-round
    # execution reservoir.  Funding it at the GenVM startup floor leaves no
    # budget for the child's first storage write (a constructor that stores one
    # field therefore finalizes as ``out_of storage``).  Apply the same 20%
    # execution headroom used by recommended_fee_preset so discovered children
    # can execute beyond startup rather than merely enter the VM.
    execution_budget = _with_padding(
        max(
            int(fees["executionBudgetPerRound"]),
            int(policy.message_fee_params_budget_floor()),
            int(policy.genvm_start_budget_floor()),
        ),
        DEFAULT_PRICE_CAP_HEADROOM_BPS,
    )
    max_gen_price = max(
        int(fees["maxPriceGenPerTimeUnit"]),
        _with_cap_headroom(int(policy.gen_per_time_unit)),
    )
    storage_price_cap = max(
        int(fees["storageFeeMaxGasPrice"]),
        _with_cap_headroom(int(policy.storage_unit_price)),
    )
    receipt_price_cap = max(
        int(fees["receiptFeeMaxGasPrice"]),
        _with_cap_headroom(int(policy.receipt_gas_price)),
    )

    grouped: dict[tuple[int, str, str], dict[str, Any]] = {}
    for message in messages:
        message_type = int(message["messageType"])
        recipient = str(message["recipient"]).lower()
        call_key = _normalize_call_key(message["callKey"])
        on_acceptance = bool(message["onAcceptance"])
        key = (message_type, recipient, call_key)
        existing = grouped.get(key)
        if existing is not None:
            if bool(existing["onAcceptance"]) != on_acceptance:
                raise MessageEmissionPhaseMismatch("MessageEmissionPhaseMismatch")
            existing["count"] += 1
            continue
        grouped[key] = {
            "messageType": message_type,
            "recipient": recipient,
            "callKey": call_key,
            "onAcceptance": on_acceptance,
            "count": 1,
        }

    allocations: list[dict[str, Any]] = []
    for item in grouped.values():
        count = int(item.pop("count"))
        if int(item["messageType"]) == MESSAGE_TYPE_EXTERNAL:
            gas_limit = max(
                int(policy.default_external_gas_limit),
                int(policy.min_external_gas_limit),
            )
            max_gas_price = receipt_price_cap
            if max_gas_price <= 0:
                raise ExternalAllocationInvalid("ExternalExecutionPriceUnavailable")
            fee_params = encode(
                [EXTERNAL_MESSAGE_FEE_PARAMS_ABI_TYPE],
                [(gas_limit, max_gas_price)],
            )
            budget = gas_limit * max_gas_price * count
            on_acceptance = False
        else:
            internal_params = {
                "leaderTimeunitsAllocation": leader_timeunits,
                "validatorTimeunitsAllocation": validator_timeunits,
                "appealRounds": 0,
                "executionBudgetPerRound": execution_budget,
                "rotations": rotations,
                "maxPriceGenPerTimeUnit": max_gen_price,
                "storageFeeMaxGasPrice": storage_price_cap,
                "receiptFeeMaxGasPrice": receipt_price_cap,
            }
            fee_params = encode(
                [INTERNAL_MESSAGE_FEE_PARAMS_ABI_TYPE],
                [
                    (
                        internal_params["leaderTimeunitsAllocation"],
                        internal_params["validatorTimeunitsAllocation"],
                        internal_params["appealRounds"],
                        internal_params["executionBudgetPerRound"],
                        internal_params["rotations"],
                        internal_params["maxPriceGenPerTimeUnit"],
                        internal_params["storageFeeMaxGasPrice"],
                        internal_params["receiptFeeMaxGasPrice"],
                    )
                ],
            )
            per_message_budget = min_message_primary_fees(internal_params, policy)
            budget = per_message_budget * count
            on_acceptance = bool(item["onAcceptance"])

        allocations.append(
            {
                "messageType": int(item["messageType"]),
                "onAcceptance": on_acceptance,
                "parentIndex": NODE_ROOT_SENTINEL,
                "recipient": item["recipient"],
                "callKey": item["callKey"],
                "budget": budget,
                "feeParams": fee_params,
            }
        )

    return allocations


def fee_accounting_with_discovered_messages(
    accounting: dict[str, Any],
    receipt: Any,
    policy: StudioFeePolicy | None = None,
) -> dict[str, Any]:
    """Rebuild simulation accounting around exact discovered allocations."""
    policy = _accounting_policy(accounting, policy)
    fees = normalize_fees_distribution(accounting.get("fees_distribution") or {})
    allocations = discovered_message_fee_allocations(receipt, fees, policy)
    if not allocations:
        return accounting

    fees["totalMessageFees"] = sum(
        int(allocation["budget"])
        for allocation in allocations
        if int(allocation["parentIndex"]) == NODE_ROOT_SENTINEL
    )
    fees["executionBudgetPerRound"] = max(
        int(fees["executionBudgetPerRound"]),
        int(policy.message_fee_params_budget_floor())
        + int(policy.receipt_gas_price) * DEFAULT_PARENT_MESSAGE_RECEIPT_HEADROOM,
        int(policy.genvm_start_budget_floor()),
    )
    fee_value = required_fee_deposit(
        fees,
        int(accounting.get("num_of_initial_validators") or VALIDATORS_PER_ROUND[0]),
        policy,
    )
    return create_fee_accounting(
        fees_distribution=fees,
        message_allocations=allocations,
        num_of_validators=int(
            accounting.get("num_of_initial_validators") or VALIDATORS_PER_ROUND[0]
        ),
        submitted_value=int(accounting.get("user_value", 0) or 0) + fee_value,
        user_value=int(accounting.get("user_value", 0) or 0),
        sender=accounting.get("sender"),
        policy=policy,
        allow_low_execution_budget=True,
    )


def _execution_fee_buckets(consumed: FeeBucketConsumption) -> list[int]:
    return [_bucket_value(consumed, GENVM_EXECUTION_FEE_BUCKET)]


def _chargeable_execution_fee_buckets(
    consumed: FeeBucketConsumption,
    fee_report: dict[str, Any] | None,
    policy: StudioFeePolicy,
    receipt: Any | None = None,
) -> list[int]:
    if not isinstance(fee_report, dict):
        return [
            _bucket_value(consumed, GENVM_EXECUTION_FEE_BUCKET),
            0,
        ]

    return [
        _receipt_report_chargeable_fee(fee_report),
        _chargeable_storage_fee(receipt, consumed, fee_report),
    ]


def _chargeable_storage_fee(
    receipt: Any | None,
    consumed: FeeBucketConsumption,
    fee_report: dict[str, Any] | None = None,
) -> int:
    if receipt is not None and not _receipt_execution_allows_messages(receipt):
        return 0
    shared_execution = _bucket_value(consumed, GENVM_EXECUTION_FEE_BUCKET)
    if not isinstance(fee_report, dict):
        return shared_execution
    # GenVM v0.3 shares receipt and storage charges in bucket 0. Its complete
    # receipt charge includes the always-reserved empty message-reveal cost,
    # whereas Consensus charges only the proposal and any actual reveal. The
    # remainder is therefore the storage/event-write fee supplied to Consensus.
    genvm_receipt_fee = int(fee_report.get("totalStudioMeteredFee", 0) or 0)
    return max(0, shared_execution - genvm_receipt_fee)


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


def _bucket_value(consumed: FeeBucketConsumption, index: int) -> int:
    if isinstance(consumed, dict):
        return int(consumed.get(_bucket_name(index), 0) or 0)
    return int(consumed[index]) if len(consumed) > index else 0


def _has_bucket(consumed: FeeBucketConsumption, index: int) -> bool:
    if isinstance(consumed, dict):
        return _bucket_name(index) in consumed
    return len(consumed) > index


def _bucket_name(index: int) -> str:
    return GENVM_FEE_BUCKET_NAMES[index] if index < len(GENVM_FEE_BUCKET_NAMES) else ""


def _execution_budget_per_round(accounting: dict[str, Any]) -> int:
    try:
        fees = normalize_fees_distribution(accounting.get("fees_distribution") or {})
    except FeeValidationError:
        return 0
    return int(fees["executionBudgetPerRound"])


def _genvm_fee_bucket_report(
    consumed: FeeBucketConsumption,
    *,
    execution_budget_per_round: int = 0,
) -> dict[str, Any]:
    execution = _bucket_value(consumed, GENVM_EXECUTION_FEE_BUCKET)
    message = _bucket_value(consumed, GENVM_MESSAGE_FEE_BUCKET)
    nondet_output_bytes = _bucket_value(consumed, GENVM_NONDET_OUTPUT_BYTES_BUCKET)
    submitted_message_bytes = _bucket_value(
        consumed, GENVM_SUBMITTED_MESSAGE_BYTES_BUCKET
    )
    submitted_message_count = _bucket_value(
        consumed, GENVM_SUBMITTED_MESSAGE_COUNT_BUCKET
    )
    buckets = []
    bucket_definitions = (
        (GENVM_EXECUTION_FEE_BUCKET, "execution", "fee"),
        (GENVM_MESSAGE_FEE_BUCKET, "message", "fee"),
        (
            GENVM_NONDET_OUTPUT_BYTES_BUCKET,
            "nondeterministicOutputBytes",
            "bytes",
        ),
        (
            GENVM_SUBMITTED_MESSAGE_BYTES_BUCKET,
            "submittedMessageBytes",
            "bytes",
        ),
        (
            GENVM_SUBMITTED_MESSAGE_COUNT_BUCKET,
            "submittedMessageCount",
            "count",
        ),
    )
    for index, name, unit in bucket_definitions:
        if _has_bucket(consumed, index):
            buckets.append(
                {
                    "index": index,
                    "name": name,
                    "unit": unit,
                    "consumed": _bucket_value(consumed, index),
                }
            )
    report = {
        "layout": "genvm-v0.3",
        "execution": execution,
        "message": message,
        "nondeterministicOutputBytes": nondet_output_bytes,
        "submittedMessageBytes": submitted_message_bytes,
        "submittedMessageCount": submitted_message_count,
        "totalExecution": execution,
        "totalWithMessage": execution + message,
        "buckets": buckets,
    }
    overrun = max(0, execution - execution_budget_per_round)
    report.update(
        {
            "executionBudgetPerRound": execution_budget_per_round,
            "executionBudgetRemaining": max(0, execution_budget_per_round - execution),
            "executionBudgetOverrun": overrun,
            "executionBudgetExceeded": overrun > 0,
        }
    )
    return report


def _chargeable_execution_bucket_report(
    consumed: list[int],
    *,
    execution_budget_per_round: int = 0,
) -> dict[str, Any]:
    receipt = _bucket_value(consumed, 0)
    storage = _bucket_value(consumed, 1)
    total_execution = receipt + storage
    overrun = max(0, total_execution - execution_budget_per_round)
    return {
        "layout": "consensus-chargeable",
        "receipt": receipt,
        "storage": storage,
        "totalExecution": total_execution,
        "totalWithMessage": total_execution,
        "buckets": [
            {"index": 0, "name": "receipt", "unit": "fee", "consumed": receipt},
            {"index": 1, "name": "storage", "unit": "fee", "consumed": storage},
        ],
        "executionBudgetPerRound": execution_budget_per_round,
        "executionBudgetRemaining": max(
            0, execution_budget_per_round - total_execution
        ),
        "executionBudgetOverrun": overrun,
        "executionBudgetExceeded": overrun > 0,
    }


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
    external_settled = int(accounting.get("external_message_fee_settled", 0) or 0)
    declared_consumed = max(0, total_consumed - external_settled)
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
        report["externalSettled"] = external_settled
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
    emits_messages = bool(message_allocations) or int(fees["totalMessageFees"]) > 0
    execution_floor = policy.message_fee_params_budget_floor()
    if emits_messages:
        execution_floor += (
            policy.receipt_gas_price * DEFAULT_PARENT_MESSAGE_RECEIPT_HEADROOM
        )
    execution_floor = max(execution_floor, policy.genvm_start_budget_floor())

    observed_execution = _observed_chargeable_execution_fee(accounting, report)
    genvm_execution_required = _int_report_field(
        (
            report.get("genvmBuckets")
            if isinstance(report.get("genvmBuckets"), dict)
            else {}
        ),
        "execution",
    )
    observed_budget_requirement = max(
        observed_execution,
        genvm_execution_required,
    )
    recommended_execution = int(fees["executionBudgetPerRound"])
    if observed_budget_requirement > 0:
        recommended_execution = max(
            _with_padding(observed_budget_requirement, padding_bps),
            execution_floor,
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
            "genvmExecutionRequired": genvm_execution_required,
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


def _receipt_data_fees_consumed(
    receipt: Any | None,
) -> FeeBucketConsumption | None:
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
        if isinstance(consumed, dict):
            return {str(name): int(value) for name, value in consumed.items()}
        return [int(value) for value in consumed]
    totals = genvm_result.get("data_fee_bucket_totals")
    remaining = genvm_result.get("data_fees_remaining")
    if totals is None or remaining is None:
        return None
    if isinstance(totals, dict) != isinstance(remaining, dict):
        return None
    if isinstance(totals, dict):
        if totals.keys() != remaining.keys():
            return None
        return {
            str(name): max(0, int(total) - int(remaining[name]))
            for name, total in totals.items()
        }
    return [max(0, int(total) - int(rest)) for total, rest in zip(totals, remaining)]


def _receipt_execution_policy(
    receipt: Any | None,
    fallback: StudioFeePolicy,
) -> StudioFeePolicy:
    genvm_result = _receipt_genvm_result(receipt)
    snapshot = (
        genvm_result.get(FEE_POLICY_SNAPSHOT_KEY)
        if isinstance(genvm_result, dict)
        else None
    )
    if isinstance(snapshot, dict):
        try:
            return StudioFeePolicy.from_snapshot(snapshot)
        except (KeyError, TypeError, ValueError):
            pass
    return fallback


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
    validate_receipt_admission_caps(receipt, policy)
    receipt_bytes = policy.estimate_propose_receipt_bytes(eq_outputs_length)
    proposal_gas = policy.estimate_propose_receipt_gas(receipt_bytes)
    proposal_fee = proposal_gas * policy.receipt_gas_price
    empty_message_reveal_fee = (
        policy.estimate_message_reveal_gas(0, 0) * policy.receipt_gas_price
    )
    report: dict[str, Any] = {
        "receiptGasPrice": policy.receipt_gas_price,
        "proposalReceipt": {
            "eqBlocksOutputsLength": eq_outputs_length,
            "receiptBytes": receipt_bytes,
            "estimatedGas": proposal_gas,
            "fee": proposal_fee,
        },
        "totalEstimatedFee": proposal_fee,
        # GenVM reserves an empty message reveal at startup even when the
        # execution emits no messages. Keep this full meter value separate
        # from the lower Consensus charge so shared-bucket storage can be
        # recovered exactly.
        "totalStudioMeteredFee": proposal_fee + empty_message_reveal_fee,
    }

    submitted_messages, message_reports = (
        _receipt_submitted_messages_and_reports(receipt, message_payloads)
        if _receipt_execution_allows_messages(receipt)
        else ([], [])
    )
    if submitted_messages:
        message_bytes = len(encode([SUBMITTED_MESSAGE_ABI_TYPE], [submitted_messages]))
        if (
            policy.max_submitted_messages_bytes > 0
            and message_bytes > policy.max_submitted_messages_bytes
        ):
            raise SubmittedMessagesTooLarge(
                "SubmittedMessagesTooLarge"
                f"({message_bytes},{policy.max_submitted_messages_bytes})"
            )
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
        report["totalStudioMeteredFee"] = proposal_fee + message_fee

    consumed = _receipt_data_fees_consumed(receipt)
    if consumed is not None and _has_bucket(
        consumed, GENVM_SUBMITTED_MESSAGE_BYTES_BUCKET
    ):
        message_count = (
            _bucket_value(consumed, GENVM_SUBMITTED_MESSAGE_COUNT_BUCKET)
            if _has_bucket(consumed, GENVM_SUBMITTED_MESSAGE_COUNT_BUCKET)
            else len(submitted_messages)
        )
        report["totalStudioMeteredFee"] = _genvm_receipt_metered_fee(
            receipt,
            policy,
            consumed,
            message_count=message_count,
        )

    return report


def _genvm_receipt_metered_fee(
    receipt: Any,
    policy: StudioFeePolicy,
    consumed: FeeBucketConsumption,
    *,
    message_count: int,
) -> int:
    """Reproduce the receipt-related part of GenVM v0.3 bucket 0.

    Storage, receipt writes, nondeterministic output, and event writes share
    the same enforced reservoir. GenVM's metadata counters let Studio remove
    only the receipt portion and pass the remaining storage/event-write charge
    to Consensus without weakening the unified cap.
    """

    receipt_fee_per_byte = int(policy.receipt_gas_price) * int(
        policy.calldata_gas_per_byte
    )
    changed_slot_fee = int(policy.receipt_gas_price) * int(policy.gas_per_changed_slot)
    if isinstance(consumed, list):
        # Positional receipts retain the pre-named-bucket startup layout.
        total = int(policy._legacy_genvm_start_budget_floor())

        eq_outputs = _receipt_eq_outputs(receipt)
        if eq_outputs:
            total += sum(
                (64 + ((len(output) + 31) // 32) * 32) * receipt_fee_per_byte
                for output in eq_outputs
            )
        else:
            raw_output_bytes = _bucket_value(consumed, GENVM_NONDET_OUTPUT_BYTES_BUCKET)
            if raw_output_bytes > 0:
                total += (
                    64 + ((raw_output_bytes + 31) // 32) * 32
                ) * receipt_fee_per_byte
    else:
        total = int(policy.genvm_start_budget_floor())
        nondet_output_bytes = _bucket_value(consumed, GENVM_NONDET_OUTPUT_BYTES_BUCKET)
        total += (
            max(0, nondet_output_bytes - GENVM_NONDET_OUTPUT_HEADER_BYTES)
            * receipt_fee_per_byte
        )
        if message_count > 0:
            total += (
                policy.fixed_message_reveal_gas
                + policy.intrinsic_gas
                + policy.bootloader_overhead
            ) * policy.receipt_gas_price

    submitted_message_bytes = _bucket_value(
        consumed, GENVM_SUBMITTED_MESSAGE_BYTES_BUCKET
    )
    total += submitted_message_bytes * receipt_fee_per_byte
    total += max(0, int(message_count)) * changed_slot_fee
    return total


def _receipt_eq_blocks_outputs_length(receipt: Any) -> int:
    genvm_result = _receipt_genvm_result(receipt)
    if isinstance(genvm_result, dict):
        explicit = genvm_result.get("eq_blocks_outputs_length")
        if explicit is None:
            explicit = genvm_result.get("eqBlocksOutputsLength")
        if explicit is not None:
            return max(0, int(explicit))

    explicit_outputs = _receipt_value(receipt, "eq_blocks_outputs")
    if isinstance(explicit_outputs, str) and explicit_outputs.startswith("0x"):
        return len(bytes.fromhex(explicit_outputs.removeprefix("0x")))

    return len(_encode_eq_blocks_outputs(_receipt_eq_outputs(receipt)))


def _receipt_submitted_messages(receipt: Any) -> list[tuple[Any, ...]]:
    submitted, _ = _receipt_submitted_messages_and_reports(receipt)
    return submitted


def _enforce_submitted_messages_cap(
    messages: list[dict[str, Any]],
    policy: StudioFeePolicy,
) -> int:
    if not messages:
        return 0
    submitted, _ = _receipt_submitted_messages_and_reports({}, messages)
    message_bytes = len(encode([SUBMITTED_MESSAGE_ABI_TYPE], [submitted]))
    if (
        policy.max_submitted_messages_bytes > 0
        and message_bytes > policy.max_submitted_messages_bytes
    ):
        raise SubmittedMessagesTooLarge(
            "SubmittedMessagesTooLarge"
            f"({message_bytes},{policy.max_submitted_messages_bytes})"
        )
    return message_bytes


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
        submitted_fee_params = fee_params
        if message_type == MESSAGE_TYPE_EXTERNAL:
            submitted_fee_params = b""
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
            EMPTY_CALL_KEY,
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
                submitted_fee_params,
                declared_budget,
                allocation_subtree,
                call_key,
                bool(_message_field(message, "use_balance", "useBalance", False)),
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
                "useBalance": bool(
                    _message_field(message, "use_balance", "useBalance", False)
                ),
            }
        )
    return submitted, reports


def _message_effect_identities(
    tx_id: str,
    messages: list[dict[str, Any]],
) -> list[tuple[str, str]]:
    """Mirror Messages._logicalOccurrence and _effectDescriptor.

    The application key deliberately excludes fee/value/lifecycle fields. Any
    drift in those fields is caught by the full SubmittedMessage descriptor for
    the same stable occurrence, exactly as in Consensus.
    """

    submitted, _ = _receipt_submitted_messages_and_reports({}, messages)
    tx_id_bytes = _bytes32_field(tx_id)
    tuple_type = SUBMITTED_MESSAGE_ABI_TYPE.removesuffix("[]")
    occurrence_counts: dict[bytes, int] = {}
    identities: list[tuple[str, str]] = []
    zero_address = "0x" + ("0" * 40)

    for submitted_message in submitted:
        message_type = int(submitted_message[0])
        recipient = str(submitted_message[1]).lower()
        data_hash = keccak(bytes(submitted_message[3]))
        deploy_salt = (
            int(submitted_message[5])
            if message_type == MESSAGE_TYPE_INTERNAL and recipient == zero_address
            else 0
        )
        application_key = keccak(
            encode(
                ["uint8", "address", "bytes32", "uint256"],
                [message_type, recipient, data_hash, deploy_salt],
            )
        )
        ordinal = occurrence_counts.get(application_key, 0)
        occurrence_counts[application_key] = ordinal + 1
        occurrence = keccak(
            encode(
                ["bytes32", "bytes32", "uint256"],
                [tx_id_bytes, application_key, ordinal],
            )
        )
        descriptor = keccak(encode([tuple_type], [submitted_message]))
        identities.append(("0x" + occurrence.hex(), "0x" + descriptor.hex()))

    return identities


def message_effect_identities(
    tx_id: str,
    messages: list[dict[str, Any]],
) -> list[tuple[str, str]]:
    """Return Consensus-compatible stable occurrence and descriptor pairs.

    Message delivery, value reservation, and fee accounting all need to share
    one logical identity. Keeping that identity authority here prevents the
    asynchronous Studio worker from inventing a second retry key.
    """

    return _message_effect_identities(tx_id, messages)


def acceptance_dispatch_pending(accounting: dict[str, Any] | None) -> bool:
    """Whether the current agreed decision still owes its acceptance phase."""

    if not isinstance(accounting, dict):
        return False
    generation = accounting.get("active_message_generation")
    return (
        isinstance(generation, dict)
        and bool(generation.get("acceptanceDispatchRequired", False))
        and not bool(generation.get("acceptanceDispatched", False))
    )


def message_novelty_mask(
    accounting: dict[str, Any],
    tx_id: str,
    messages: list[dict[str, Any]],
) -> list[bool]:
    identities = _message_effect_identities(tx_id, messages)
    descriptors = accounting.get("message_effect_descriptors") or {}
    delivered = accounting.get("message_effect_delivered") or {}
    novelty = []
    for occurrence, descriptor in identities:
        expected = descriptors.get(occurrence)
        if expected is not None and expected != descriptor:
            raise MessageEffectDescriptorMismatch(
                "MessageEffectDescriptorMismatch"
                f"({tx_id},{occurrence},{expected},{descriptor})"
            )
        novelty.append(not bool(delivered.get(occurrence, False)))
    return novelty


def prepare_reveal_message_generation(
    accounting: dict[str, Any],
    tx_id: str,
    messages: list[dict[str, Any]],
    *,
    policy: StudioFeePolicy | None = None,
) -> dict[str, Any]:
    """Retire the prior reveal and charge only fresh logical occurrences."""

    updated = copy.deepcopy(accounting)
    prior_generation = updated.get("active_message_generation")
    if isinstance(prior_generation, dict):
        updated = unwind_reveal_message_fees(
            updated,
            list(prior_generation.get("messages") or []),
            acceptance_dispatched=bool(
                prior_generation.get("acceptanceDispatched", False)
            ),
        )

    identities = _message_effect_identities(tx_id, messages)
    novelty = message_novelty_mask(updated, tx_id, messages)
    descriptors = updated.setdefault("message_effect_descriptors", {})
    for occurrence, descriptor in identities:
        descriptors.setdefault(occurrence, descriptor)

    novel_messages = [
        message for message, is_novel in zip(messages, novelty) if is_novel
    ]
    updated = record_reveal_message_fees(
        updated,
        novel_messages,
        policy=policy,
    )
    updated["active_message_generation"] = {
        "messages": _message_accounting_json_safe(messages),
        "occurrences": [occurrence for occurrence, _ in identities],
        "novelty": novelty,
        # Acceptance itself is a durable helper-chain phase, including when
        # the receipt contains no accepted children. The worker clears this
        # only after the helper call and local child insertion both succeed.
        "acceptanceDispatchRequired": True,
        "acceptanceDispatched": False,
    }
    return updated


def _message_accounting_json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return "0x" + value.hex()
    if isinstance(value, bytearray):
        return "0x" + bytes(value).hex()
    if isinstance(value, list):
        return [_message_accounting_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_message_accounting_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _message_accounting_json_safe(item) for key, item in value.items()}
    return value


def discard_active_message_generation(
    accounting: dict[str, Any],
) -> dict[str, Any]:
    updated = copy.deepcopy(accounting)
    prior_generation = updated.pop("active_message_generation", None)
    if not isinstance(prior_generation, dict):
        return updated
    updated = unwind_reveal_message_fees(
        updated,
        list(prior_generation.get("messages") or []),
        acceptance_dispatched=bool(prior_generation.get("acceptanceDispatched", False)),
    )
    updated.pop("active_message_generation", None)
    updated["message_fees_recorded_at_reveal"] = True
    return updated


def mark_message_effects_delivered(
    accounting: dict[str, Any],
    tx_id: str,
    messages: list[dict[str, Any]],
    on: str,
) -> dict[str, Any]:
    updated = copy.deepcopy(accounting)
    identities = _message_effect_identities(tx_id, messages)
    # Reuse descriptor validation before installing an irreversible tombstone.
    message_novelty_mask(updated, tx_id, messages)
    delivered = updated.setdefault("message_effect_delivered", {})
    for message, (occurrence, descriptor) in zip(messages, identities):
        if bool(message.get("onAcceptance", False)) != (on == "accepted"):
            continue
        updated.setdefault("message_effect_descriptors", {}).setdefault(
            occurrence, descriptor
        )
        delivered[occurrence] = True

    updated.setdefault("message_phase_emitted", {})[on] = True
    generation = updated.get("active_message_generation")
    if on == "accepted" and isinstance(generation, dict):
        generation["acceptanceDispatched"] = True
    return updated


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
        "call_key": getattr(pending_transaction, "call_key", EMPTY_CALL_KEY),
        "allocation_subtree": getattr(pending_transaction, "allocation_subtree", []),
        "use_balance": getattr(pending_transaction, "use_balance", False),
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
        return _bytes_from_string_field(value)
    return bytes(value)


def _bytes_from_string_field(value: str) -> bytes:
    raw = value.removeprefix("0x")
    if value.startswith("0x"):
        return _bytes_from_hex_field(raw)
    if raw == "":
        return b""
    return _bytes_from_encoded_text(raw)


def _bytes_from_encoded_text(raw: str) -> bytes:
    try:
        return base64.b64decode(raw, validate=True)
    except Exception:
        return _bytes_from_hex_or_utf8(raw)


def _bytes_from_hex_or_utf8(raw: str) -> bytes:
    try:
        return bytes.fromhex(raw)
    except ValueError:
        return raw.encode("utf-8")


def _bytes_from_hex_field(raw: str) -> bytes:
    try:
        return bytes.fromhex(raw)
    except ValueError:
        return b""


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
    normalized = _normalize_call_key(call_key or EMPTY_CALL_KEY)
    if normalized == CALL_KEY_WILDCARD:
        return normalized
    if normalized != EMPTY_CALL_KEY:
        return normalized

    raw_calldata = _bytes_field(calldata)
    if len(raw_calldata) < 4:
        return EMPTY_CALL_KEY

    return "0x" + raw_calldata[:4].hex().ljust(64, "0")
