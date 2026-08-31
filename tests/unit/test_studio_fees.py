import base64
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import rlp
from eth_abi import encode
from web3 import Web3

from backend.consensus.base import (
    ConsensusAlgorithm,
    _author_message_phase_locally,
    _apply_external_message_freeze_check,
    _apply_message_value_withdrawals_for_phase,
    _child_config_rotation_rounds,
    _dispatch_messages_for_phase,
    _emit_messages,
    _get_messages_data,
    _runtime_rotation_limit,
    _studio_child_transaction_id,
    _validators_in_frozen_selection_pool,
)
from backend.consensus.history import (
    materialize_decision_metadata,
    prepare_appeal_decision_basis,
)
from backend.domain.types import TransactionExecutionMode, TransactionType
from backend.errors.errors import InvalidTransactionError
from backend.database_handler.accounts_manager import _infer_final_round
from backend.database_handler.transactions_processor import (
    TransactionsProcessor,
    get_tx_execution_hash,
)
from backend.protocol_rpc.exceptions import JSONRPCError, NotFoundError
from backend.protocol_rpc.endpoints import (
    _available_appeal_validator_count,
    _current_fee_round,
    _funded_max_rotations,
    _handle_appeal_or_top_up_and_submit,
    _handle_finalize_transaction,
    _handle_top_up_fees,
    _normal_leader_count,
    estimate_latest_appeal_charge,
    get_transaction_lifecycle,
    get_transaction_status_details,
    get_transaction_status,
    _stage_simulated_call_value,
    _simulation_fee_accounting,
    _validate_fee_envelope,
    _with_default_simulation_fees,
    sim_calculate_round_fees,
    sim_estimate_message_reveal_gas,
    sim_estimate_propose_receipt_gas,
    sim_estimate_transaction_fees,
    sim_min_message_primary_fees,
)
from backend.protocol_rpc.fees import (
    ArithmeticOverflow,
    AllocationDuplicateKey,
    AllocationLifecycleBudgetInsufficient,
    AllocationTreeBudgetInconsistent,
    AllocationTreeMalformed,
    AllocationTreeTooDeep,
    BudgetTooLow,
    ContributionSegmentsFull,
    EqOutputsTooLarge,
    ExternalAllocationInvalid,
    FeeValueMustBeNonZero,
    InsufficientFees,
    InvalidAppealBond,
    InvalidAppealRounds,
    InvalidFeeParams,
    InvalidNumOfValidators,
    MaxPriceExceeded,
    MessageBudgetExceeded,
    MessageDeclaredBudgetInsufficient,
    MessageEmissionPhaseMismatch,
    MessageEffectDescriptorMismatch,
    MessageFeeParamsMismatch,
    MessageAllocationsNotEqualBudget,
    MessageAllocationsRestricted,
    MessageFeesReportMismatch,
    MessageNoMatchingAllocation,
    Mode1MessageFeesRequireGenVMPerEmissionSupport,
    message_effect_identities,
    min_message_primary_fees,
    PhaseTimeoutOutOfBounds,
    SubmittedMessagesTooLarge,
    CALL_KEY_WILDCARD,
    EMPTY_CALL_KEY,
    MESSAGE_ALLOCATION_NODE_ABI_TYPE,
    MIN_RECEIPT_BYTES,
    MESSAGE_REVEAL_LENGTH_SLOTS,
    MAX_CONTRIBUTION_SEGMENTS,
    NODE_ROOT_SENTINEL,
    NONDET_OUTPUT_LENGTH_BYTES,
    FEE_ACCOUNTING_KEY,
    FEE_POLICY_SNAPSHOT_KEY,
    PROPOSE_RECEIPT_SLOTS,
    SUBMITTED_MESSAGE_ABI_TYPE,
    DEFAULT_GEN_PER_TIME_UNIT,
    DEFAULT_EXTERNAL_MESSAGE_GAS_LIMIT,
    DEFAULT_PRICE_CAP_HEADROOM_BPS,
    DEFAULT_TIME_UNIT_OVERLAY_BPS,
    DEFAULT_RECEIPT_GAS_PRICE,
    DEFAULT_STORAGE_UNIT_PRICE,
    DEFAULT_TRANSACTION_EXECUTION_BUDGET_PER_ROUND,
    GENVM_UNMETERED_DATA_FEE_BUCKET,
    StudioFeePolicy,
    TopUpCannotExtendSchedule,
    TooManyMessages,
    abort_latest_appeal_admission,
    apply_fee_top_up,
    activate_fee_accounting,
    calculate_appeal_charge,
    calculate_min_appeal_bond,
    calculate_round_fees,
    calculate_time_unit_fees_through_round,
    consume_message_fees,
    create_child_fee_accounting,
    create_fee_accounting,
    decode_external_message_fee_params,
    decode_internal_message_fee_params,
    cancel_fee_accounting,
    default_transaction_fees_for_policy,
    derive_external_message_call_key,
    discovered_message_fee_allocations,
    fee_accounting_with_discovered_messages,
    fill_message_fee_payload_from_allocation,
    mark_message_effects_delivered,
    message_novelty_mask,
    prepare_reveal_message_generation,
    genvm_fee_context,
    genvm_message_fee_allocation,
    get_leader_rounds,
    get_leader_rounds_through_round,
    record_appeal_bond,
    record_external_message_execution_fees,
    record_execution_fee_consumption,
    record_reveal_message_fees,
    refund_failed_external_message_fee,
    required_fee_deposit,
    settle_fee_accounting,
    _settlement_storage_recipient_count,
    stamp_receipt_execution_policy,
    studio_fee_config,
    successful_appeal_profit,
    successful_appeal_reward,
    unwind_reveal_message_fees,
    validate_message_allocations,
    validate_transaction_fee_deposit,
)
from backend.node.types import (
    ExecutionMode,
    ExecutionResultStatus,
    PendingTransaction,
    Receipt,
)
from backend.protocol_rpc.types import (
    DecodedsubmitAppealDataArgs,
    DecodedRollupTransaction,
    DecodedRollupTransactionData,
    DecodedRollupTransactionDataArgs,
    DecodedTopUpFeesDataArgs,
    DecodedFinalizeTransactionDataArgs,
)


def _fees_distribution(
    *,
    leader_timeunits=100,
    validator_timeunits=200,
    appeals=0,
    rotations=None,
    execution_budget_per_round=0,
    execution_consumed=0,
    total_message_fees=0,
    max_price_gen_per_time_unit=0,
    storage_fee_max_gas_price=0,
    receipt_fee_max_gas_price=0,
):
    if rotations is None:
        rotations = [0] * (appeals + 1)
    return {
        "leaderTimeunitsAllocation": leader_timeunits,
        "validatorTimeunitsAllocation": validator_timeunits,
        "appealRounds": appeals,
        "executionBudgetPerRound": execution_budget_per_round,
        "executionConsumed": execution_consumed,
        "totalMessageFees": total_message_fees,
        "rotations": rotations,
        "maxPriceGenPerTimeUnit": max_price_gen_per_time_unit,
        "storageFeeMaxGasPrice": storage_fee_max_gas_price,
        "receiptFeeMaxGasPrice": receipt_fee_max_gas_price,
    }


def _top_up_distribution(**overrides):
    """Pure-funding top-up shape accepted by Consensus after activation."""
    values = {
        "leader_timeunits": 0,
        "validator_timeunits": 0,
        "appeals": 0,
        "rotations": [],
    }
    values.update(overrides)
    return _fees_distribution(**values)


def _required_env_fee_deposit(fees_distribution, num_of_validators=5):
    return required_fee_deposit(
        fees_distribution,
        num_of_validators,
        StudioFeePolicy.from_env(),
    )


def _env_fees_distribution(**overrides):
    policy = StudioFeePolicy.from_env()
    defaults, _ = default_transaction_fees_for_policy(policy)
    values = {
        "leader_timeunits": int(defaults["leaderTimeunitsAllocation"]),
        "validator_timeunits": int(defaults["validatorTimeunitsAllocation"]),
        "appeals": int(defaults["appealRounds"]),
        "rotations": [int(value) for value in defaults["rotations"]],
        "execution_budget_per_round": int(defaults["executionBudgetPerRound"]),
        "max_price_gen_per_time_unit": int(defaults["maxPriceGenPerTimeUnit"]),
        "storage_fee_max_gas_price": int(defaults["storageFeeMaxGasPrice"]),
        "receipt_fee_max_gas_price": int(defaults["receiptFeeMaxGasPrice"]),
    }
    values.update(overrides)
    return _fees_distribution(**values)


def _encode_internal_fee_params(
    *,
    leader_timeunits=5,
    validator_timeunits=10,
    appeals=0,
    execution_budget_per_round=0,
    rotations=None,
    max_price_gen_per_time_unit=1,
    storage_fee_max_gas_price=2**200,
    receipt_fee_max_gas_price=2**200,
):
    if rotations is None:
        rotations = [0] * (appeals + 1)
    return encode(
        ["(uint256,uint256,uint256,uint256,uint256[],uint256,uint256,uint256)"],
        [
            (
                leader_timeunits,
                validator_timeunits,
                appeals,
                execution_budget_per_round,
                rotations,
                max_price_gen_per_time_unit,
                storage_fee_max_gas_price,
                receipt_fee_max_gas_price,
            )
        ],
    )


def _encode_external_fee_params(*, gas_limit=21_000, max_gas_price=10):
    return encode(["(uint256,uint256)"], [(gas_limit, max_gas_price)])


def _external_selector_call_key(selector: bytes) -> str:
    return "0x" + selector.hex().ljust(64, "0")


def _history_receipt(
    *,
    mode: str,
    address: str,
    vote: str | None = None,
    eq_outputs_length: int = 0,
    data_fees_consumed: list[int] | None = None,
    timeout: bool = False,
) -> dict:
    genvm_result = {"eq_blocks_outputs_length": eq_outputs_length}
    if data_fees_consumed is not None:
        genvm_result["data_fees_consumed"] = data_fees_consumed
    if timeout:
        genvm_result["error_code"] = "CONSENSUS_LEADER_EXEC_TIMEOUT"
    return {
        "mode": mode,
        "vote": vote,
        "node_config": {"address": address},
        "result": base64.b64encode(b"\x00timeout" if timeout else b"\x00ok").decode(),
        "execution_result": "ERROR" if timeout else "SUCCESS",
        "genvm_result": genvm_result,
        "pending_transactions": [],
    }


def _root_parent_index() -> int:
    return NODE_ROOT_SENTINEL


def test_derive_external_message_call_key_preserves_explicit_value():
    explicit = "0x" + "99" * 32
    assert derive_external_message_call_key(explicit, b"\x12\x34\x56\x78") == explicit


def test_derive_external_message_call_key_from_calldata_selector_when_omitted():
    selector = b"\x12\x34\x56\x78"
    assert derive_external_message_call_key(
        EMPTY_CALL_KEY,
        selector + b"payload",
    ) == _external_selector_call_key(selector)


def test_derive_external_message_call_key_keeps_empty_key_without_selector():
    assert (
        derive_external_message_call_key(EMPTY_CALL_KEY, b"\x12\x34") == EMPTY_CALL_KEY
    )


def test_derive_external_message_call_key_preserves_consensus_wildcard():
    assert (
        derive_external_message_call_key(CALL_KEY_WILDCARD, b"\x12\x34\x56\x78")
        == CALL_KEY_WILDCARD
    )


def _allocation(
    *,
    message_type=1,
    on_acceptance=None,
    parent_index=NODE_ROOT_SENTINEL,
    recipient="0x2222222222222222222222222222222222222222",
    call_key=CALL_KEY_WILDCARD,
    budget=55,
    fee_params=None,
):
    if on_acceptance is None:
        on_acceptance = message_type == 1
    if fee_params is None:
        fee_params = (
            _encode_external_fee_params()
            if message_type == 0
            else _encode_internal_fee_params()
        )
    return {
        "messageType": message_type,
        "onAcceptance": on_acceptance,
        "parentIndex": parent_index,
        "recipient": recipient,
        "callKey": call_key,
        "budget": budget,
        "feeParams": fee_params,
    }


@pytest.mark.parametrize(
    "validators,appeals,rotations,expected",
    [
        (5, 0, [0], 1100),
        (5, 0, [1], 2200),
        (5, 0, [2], 3300),
        (5, 0, [3], 4400),
        (5, 1, [0, 0], 4900),
        (5, 1, [1, 0], 6000),
        (5, 1, [0, 1], 7200),
        (5, 1, [1, 1], 8300),
        (5, 1, [2, 1], 9400),
        (5, 1, [1, 2], 10600),
        (5, 1, [2, 2], 11700),
        (5, 1, [3, 3], 15100),
        (5, 2, [0, 0, 0], 12300),
        (5, 2, [1, 0, 0], 13400),
        (5, 2, [0, 1, 0], 14600),
        (5, 2, [0, 0, 1], 17000),
        (5, 2, [1, 1, 1], 20400),
        (5, 2, [2, 1, 0], 16800),
        (5, 2, [0, 2, 1], 21600),
        (5, 2, [1, 2, 3], 32100),
        (5, 2, [2, 2, 2], 28500),
        (5, 2, [3, 3, 3], 36600),
        (5, 2, [0, 2, 4], 35700),
        (5, 3, [0, 0, 0, 0], 26900),
        (5, 3, [1, 0, 0, 0], 28000),
        (5, 3, [0, 1, 0, 0], 29200),
        (5, 3, [0, 0, 1, 0], 31600),
        (5, 3, [0, 0, 0, 1], 36400),
        (5, 3, [1, 1, 1, 1], 44500),
        (5, 3, [2, 1, 0, 1], 40900),
        (5, 3, [1, 2, 1, 2], 56300),
        (5, 3, [2, 2, 2, 2], 62100),
        (5, 3, [3, 2, 1, 0], 39500),
        (5, 3, [3, 3, 3, 3], 79700),
        (5, 4, [0, 0, 0, 0, 0], 55900),
        (5, 4, [1, 0, 0, 0, 0], 57000),
        (5, 4, [0, 1, 0, 0, 0], 58200),
        (5, 4, [0, 0, 1, 0, 0], 60600),
        (5, 4, [0, 0, 0, 1, 0], 65400),
        (5, 4, [0, 0, 0, 0, 1], 75000),
        (5, 4, [1, 1, 1, 1, 1], 92600),
        (5, 4, [2, 1, 2, 1, 2], 117500),
        (5, 4, [2, 2, 2, 2, 2], 129300),
        (5, 4, [3, 3, 3, 3, 3], 166000),
        (5, 4, [0, 1, 2, 3, 4], 172500),
        (5, 5, [0, 0, 0, 0, 0, 0], 113700),
        (5, 5, [1, 0, 0, 0, 0, 0], 114800),
        (5, 5, [0, 1, 0, 0, 0, 0], 116000),
        (5, 5, [0, 0, 1, 0, 0, 0], 118400),
        (5, 5, [0, 0, 0, 1, 0, 0], 123200),
        (5, 5, [0, 0, 0, 0, 1, 0], 132800),
        (5, 5, [0, 0, 0, 0, 0, 1], 152000),
        (5, 5, [1, 1, 1, 1, 1, 1], 188700),
        (5, 5, [2, 1, 0, 1, 2, 1], 204200),
        (5, 5, [2, 2, 2, 2, 2, 2], 263700),
        (5, 5, [3, 2, 1, 0, 1, 2], 222000),
        (5, 5, [3, 3, 3, 3, 3, 3], 338700),
        (5, 5, [0, 2, 4, 0, 0, 2], 213700),
        (23, 0, [0], 4700),
        (23, 1, [0, 0], 8500),
        (23, 2, [0, 2, 4], 39300),
        (1537, 0, [0], 307500),
        (1537, 1, [0, 0], 311300),
    ],
)
def test_calculate_round_fees_matches_consensus_budget_cases(
    validators, appeals, rotations, expected
):
    fees_distribution = _fees_distribution(appeals=appeals, rotations=rotations)
    configured_sizes = [5, 7, 11, 13, 23, 25, 47, 49, 95, 97, 191]
    appeal_profit_reserve = sum(
        successful_appeal_profit(
            (rotations[appeal_ordinal + 1] + 1)
            * (100 + configured_sizes[(appeal_ordinal + 1) * 2] * 200)
        )
        for appeal_ordinal in range(appeals)
    )

    assert calculate_round_fees(fees_distribution, validators) == (
        expected + appeal_profit_reserve
    )


def test_calculate_round_fees_rejects_invalid_validator_count():
    fees_distribution = _fees_distribution()

    with pytest.raises(InvalidNumOfValidators):
        calculate_round_fees(fees_distribution, 6)


def test_calculate_round_fees_rejects_invalid_appeal_rounds():
    fees_distribution = _fees_distribution(appeals=2, rotations=[1])

    with pytest.raises(InvalidAppealRounds):
        calculate_round_fees(fees_distribution, 5)


def test_calculate_round_fees_rejects_uint256_work_overflow():
    fees_distribution = _fees_distribution(
        leader_timeunits=2**256 - 1,
        validator_timeunits=1,
    )

    with pytest.raises(ArithmeticOverflow):
        calculate_round_fees(fees_distribution, 5)


def test_calculate_round_fees_rejects_uint256_execution_budget_overflow():
    fees_distribution = _fees_distribution(
        execution_budget_per_round=2**256 - 1,
    )

    with pytest.raises(ArithmeticOverflow):
        calculate_round_fees(fees_distribution, 5)


def test_successful_appeal_reward_uses_consensus_overflow_avoiding_arrangement():
    # bond * 5 would overflow here, but AppealEconomics computes bond * 2 +
    # bond / 2 and the result is representable.
    bond = (2**256 - 1) // 4
    assert successful_appeal_profit(bond) == bond * 3 // 2


def test_successful_appeal_reward_rejects_unrepresentable_result():
    with pytest.raises(ArithmeticOverflow):
        successful_appeal_profit((2**256 - 1) // 2)


def test_calculate_round_fees_applies_gen_per_time_unit_multiplier():
    fees_distribution = _fees_distribution(max_price_gen_per_time_unit=12)
    policy = StudioFeePolicy(gen_per_time_unit=10)

    # Consensus reserves at the submitted cap (12), while validating that the
    # live price (10) does not exceed it.
    assert calculate_round_fees(fees_distribution, 5, policy=policy) == 13200
    assert (
        calculate_round_fees(
            _fees_distribution(
                execution_budget_per_round=50,
                max_price_gen_per_time_unit=12,
            ),
            5,
            policy=policy,
        )
        == 13250
    )


def test_calculate_later_round_fees_uses_normal_round_rotation_ordinal():
    fees_distribution = _fees_distribution(
        appeals=2,
        rotations=[0, 1, 2],
        max_price_gen_per_time_unit=1,
    )

    # Raw consensus round 4 is normal-round ordinal 2. Its configured two
    # rotations price three attempts at the round-4 committee size (23).
    assert calculate_round_fees(fees_distribution, 23, round=4) == 3 * (100 + 23 * 200)


def test_nondefault_initial_committee_keeps_later_rounds_on_absolute_fee_ladder():
    fees_distribution = _fees_distribution(appeals=1, rotations=[0, 0])

    # Round zero honors the admitted seven-seat committee. The appeal and
    # replacement normal rounds remain the protocol's raw-round 7/11 seats;
    # they do not shift to 11/13 merely because round zero started at seven.
    assert calculate_round_fees(fees_distribution, 7) == 8_750


def test_sim_min_message_primary_fees_decodes_canonical_v06_abi(monkeypatch):
    monkeypatch.setenv("GENLAYER_STUDIO_GEN_PER_TIME_UNIT", "1")
    monkeypatch.setenv("GENLAYER_STUDIO_TIME_UNIT_OVERLAY_BPS", "1500")
    fee_params = _encode_internal_fee_params(
        max_price_gen_per_time_unit=1,
        storage_fee_max_gas_price=2,
        receipt_fee_max_gas_price=3,
    )

    assert sim_min_message_primary_fees("0x" + fee_params.hex()) == "64"


def test_calculate_round_fees_reserves_profit_and_grosses_up_only_work():
    fees_distribution = _fees_distribution(
        appeals=1,
        rotations=[0, 0],
        max_price_gen_per_time_unit=10,
    )
    policy = StudioFeePolicy(
        gen_per_time_unit=8,
        time_unit_overlay_bps=DEFAULT_TIME_UNIT_OVERLAY_BPS,
    )

    work = 4_900 * 10
    profit_reserve = successful_appeal_profit(2_300) * 10
    overlay = work * 1_500 // 8_500
    assert calculate_round_fees(fees_distribution, 5, policy=policy) == (
        work + overlay + profit_reserve
    )


def test_calculate_round_fees_prices_odd_bond_before_profit_rounding():
    fees_distribution = _fees_distribution(
        leader_timeunits=101,
        validator_timeunits=200,
        appeals=1,
        rotations=[0, 0],
        max_price_gen_per_time_unit=2,
    )
    policy = StudioFeePolicy(gen_per_time_unit=2)
    work = (1_101 + 1_501 + 2_301) * 2
    priced_bond = 2_301 * 2
    exact_profit = successful_appeal_profit(priced_bond)

    assert exact_profit == 6_903
    assert calculate_round_fees(fees_distribution, 5, policy=policy) == (
        work + exact_profit
    )


def test_settlement_uses_funding_time_overlay_split_despite_live_drift():
    submission_policy = StudioFeePolicy(
        gen_per_time_unit=1,
        time_unit_overlay_bps=1_000,
    )
    fees_distribution = _fees_distribution(max_price_gen_per_time_unit=1)
    deposit = required_fee_deposit(fees_distribution, 5, submission_policy)
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=deposit,
        user_value=0,
        policy=submission_policy,
    )

    assert deposit == 1_222
    assert accounting["time_unit_overlay_budget"] == 122

    lower_split, lower_refund = settle_fee_accounting(
        accounting,
        actual_final_round=0,
        num_of_validators=5,
        policy=StudioFeePolicy(gen_per_time_unit=1, time_unit_overlay_bps=500),
    )
    higher_split, higher_refund = settle_fee_accounting(
        accounting,
        actual_final_round=0,
        num_of_validators=5,
        policy=StudioFeePolicy(gen_per_time_unit=1, time_unit_overlay_bps=2_000),
    )

    assert lower_split["time_unit_overlay_requested"] == 122
    assert lower_split["time_unit_overlay_spent"] == 122
    assert lower_refund == 0
    assert higher_split["time_unit_overlay_requested"] == 122
    assert higher_split["time_unit_overlay_spent"] == 122
    assert higher_refund == 0


def test_calculate_round_fees_reserves_at_gen_cap_without_submission_rejection():
    assert (
        calculate_round_fees(
            _fees_distribution(max_price_gen_per_time_unit=5),
            5,
            policy=StudioFeePolicy(gen_per_time_unit=10),
        )
        == 5_500
    )

    with pytest.raises(MaxPriceExceeded):
        calculate_round_fees(
            _fees_distribution(storage_fee_max_gas_price=5),
            5,
            policy=StudioFeePolicy(storage_unit_price=10),
        )

    with pytest.raises(MaxPriceExceeded):
        calculate_round_fees(
            _fees_distribution(receipt_fee_max_gas_price=5),
            5,
            policy=StudioFeePolicy(receipt_gas_price=10),
        )


def test_calculate_round_fees_adds_execution_budget_per_leader_round():
    fees_distribution = _fees_distribution(
        appeals=1,
        rotations=[1, 2],
        execution_budget_per_round=50,
    )

    assert get_leader_rounds(fees_distribution) == 6
    assert calculate_round_fees(fees_distribution, 5) == 21250


def test_sim_calculate_round_fees_exposes_decimal_canonical_quote(monkeypatch):
    monkeypatch.setenv("GENLAYER_STUDIO_GEN_PER_TIME_UNIT", "1")
    monkeypatch.setenv("GENLAYER_STUDIO_STORAGE_UNIT_PRICE", "0")
    monkeypatch.setenv("GENLAYER_STUDIO_RECEIPT_GAS_PRICE", "0")
    monkeypatch.setenv("GENLAYER_STUDIO_TIME_UNIT_OVERLAY_BPS", "1500")
    fees_distribution = _fees_distribution(
        appeals=1,
        rotations=[0, 0],
        max_price_gen_per_time_unit=1,
    )

    assert sim_calculate_round_fees(fees_distribution, 5, 0) == "9214"


def test_sim_receipt_metering_views_match_consensus_formulas(monkeypatch):
    monkeypatch.setenv("GENLAYER_STUDIO_RECEIPT_GAS_PRICE", "2")

    proposal = sim_estimate_propose_receipt_gas(0)
    reveal = sim_estimate_message_reveal_gas(352, 1)

    assert proposal == {
        "receiptBytes": "1024",
        "gas": "314384",
        "fee": "628768",
    }
    assert reveal == {
        "gas": "187632",
        "fee": "375264",
    }


def test_time_unit_fees_through_round_refunds_unused_appeal_budget():
    fees_distribution = _fees_distribution(appeals=2, rotations=[0, 0, 0])

    assert get_leader_rounds_through_round(fees_distribution, 0) == 1
    assert calculate_time_unit_fees_through_round(fees_distribution, 5, 0) == 1100
    assert calculate_round_fees(fees_distribution, 5) == 22800


def test_time_unit_fees_through_round_uses_actual_rotation_history():
    fees_distribution = _fees_distribution(rotations=[2])
    consensus_history = {
        "consensus_results": [
            {"consensus_round": "Accepted"},
        ]
    }

    assert (
        calculate_time_unit_fees_through_round(
            fees_distribution,
            5,
            0,
            consensus_history=consensus_history,
        )
        == 1100
    )


def test_time_unit_fees_through_round_caps_actual_rotations_to_funded_slots():
    fees_distribution = _fees_distribution(rotations=[1])
    consensus_history = {
        "consensus_results": [
            {"consensus_round": "Leader Rotation"},
            {"consensus_round": "Leader Rotation"},
            {"consensus_round": "Accepted"},
        ]
    }

    assert (
        calculate_time_unit_fees_through_round(
            fees_distribution,
            5,
            0,
            consensus_history=consensus_history,
        )
        == 2200
    )


def test_required_fee_deposit_includes_message_fee_bucket():
    fees_distribution = _fees_distribution(total_message_fees=55)

    assert required_fee_deposit(fees_distribution, 5) == 1155


def test_studio_fee_policy_env_defaults_to_fee_enabled(monkeypatch):
    monkeypatch.delenv("GENLAYER_STUDIO_GEN_PER_TIME_UNIT", raising=False)
    monkeypatch.delenv("GENLAYER_STUDIO_STORAGE_UNIT_PRICE", raising=False)
    monkeypatch.delenv("GENLAYER_STUDIO_RECEIPT_GAS_PRICE", raising=False)
    monkeypatch.delenv("GENLAYER_STUDIO_DEFAULT_EXTERNAL_GAS_LIMIT", raising=False)

    policy = StudioFeePolicy.from_env()

    assert policy.gen_per_time_unit == DEFAULT_GEN_PER_TIME_UNIT
    assert policy.storage_unit_price == DEFAULT_STORAGE_UNIT_PRICE
    assert policy.receipt_gas_price == DEFAULT_RECEIPT_GAS_PRICE
    assert policy.default_external_gas_limit == DEFAULT_EXTERNAL_MESSAGE_GAS_LIMIT
    assert policy.time_unit_overlay_bps == DEFAULT_TIME_UNIT_OVERLAY_BPS
    assert policy.fee_accounting_enabled() is True


def test_studio_fee_policy_env_allows_explicit_gasless_mode(monkeypatch):
    monkeypatch.setenv("GENLAYER_STUDIO_GEN_PER_TIME_UNIT", "0")
    monkeypatch.setenv("GENLAYER_STUDIO_STORAGE_UNIT_PRICE", "0")
    monkeypatch.setenv("GENLAYER_STUDIO_RECEIPT_GAS_PRICE", "0")

    policy = StudioFeePolicy.from_env()

    assert policy.gen_per_time_unit == 0
    assert policy.storage_unit_price == 0
    assert policy.receipt_gas_price == 0
    assert policy.fee_accounting_enabled() is False


def test_stage_simulated_call_value_credits_snapshot_only():
    snapshot = SimpleNamespace(balance=7)

    _stage_simulated_call_value(snapshot, 5)

    assert snapshot.balance == 12


def test_studio_fee_policy_matches_consensus_deterministic_receipt_estimators():
    policy = StudioFeePolicy(receipt_gas_price=3, extra_exec_gas=210_000)

    receipt_bytes = policy.estimate_propose_receipt_bytes(123)
    expected_propose_gas = (
        policy.fixed_propose_receipt_gas
        + policy.intrinsic_gas
        + policy.bootloader_overhead
        + (receipt_bytes * policy.calldata_gas_per_byte)
        + (PROPOSE_RECEIPT_SLOTS * policy.gas_per_changed_slot)
    )
    expected_receipt_floor_gas = (
        policy.fixed_propose_receipt_gas
        + policy.intrinsic_gas
        + policy.bootloader_overhead
        + (MIN_RECEIPT_BYTES * policy.calldata_gas_per_byte)
        + (PROPOSE_RECEIPT_SLOTS * policy.gas_per_changed_slot)
    )
    expected_legacy_receipt_gas = expected_receipt_floor_gas - (
        policy.fixed_propose_receipt_gas
    )
    expected_genvm_start_gas = (
        policy.fixed_propose_receipt_gas
        + policy.intrinsic_gas
        + policy.bootloader_overhead
        + (PROPOSE_RECEIPT_SLOTS * policy.gas_per_changed_slot)
        + policy.fixed_message_reveal_gas
        + policy.intrinsic_gas
        + policy.bootloader_overhead
        + (MESSAGE_REVEAL_LENGTH_SLOTS * policy.gas_per_changed_slot)
        + (NONDET_OUTPUT_LENGTH_BYTES * policy.calldata_gas_per_byte)
    )
    expected_measured_receipt_gas = (
        999
        + policy.extra_exec_gas
        + policy.intrinsic_gas
        + policy.bootloader_overhead
        + (receipt_bytes * policy.calldata_gas_per_byte)
        + (99 * policy.gas_per_changed_slot)
    )
    expected_nondet_output_start_gas = (
        NONDET_OUTPUT_LENGTH_BYTES * policy.calldata_gas_per_byte
    )
    expected_message_reveal_gas = (
        policy.fixed_message_reveal_gas
        + policy.intrinsic_gas
        + policy.bootloader_overhead
        + (320 * policy.calldata_gas_per_byte)
        + ((MESSAGE_REVEAL_LENGTH_SLOTS + 2) * policy.gas_per_changed_slot)
    )
    expected_consensus_message_reveal_gas = (
        policy.fixed_message_reveal_gas
        + policy.intrinsic_gas
        + policy.bootloader_overhead
        + (320 * policy.calldata_gas_per_byte)
        + (2 * policy.gas_per_changed_slot)
    )

    assert receipt_bytes == policy.receipt_wrapper_bytes + 123
    assert policy.estimate_propose_receipt_gas(receipt_bytes) == expected_propose_gas
    assert (
        policy.estimate_receipt_gas(
            measured_exec_gas=999,
            calldata_length=receipt_bytes,
            slots_changed=99,
        )
        == expected_measured_receipt_gas
    )
    assert (
        policy.estimate_receipt_gas(
            measured_exec_gas=0,
            calldata_length=MIN_RECEIPT_BYTES,
            slots_changed=PROPOSE_RECEIPT_SLOTS,
        )
        == expected_legacy_receipt_gas
    )
    assert policy.estimate_message_reveal_gas(320, 2) == expected_message_reveal_gas
    assert (
        policy.estimate_consensus_message_reveal_gas(320, 2)
        == expected_consensus_message_reveal_gas
    )
    assert policy.estimate_nondet_output_start_gas() == expected_nondet_output_start_gas
    assert (
        policy.estimate_propose_receipt_gas(MIN_RECEIPT_BYTES)
        == expected_receipt_floor_gas
    )
    assert policy.message_fee_params_budget_floor() == expected_receipt_floor_gas * 3
    assert policy.genvm_start_budget_floor() == expected_genvm_start_gas * 3


def test_studio_fee_config_exposes_default_nonzero_fee_policy():
    policy = StudioFeePolicy(
        gen_per_time_unit=DEFAULT_GEN_PER_TIME_UNIT,
        storage_unit_price=DEFAULT_STORAGE_UNIT_PRICE,
        receipt_gas_price=DEFAULT_RECEIPT_GAS_PRICE,
    )
    distribution, fee_value = default_transaction_fees_for_policy(policy)

    assert distribution["leaderTimeunitsAllocation"] == 100
    assert distribution["validatorTimeunitsAllocation"] == 200
    assert distribution["executionBudgetPerRound"] == max(
        DEFAULT_TRANSACTION_EXECUTION_BUDGET_PER_ROUND,
        policy.message_fee_params_budget_floor(),
    )
    expected_gen_cap = (
        DEFAULT_GEN_PER_TIME_UNIT * DEFAULT_PRICE_CAP_HEADROOM_BPS + 9_999
    ) // 10_000
    expected_storage_cap = (
        DEFAULT_STORAGE_UNIT_PRICE * DEFAULT_PRICE_CAP_HEADROOM_BPS + 9_999
    ) // 10_000
    expected_receipt_cap = (
        DEFAULT_RECEIPT_GAS_PRICE * DEFAULT_PRICE_CAP_HEADROOM_BPS + 9_999
    ) // 10_000
    assert distribution["maxPriceGenPerTimeUnit"] == expected_gen_cap
    assert distribution["storageFeeMaxGasPrice"] == expected_storage_cap
    assert distribution["receiptFeeMaxGasPrice"] == expected_receipt_cap
    assert (
        fee_value == (1100 * expected_gen_cap) + distribution["executionBudgetPerRound"]
    )

    config = studio_fee_config(policy)
    assert config["enabled"] is True
    assert config["policy"]["fixedProposeReceiptGas"] == "210000"
    assert config["policy"]["fixedMessageRevealGas"] == "100000"
    assert config["policy"]["receiptWrapperBytes"] == "1024"
    assert config["policy"]["messageFeeParamsBudgetFloor"] == str(
        policy.message_fee_params_budget_floor()
    )
    assert config["policy"]["timeUnitOverlayBps"] == "0"
    assert config["capabilities"]["messageFees"]["mode1"] == {
        "accounting": True,
        "genvmExecution": False,
    }
    assert config["capabilities"]["messageFees"]["mode2"]["genvmExecution"] is True
    assert config["defaultFees"]["distribution"]["maxPriceGenPerTimeUnit"] == str(
        expected_gen_cap
    )
    assert config["defaultFees"]["feeValue"] == str(fee_value)


def test_validate_transaction_fee_deposit_accepts_exact_fee_and_user_value():
    fees_distribution = _fees_distribution(total_message_fees=55)

    assert (
        validate_transaction_fee_deposit(
            fees_distribution=fees_distribution,
            num_of_validators=5,
            submitted_value=1167,
            user_value=12,
        )
        == 1155
    )


def test_validate_transaction_fee_deposit_rejects_insufficient_fee_value():
    fees_distribution = _fees_distribution(total_message_fees=55)

    with pytest.raises(InsufficientFees):
        validate_transaction_fee_deposit(
            fees_distribution=fees_distribution,
            num_of_validators=5,
            submitted_value=1166,
            user_value=12,
        )


def test_validate_transaction_fee_deposit_rejects_user_value_above_submitted_value():
    fees_distribution = _fees_distribution()

    with pytest.raises(InsufficientFees):
        validate_transaction_fee_deposit(
            fees_distribution=fees_distribution,
            num_of_validators=5,
            submitted_value=10,
            user_value=12,
        )


def test_validate_transaction_fee_deposit_rejects_execution_budget_below_floor():
    fees_distribution = _fees_distribution(execution_budget_per_round=1)
    policy = StudioFeePolicy(receipt_gas_price=1)

    with pytest.raises(BudgetTooLow):
        validate_transaction_fee_deposit(
            fees_distribution=fees_distribution,
            num_of_validators=5,
            submitted_value=10_000_000,
            user_value=0,
            policy=policy,
        )


@pytest.mark.parametrize(
    ("field", "field_index"),
    [
        ("leaderTimeunitsAllocation", 1),
        ("validatorTimeunitsAllocation", 2),
        ("executionBudgetPerRound", 3),
        ("maxPriceGenPerTimeUnit", 4),
        ("storageFeeMaxGasPrice", 5),
        ("receiptFeeMaxGasPrice", 6),
    ],
)
def test_v06_submission_requires_all_six_nonzero_fee_fields(field, field_index):
    fees_distribution = _fees_distribution(
        execution_budget_per_round=1,
        max_price_gen_per_time_unit=1,
        storage_fee_max_gas_price=1,
        receipt_fee_max_gas_price=1,
    )
    fees_distribution[field] = 0
    policy = StudioFeePolicy(
        gen_per_time_unit=1,
        enforce_v06_submission_config=True,
    )

    with pytest.raises(
        FeeValueMustBeNonZero,
        match=rf"FeeValueMustBeNonZero\({field_index}\)",
    ):
        validate_transaction_fee_deposit(
            fees_distribution=fees_distribution,
            num_of_validators=5,
            submitted_value=10_000_000,
            user_value=0,
            policy=policy,
        )


def test_v06_submission_enforces_deployed_phase_timeout_bounds():
    fees_distribution = _fees_distribution(
        leader_timeunits=29,
        validator_timeunits=30,
        execution_budget_per_round=1,
        max_price_gen_per_time_unit=1,
        storage_fee_max_gas_price=1,
        receipt_fee_max_gas_price=1,
    )
    policy = StudioFeePolicy(
        gen_per_time_unit=1,
        enforce_v06_submission_config=True,
        min_propose_timeunits=30,
        max_propose_timeunits=600,
        min_commit_timeunits=30,
        max_commit_timeunits=600,
    )

    with pytest.raises(PhaseTimeoutOutOfBounds, match="29,30,600"):
        validate_transaction_fee_deposit(
            fees_distribution=fees_distribution,
            num_of_validators=5,
            submitted_value=10_000_000,
            user_value=0,
            policy=policy,
        )


@pytest.mark.parametrize("overlay_bps", [-1, 10_000, 10_001])
def test_fee_policy_rejects_non_live_overlay_split(overlay_bps):
    with pytest.raises(ValueError, match="time-unit overlay bps"):
        StudioFeePolicy(time_unit_overlay_bps=overlay_bps)


def test_activation_checks_live_gen_cap_and_locks_all_live_prices():
    submission_policy = StudioFeePolicy(
        gen_per_time_unit=2,
        storage_unit_price=3,
        receipt_gas_price=4,
    )
    fees_distribution = _fees_distribution(
        execution_budget_per_round=10_000_000,
        max_price_gen_per_time_unit=5,
        storage_fee_max_gas_price=10,
        receipt_fee_max_gas_price=10,
    )
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(
            fees_distribution,
            5,
            submission_policy,
        ),
        user_value=0,
        policy=submission_policy,
        allow_low_execution_budget=True,
    )

    canceled, should_cancel = activate_fee_accounting(
        accounting,
        StudioFeePolicy(gen_per_time_unit=6),
    )
    assert should_cancel is True
    assert canceled["activation_price_cap_exceeded"] == {
        "actual": 6,
        "maximum": 5,
    }

    activated, should_cancel = activate_fee_accounting(
        accounting,
        StudioFeePolicy(
            gen_per_time_unit=4,
            storage_unit_price=7,
            receipt_gas_price=8,
        ),
        selection_pool_addresses=[f"0x{index:040x}" for index in range(1, 12)],
    )
    assert should_cancel is False
    assert activated["activation_prices_locked"] is True
    assert activated["locked_prices"] == {
        "genPerTimeUnit": 4,
        "storageUnitPrice": 7,
        "receiptGasPrice": 8,
    }
    assert activated["policy_snapshot"]["gen_per_time_unit"] == 4
    assert activated["policy_snapshot"]["storage_unit_price"] == 7
    assert activated["policy_snapshot"]["receipt_gas_price"] == 8
    assert activated["selection_pool_count"] == 11
    assert activated["selection_pool_addresses"] == [
        f"0x{index:040x}" for index in range(1, 12)
    ]

    relocked, should_cancel = activate_fee_accounting(
        activated,
        StudioFeePolicy(gen_per_time_unit=100),
    )
    assert should_cancel is False
    assert relocked["locked_prices"] == activated["locked_prices"]


@pytest.mark.parametrize(
    ("live_policy", "cap_type", "actual", "maximum"),
    [
        (StudioFeePolicy(storage_unit_price=11), "storageUnitPrice", 11, 10),
        (StudioFeePolicy(receipt_gas_price=12), "receiptGasPrice", 12, 10),
    ],
)
def test_activation_checks_all_signed_price_caps(
    live_policy, cap_type, actual, maximum
):
    fees_distribution = _fees_distribution(
        max_price_gen_per_time_unit=10,
        storage_fee_max_gas_price=10,
        receipt_fee_max_gas_price=10,
    )
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5),
        user_value=0,
    )

    canceled, should_cancel = activate_fee_accounting(accounting, live_policy)

    assert should_cancel is True
    assert canceled["activation_price_cap_type"] == cap_type
    assert canceled["activation_price_cap_exceeded"] == {
        "actual": actual,
        "maximum": maximum,
    }
    assert canceled["activation_cancel_reason"] == (
        f"activation_price_cap_exceeded:{cap_type}"
    )


def test_activation_cancels_when_live_receipt_floor_outgrows_committed_budget():
    submission_policy = StudioFeePolicy(
        receipt_gas_price=1,
        intrinsic_gas=0,
        bootloader_overhead=0,
        gas_per_changed_slot=0,
        calldata_gas_per_byte=0,
        fixed_propose_receipt_gas=10,
    )
    fees_distribution = _fees_distribution(
        execution_budget_per_round=10,
        receipt_fee_max_gas_price=2,
    )
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(
            fees_distribution,
            5,
            submission_policy,
        ),
        user_value=0,
        policy=submission_policy,
    )
    activation_policy = replace(
        submission_policy,
        receipt_gas_price=2,
    )

    canceled, should_cancel = activate_fee_accounting(
        accounting,
        activation_policy,
    )

    assert should_cancel is True
    assert canceled["activation_budget_floor_not_met"] == {
        "actual": 10,
        "minimum": 20,
    }
    assert canceled["activation_cancel_reason"] == "activation_budget_floor_not_met"


def test_runtime_execution_policy_locks_complete_activation_formula():
    submission_policy = StudioFeePolicy(
        gen_per_time_unit=2,
        storage_unit_price=3,
        receipt_gas_price=4,
        calldata_gas_per_byte=5,
        fixed_propose_receipt_gas=6,
    )
    fees_distribution = _fees_distribution(
        execution_budget_per_round=10_000_000,
        max_price_gen_per_time_unit=10,
        storage_fee_max_gas_price=10,
        receipt_fee_max_gas_price=10,
    )
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(
            fees_distribution,
            5,
            submission_policy,
        ),
        user_value=0,
        policy=submission_policy,
        allow_low_execution_budget=True,
    )
    activated, should_cancel = activate_fee_accounting(
        accounting,
        StudioFeePolicy(
            gen_per_time_unit=7,
            storage_unit_price=8,
            receipt_gas_price=9,
            calldata_gas_per_byte=17,
            fixed_propose_receipt_gas=19,
        ),
    )
    assert should_cancel is False

    _, gas_data = genvm_fee_context(
        activated,
        StudioFeePolicy(
            gen_per_time_unit=70,
            storage_unit_price=80,
            receipt_gas_price=90,
            calldata_gas_per_byte=11,
            fixed_propose_receipt_gas=13,
        ),
    )

    assert gas_data["genPerTimeUnit"] == "7"
    assert gas_data["storageUnitPrice"] == "8"
    assert gas_data["receiptGasPerByte"] == str(9 * 17)
    assert gas_data["fixedProposeReceiptGas"] == str(9 * 19)


def test_activated_reveal_uses_committed_message_count_cap(monkeypatch):
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=120),
        num_of_validators=5,
        submitted_value=1220,
        user_value=0,
    )
    activated, should_cancel = activate_fee_accounting(
        accounting,
        StudioFeePolicy(),
    )
    assert should_cancel is False
    monkeypatch.setenv("GENLAYER_STUDIO_MAX_MESSAGES_PER_TX", "1")
    message = {
        "messageType": 1,
        "recipient": "0x2222222222222222222222222222222222222222",
        "onAcceptance": True,
        "feeParams": _encode_internal_fee_params(),
        "declaredBudget": 55,
        "callKey": "0x" + "12" * 32,
    }

    revealed = record_reveal_message_fees(activated, [message, message])

    assert len(revealed["message_consumption_events"]) == 1


def test_endpoint_fee_envelope_rejects_insufficient_fee_deposit():
    fees_distribution = _env_fees_distribution(total_message_fees=55)
    fee_value = _required_env_fee_deposit(fees_distribution) - 1
    decoded = DecodedRollupTransaction(
        from_address="0x1111111111111111111111111111111111111111",
        to_address="0x0000000000000000000000000000000000000000",
        data=DecodedRollupTransactionData(
            function_name="addTransaction",
            args=DecodedRollupTransactionDataArgs(
                sender="0x1111111111111111111111111111111111111111",
                recipient="0x2222222222222222222222222222222222222222",
                num_of_initial_validators=5,
                max_rotations=0,
                data="0x",
                user_value=12,
                fees_distribution=fees_distribution,
            ),
        ),
        type="2",
        nonce=0,
        value=12,
        fee_value=fee_value,
        submitted_value=12 + fee_value,
    )

    with pytest.raises(InvalidTransactionError, match="InsufficientFees"):
        _validate_fee_envelope(decoded)


def test_endpoint_fee_envelope_accepts_exact_fee_deposit():
    fees_distribution = _env_fees_distribution(total_message_fees=55)
    fee_value = _required_env_fee_deposit(fees_distribution)
    decoded = DecodedRollupTransaction(
        from_address="0x1111111111111111111111111111111111111111",
        to_address="0x0000000000000000000000000000000000000000",
        data=DecodedRollupTransactionData(
            function_name="addTransaction",
            args=DecodedRollupTransactionDataArgs(
                sender="0x1111111111111111111111111111111111111111",
                recipient="0x2222222222222222222222222222222222222222",
                num_of_initial_validators=5,
                max_rotations=0,
                data="0x",
                user_value=12,
                fees_distribution=fees_distribution,
            ),
        ),
        type="2",
        nonce=0,
        value=12,
        fee_value=fee_value,
        submitted_value=12 + fee_value,
    )

    _validate_fee_envelope(decoded)


def test_fee_enabled_endpoint_rejects_legacy_submission_without_distribution(
    monkeypatch,
):
    monkeypatch.setattr(
        StudioFeePolicy,
        "from_env",
        classmethod(lambda cls: StudioFeePolicy(gen_per_time_unit=1)),
    )
    decoded = DecodedRollupTransaction(
        from_address="0x1111111111111111111111111111111111111111",
        to_address="0x0000000000000000000000000000000000000000",
        data=DecodedRollupTransactionData(
            function_name="addTransaction",
            args=DecodedRollupTransactionDataArgs(
                sender="0x1111111111111111111111111111111111111111",
                recipient="0x2222222222222222222222222222222222222222",
                num_of_initial_validators=5,
                max_rotations=0,
                data="0x",
                fees_distribution=None,
            ),
        ),
        type="2",
        nonce=0,
        value=0,
    )

    with pytest.raises(InvalidTransactionError, match="FeesDistributionMissing"):
        _validate_fee_envelope(decoded)


def test_endpoint_rejects_zero_salt_for_deploy_salted(monkeypatch):
    monkeypatch.setattr(
        StudioFeePolicy,
        "from_env",
        classmethod(lambda cls: StudioFeePolicy()),
    )
    decoded = DecodedRollupTransaction(
        from_address="0x1111111111111111111111111111111111111111",
        to_address="0x0000000000000000000000000000000000000000",
        data=DecodedRollupTransactionData(
            function_name="deploySalted",
            args=DecodedRollupTransactionDataArgs(
                sender="0x1111111111111111111111111111111111111111",
                recipient="0x2222222222222222222222222222222222222222",
                num_of_initial_validators=5,
                max_rotations=0,
                salt_nonce=0,
                data="0x",
                fees_distribution=None,
            ),
        ),
        type="2",
        nonce=0,
        value=0,
    )

    with pytest.raises(InvalidTransactionError, match="InvalidDeploymentWithSalt"):
        _validate_fee_envelope(decoded)


def test_submission_preserves_largest_funded_round_as_global_rotation_cap():
    fees_distribution = _env_fees_distribution(
        appeals=2,
        rotations=[3, 1, 2],
    )
    decoded = DecodedRollupTransaction(
        from_address="0x1111111111111111111111111111111111111111",
        to_address="0x0000000000000000000000000000000000000000",
        data=DecodedRollupTransactionData(
            function_name="addTransaction",
            args=DecodedRollupTransactionDataArgs(
                sender="0x1111111111111111111111111111111111111111",
                recipient="0x2222222222222222222222222222222222222222",
                num_of_initial_validators=5,
                max_rotations=4,
                data="0x",
                fees_distribution=fees_distribution,
            ),
        ),
        type="2",
        nonce=0,
        value=0,
    )

    assert _funded_max_rotations(decoded, 4) == 3


def test_internal_child_clamps_parent_rotations_to_its_own_fee_schedule():
    data = {
        "fees_distribution": _fees_distribution(
            appeals=2,
            rotations=[2, 0, 1],
        )
    }

    assert _child_config_rotation_rounds(3, data) == 2
    assert _child_config_rotation_rounds(1, {"calldata": b"legacy"}) == 1


def test_runtime_rotations_follow_each_normal_round_schedule_entry():
    accounting = _env_fee_accounting(
        _env_fees_distribution(appeals=2, rotations=[3, 1, 2])
    )
    transaction = SimpleNamespace(
        appealed=False,
        appeal_validators_timeout=False,
        appeal_undetermined=True,
        appeal_leader_timeout=False,
        consensus_history={"consensus_results": [{"consensus_round": "Undetermined"}]},
        data={FEE_ACCOUNTING_KEY: accounting},
        config_rotation_rounds=3,
    )

    # The appeal replays raw round 2, which uses rotations[1], not the global
    # cap and not the smallest entry across the transaction.
    assert _runtime_rotation_limit(transaction) == 1

    # The raw round remains pinned across retries even after the state machine
    # clears the appeal flag before selecting a replacement leader.
    transaction.appeal_undetermined = False
    assert _runtime_rotation_limit(transaction, raw_round=2) == 1

    transaction.consensus_history = {
        "consensus_results": [
            {"consensus_round": "Accepted"},
            {"consensus_round": "Validator Appeal Successful"},
        ]
    }
    # The terminal recomputation is raw round 2 as well.
    assert _runtime_rotation_limit(transaction) == 1


def test_internal_child_uses_protocol_round_zero_committee_not_parent_size():
    calls = []

    class _Processor:
        def insert_transaction(self, *args, **kwargs):
            calls.append((args, kwargs))

    context = SimpleNamespace(
        transaction=SimpleNamespace(
            to_address="0x1111111111111111111111111111111111111111",
            execution_mode="NORMAL",
            num_of_initial_validators=7,
            hash="0x" + "12" * 32,
            config_rotation_rounds=3,
            sim_config=None,
            origin_address=None,
        ),
        transactions_processor=_Processor(),
    )
    child_data = {
        "fees_distribution": _fees_distribution(rotations=[0]),
    }

    _emit_messages(
        context,
        [
            (
                "0x2222222222222222222222222222222222222222",
                child_data,
                TransactionType.RUN_CONTRACT.value,
                0,
                0,
            )
        ],
        {
            "tx_ids_hex": ["0x" + "34" * 32],
            "recipients": ["0x2222222222222222222222222222222222222222"],
        },
        "accepted",
    )

    assert calls[0][1]["num_of_initial_validators"] == 5
    assert calls[0][1]["config_rotation_rounds"] == 0


def test_internal_child_insert_requires_exact_helper_chain_ids():
    calls = []

    class _Processor:
        def insert_transaction(self, *args, **kwargs):
            calls.append((args, kwargs))

    context = SimpleNamespace(
        transaction=SimpleNamespace(
            to_address="0x1111111111111111111111111111111111111111",
            execution_mode="NORMAL",
            hash="0x" + "12" * 32,
            config_rotation_rounds=0,
            sim_config=None,
            origin_address=None,
        ),
        transactions_processor=_Processor(),
    )
    child = (
        "0x2222222222222222222222222222222222222222",
        {},
        TransactionType.RUN_CONTRACT.value,
        0,
        0,
    )

    with pytest.raises(RuntimeError, match="InternalMessageEmissionFailed"):
        _emit_messages(context, [child], None, "accepted")
    with pytest.raises(RuntimeError, match="InternalMessageEmissionCountMismatch"):
        _emit_messages(context, [child], {"tx_ids_hex": []}, "accepted")

    with pytest.raises(
        RuntimeError, match="InternalMessageEmissionRecipientCountMismatch"
    ):
        _emit_messages(
            context,
            [child],
            {"tx_ids_hex": ["0x" + "34" * 32]},
            "accepted",
        )

    assert calls == []


def _rollup_free_context(calls):
    class _Processor:
        def insert_transaction(self, *args, **kwargs):
            calls.append((args, kwargs))

    return SimpleNamespace(
        transaction=SimpleNamespace(
            to_address="0x1111111111111111111111111111111111111111",
            execution_mode="NORMAL",
            hash="0x" + "12" * 32,
            config_rotation_rounds=0,
            sim_config=None,
            origin_address=None,
        ),
        transactions_processor=_Processor(),
    )


def test_internal_deployment_binds_local_child_to_authoritative_recipient():
    calls = []
    created_accounts = []

    class _Processor:
        def insert_transaction(self, *args, **kwargs):
            calls.append((args, kwargs))

    class _Accounts:
        def create_new_account_with_address(self, address, *, commit=True):
            created_accounts.append((address, commit))

    context = SimpleNamespace(
        transaction=SimpleNamespace(
            to_address="0x1111111111111111111111111111111111111111",
            execution_mode="NORMAL",
            hash="0x" + "12" * 32,
            config_rotation_rounds=0,
            sim_config=None,
            origin_address=None,
        ),
        transactions_processor=_Processor(),
        accounts_manager=_Accounts(),
    )
    child_data = {
        "contract_address": "0x0000000000000000000000000000000000000000",
        "contract_code": b"contract",
        "calldata": b"",
    }
    helper_recipient = "0x3333333333333333333333333333333333333333"

    _emit_messages(
        context,
        [
            (
                "0x0000000000000000000000000000000000000000",
                child_data,
                TransactionType.DEPLOY_CONTRACT.value,
                0,
                0,
            )
        ],
        {
            "tx_ids_hex": ["0x" + "34" * 32],
            "recipients": [helper_recipient],
        },
        "accepted",
    )

    assert created_accounts == [(Web3.to_checksum_address(helper_recipient), False)]
    assert calls[0][0][1] == Web3.to_checksum_address(helper_recipient)
    assert child_data["contract_address"] == Web3.to_checksum_address(helper_recipient)


def test_external_message_does_not_require_a_helper_child_transaction():
    calls = []

    class _Processor:
        def insert_transaction(self, *args, **kwargs):
            calls.append((args, kwargs))

    context = SimpleNamespace(
        transaction=SimpleNamespace(
            to_address="0x1111111111111111111111111111111111111111",
            execution_mode="NORMAL",
            hash="0x" + "12" * 32,
            config_rotation_rounds=0,
            sim_config=None,
            origin_address=None,
        ),
        transactions_processor=_Processor(),
    )

    external_recipient = "0x4444444444444444444444444444444444444444"
    occurrence = "0x" + "56" * 32

    _emit_messages(
        context,
        [
            (
                external_recipient,
                {},
                TransactionType.SEND.value,
                0,
                7,
                occurrence,
            )
        ],
        {"tx_ids_hex": [], "recipients": []},
        "finalized",
    )

    assert calls[0][0][1] == Web3.to_checksum_address(external_recipient)
    assert calls[0][1]["transaction_hash"] == occurrence
    assert calls[0][1]["value"] == 7


def test_message_phase_without_a_rollup_commits_when_there_are_no_children():
    """A rollup-free deployment must not turn an empty phase into a retry loop.

    Studio deployments with no hardhat node get None from
    emit_transaction_event by design. Treating that as a lost emission
    stalled every accepted transaction forever, children or not.
    """
    calls = []
    context = _rollup_free_context(calls)

    _emit_messages(context, [], None, "accepted", rollup_skipped=True)

    assert calls == []


def test_message_phase_without_a_rollup_derives_child_ids_locally():
    """Pre-fee behaviour: a NULL child id lets insert_transaction derive one."""
    calls = []
    context = _rollup_free_context(calls)
    child = (
        "0x2222222222222222222222222222222222222222",
        {},
        TransactionType.RUN_CONTRACT.value,
        0,
        0,
    )

    _emit_messages(context, [child], None, "accepted", rollup_skipped=True)

    assert len(calls) == 1
    assert calls[0][1]["transaction_hash"] is None
    assert calls[0][1]["triggered_by_hash"] == "0x" + "12" * 32


def test_message_phase_with_a_rollup_still_rejects_a_malformed_receipt():
    """The strict checks stay in force wherever a rollup is actually attached."""
    calls = []
    context = _rollup_free_context(calls)
    child = (
        "0x2222222222222222222222222222222222222222",
        {},
        TransactionType.RUN_CONTRACT.value,
        0,
        0,
    )

    with pytest.raises(RuntimeError, match="InternalMessageEmissionFailed"):
        _emit_messages(context, [child], None, "accepted", rollup_skipped=False)
    with pytest.raises(RuntimeError, match="InternalMessageEmissionCountMismatch"):
        _emit_messages(
            context, [child], {"tx_ids_hex": []}, "accepted", rollup_skipped=False
        )

    assert calls == []


@pytest.mark.parametrize(
    ("use_balance", "initial_consumed", "expected_account_refund"),
    [(False, 55, 7), (True, 0, 62)],
)
def test_non_ghost_internal_message_is_skipped_and_refunded_once(
    use_balance,
    initial_consumed,
    expected_account_refund,
):
    insert_calls = []
    account_refunds = []
    recipient = "0x4444444444444444444444444444444444444444"
    parent_hash = "0x" + "12" * 32
    payload = {
        "messageType": 1,
        "recipient": recipient,
        "value": 7,
        "data": b"\x12\x34",
        "onAcceptance": True,
        "saltNonce": 0,
        "feeParams": b"",
        "declaredBudget": 55,
        "allocationSubtree": [],
        "callKey": "0x" + "00" * 32,
        "useBalance": use_balance,
    }
    occurrence, descriptor = message_effect_identities(parent_hash, [payload])[0]
    accounting = {
        "message_fee_budget": 55,
        "message_fee_consumed": initial_consumed,
        "message_value_effects": {
            occurrence: {
                "descriptor": descriptor,
                "phase": "accepted",
                "include": True,
                "value": 7,
                "declaredBudget": 55,
            }
        },
    }

    class _Processor:
        def insert_transaction(self, *args, **kwargs):
            insert_calls.append((args, kwargs))

        def mutate_transaction_fee_accounting(self, tx_hash, mutator, *, commit=True):
            assert tx_hash == parent_hash
            assert commit is False
            updated = mutator(self.accounting)
            self.accounting = updated
            return updated

    processor = _Processor()
    processor.accounting = accounting

    class _Accounts:
        def credit_account_balance(self, address, amount):
            account_refunds.append((address, amount))

    context = SimpleNamespace(
        transaction=SimpleNamespace(
            to_address="0x1111111111111111111111111111111111111111",
            execution_mode="NORMAL",
            hash=parent_hash,
            config_rotation_rounds=0,
            sim_config=None,
            origin_address=None,
            data={FEE_ACCOUNTING_KEY: accounting},
        ),
        transactions_processor=processor,
        accounts_manager=_Accounts(),
    )
    skipped_child = [
        recipient,
        {},
        TransactionType.RUN_CONTRACT.value,
        0,
        7,
        occurrence,
        payload,
    ]
    helper_result = {
        "tx_ids_hex": ["0x" + "00" * 32],
        "recipients": [recipient],
    }

    _emit_messages(context, [skipped_child], helper_result, "accepted")
    _emit_messages(context, [skipped_child], helper_result, "accepted")

    assert insert_calls == []
    assert account_refunds == [
        (context.transaction.to_address, expected_account_refund)
    ]
    assert processor.accounting["message_fee_consumed"] == 0
    assert len(processor.accounting.get("failed_internal_message_refunds", [])) == (
        0 if use_balance else 1
    )
    record = processor.accounting["message_value_effects"][occurrence]
    assert record["skipped"] is True
    assert record["skippedRefunded"] is True
    assert record["skippedRefundAmount"] == expected_account_refund


def test_local_message_authority_applies_ghost_factory_and_per_child_failures():
    registered = "0x2222222222222222222222222222222222222222"
    missing = "0x3333333333333333333333333333333333333333"
    parent_hash = "0x" + ("11" * 32)

    class _Processor:
        def __init__(self):
            self.ghosts = {registered.lower()}
            self.locked = False

        def lock_ghost_factory(self):
            self.locked = True

        def lock_pending_recipients(self, addresses):
            self.locked_recipients = list(addresses)

        def get_successful_ghost_creation_count(self):
            return 0

        def is_genvm_contract_address(self, address):
            return str(address).lower() in self.ghosts

        def get_pending_transaction_count_for_address(self, _address):
            return 0

    processor = _Processor()
    context = SimpleNamespace(
        transaction=SimpleNamespace(hash=parent_hash, to_address=registered),
        transactions_processor=processor,
    )

    def child(recipient, tx_type, occurrence, salt=0):
        return [
            recipient,
            {},
            tx_type,
            0,
            0,
            occurrence,
            {"saltNonce": salt, "data": b"\x01"},
        ]

    first_occurrence = "0x" + ("01" * 32)
    missing_occurrence = "0x" + ("02" * 32)
    deploy_occurrence = "0x" + ("03" * 32)
    duplicate_occurrence = "0x" + ("04" * 32)
    receipt = _author_message_phase_locally(
        context,
        [
            child(registered, TransactionType.RUN_CONTRACT.value, first_occurrence),
            child(missing, TransactionType.RUN_CONTRACT.value, missing_occurrence),
            child(
                "0x0000000000000000000000000000000000000000",
                TransactionType.DEPLOY_CONTRACT.value,
                deploy_occurrence,
                42,
            ),
            child(
                "0x0000000000000000000000000000000000000000",
                TransactionType.DEPLOY_CONTRACT.value,
                duplicate_occurrence,
                42,
            ),
        ],
        "accepted",
    )

    zero = "0x" + ("00" * 32)
    assert processor.locked is True
    assert {address.lower() for address in processor.locked_recipients} == {
        registered.lower(),
        "0x4e0065451873eaf51af1c7e00256a5db0f8a80ad",
    }
    assert receipt["tx_ids_hex"] == [
        _studio_child_transaction_id(parent_hash, "accepted", first_occurrence),
        zero,
        _studio_child_transaction_id(parent_hash, "accepted", deploy_occurrence),
        zero,
    ]
    assert receipt["recipients"][0] == Web3.to_checksum_address(registered)
    assert receipt["recipients"][1] == Web3.to_checksum_address(missing)
    assert receipt["recipients"][2] == "0x4E0065451873eaf51AF1C7E00256A5db0f8a80aD"
    assert receipt["recipients"][3] == Web3.to_checksum_address(
        "0x0000000000000000000000000000000000000000"
    )


def test_local_message_authority_enforces_recipient_queue_capacity(monkeypatch):
    monkeypatch.setenv("MAX_PENDING_PER_CONTRACT_DEFAULT", "20")
    recipient = "0x2222222222222222222222222222222222222222"

    processor = SimpleNamespace(
        lock_ghost_factory=lambda: None,
        lock_pending_recipients=lambda _addresses: None,
        get_successful_ghost_creation_count=lambda: 0,
        is_genvm_contract_address=lambda address: address.lower() == recipient.lower(),
        get_pending_transaction_count_for_address=lambda _address: 20,
    )
    occurrence = "0x" + ("05" * 32)
    receipt = _author_message_phase_locally(
        SimpleNamespace(
            transaction=SimpleNamespace(hash="0x" + ("22" * 32)),
            transactions_processor=processor,
        ),
        [
            [
                recipient,
                {},
                TransactionType.RUN_CONTRACT.value,
                0,
                0,
                occurrence,
                {"saltNonce": 0, "data": b"\x01"},
            ]
        ],
        "finalized",
    )

    assert receipt == {
        "tx_ids_hex": ["0x" + ("00" * 32)],
        "recipients": [Web3.to_checksum_address(recipient)],
    }


def test_local_message_authority_skips_empty_payload_before_deployment():
    processor = SimpleNamespace(
        lock_ghost_factory=lambda: None,
        lock_pending_recipients=lambda _addresses: None,
        get_successful_ghost_creation_count=lambda: 0,
        is_genvm_contract_address=lambda _address: False,
        get_pending_transaction_count_for_address=lambda _address: 0,
    )
    receipt = _author_message_phase_locally(
        SimpleNamespace(
            transaction=SimpleNamespace(hash="0x" + ("23" * 32)),
            transactions_processor=processor,
        ),
        [
            [
                "0x0000000000000000000000000000000000000000",
                {},
                TransactionType.DEPLOY_CONTRACT.value,
                0,
                0,
                "0x" + ("06" * 32),
                {"saltNonce": 0, "data": b""},
            ]
        ],
        "accepted",
    )

    assert receipt == {
        "tx_ids_hex": ["0x" + ("00" * 32)],
        "recipients": [
            Web3.to_checksum_address("0x0000000000000000000000000000000000000000")
        ],
    }


def test_simulation_fee_accounting_accepts_sdk_style_fee_options():
    policy = StudioFeePolicy.from_env()
    fees_distribution = _env_fees_distribution(
        execution_budget_per_round=policy.message_fee_params_budget_floor()
    )
    fee_value = _required_env_fee_deposit(fees_distribution)
    accounting = _simulation_fee_accounting(
        {
            "fees": {
                "distribution": fees_distribution,
                "feeValue": fee_value,
            },
            "numOfInitialValidators": 5,
        },
        sender="0x1111111111111111111111111111111111111111",
        user_value=0,
    )

    assert accounting is not None
    assert accounting["primary_fee_budget"] == fee_value
    assert (
        accounting["execution_budget_total"] == policy.message_fee_params_budget_floor()
    )


def test_simulation_fee_accounting_accepts_sdk_style_message_allocations():
    policy = StudioFeePolicy.from_env()
    fee_params = _encode_internal_fee_params(
        leader_timeunits=30,
        validator_timeunits=30,
        max_price_gen_per_time_unit=policy.gen_per_time_unit,
    )
    budget = calculate_round_fees(
        _fees_distribution(
            leader_timeunits=30,
            validator_timeunits=30,
            max_price_gen_per_time_unit=policy.gen_per_time_unit,
        ),
        5,
        policy=policy,
    )
    allocation = _allocation(budget=budget, fee_params=fee_params)
    fees_distribution = _env_fees_distribution(total_message_fees=budget)

    accounting = _simulation_fee_accounting(
        {
            "fees": {
                "distribution": fees_distribution,
                "messageAllocations": [allocation],
            },
            "numOfInitialValidators": 5,
        },
        sender="0x1111111111111111111111111111111111111111",
        user_value=0,
    )

    assert accounting is not None
    assert accounting["message_fee_budget"] == budget
    assert accounting["message_allocations"] == [
        {
            "messageType": 1,
            "onAcceptance": True,
            "parentIndex": NODE_ROOT_SENTINEL,
            "recipient": "0x2222222222222222222222222222222222222222",
            "callKey": CALL_KEY_WILDCARD,
            "budget": budget,
            "feeParams": "0x" + fee_params.hex(),
        }
    ]


def test_simulation_fee_accounting_defaults_to_required_deposit():
    fees_distribution = _env_fees_distribution(total_message_fees=55)
    required_fee_value = _required_env_fee_deposit(fees_distribution)
    accounting = _simulation_fee_accounting(
        {
            "fees": {
                "distribution": fees_distribution,
            },
            "numOfInitialValidators": 5,
        },
        sender="0x1111111111111111111111111111111111111111",
        user_value=12,
    )

    assert accounting is not None
    assert accounting["paid_fee_value"] == required_fee_value
    assert accounting["user_value"] == 12
    assert accounting["primary_fee_required"] == required_fee_value - 55
    assert accounting["message_fee_budget"] == 55


def test_discovered_message_allocations_are_exact_and_consensus_valid():
    policy = StudioFeePolicy.from_env()
    fees_distribution = _env_fees_distribution()
    internal_recipient = "0x2222222222222222222222222222222222222222"
    external_recipient = "0x3333333333333333333333333333333333333333"
    internal_call_key = "0x" + "12" * 32
    external_calldata = bytes.fromhex("aabbccdd01")
    receipt = {
        "pending_transactions": [
            {
                "messageType": "Internal",
                "address": internal_recipient,
                "on": "accepted",
                "call_key": internal_call_key,
            },
            {
                "messageType": "Internal",
                "address": internal_recipient,
                "on": "accepted",
                "call_key": internal_call_key,
            },
            {
                "messageType": "External",
                "address": external_recipient,
                "on": "finalized",
                "calldata": external_calldata,
                "call_key": EMPTY_CALL_KEY,
            },
            {
                "messageType": "Internal",
                "address": "0x4444444444444444444444444444444444444444",
                "on": "finalized",
                "call_key": "0x" + "56" * 32,
                "use_balance": True,
            },
        ]
    }

    allocations = discovered_message_fee_allocations(
        receipt,
        fees_distribution,
        policy,
    )

    assert len(allocations) == 2
    internal, external = allocations
    assert internal["recipient"] == internal_recipient
    assert internal["callKey"] == internal_call_key
    assert internal["onAcceptance"] is True
    internal_params = decode_internal_message_fee_params(internal["feeParams"])
    assert internal["budget"] == 2 * min_message_primary_fees(
        internal_params,
        policy,
    )
    assert external["recipient"] == external_recipient
    assert external["callKey"] == "0x" + "aabbccdd" + "00" * 28
    assert external["onAcceptance"] is False
    external_params = decode_external_message_fee_params(external["feeParams"])
    assert external_params["gasLimit"] == policy.default_external_gas_limit
    assert external["budget"] == (
        external_params["gasLimit"] * external_params["maxGasPrice"]
    )

    total_message_fees = sum(allocation["budget"] for allocation in allocations)
    validate_message_allocations(
        allocations,
        total_message_fees=total_message_fees,
        policy=policy,
    )


def test_discovered_messages_rebuild_simulation_accounting_and_deposit():
    policy = StudioFeePolicy.from_env()
    fees_distribution = _env_fees_distribution()
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5, policy),
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
        policy=policy,
    )
    receipt = {
        "pending_transactions": [
            {
                "messageType": "Internal",
                "address": "0x2222222222222222222222222222222222222222",
                "on": "finalized",
                "call_key": "0x" + "34" * 32,
            }
        ]
    }

    rebuilt = fee_accounting_with_discovered_messages(accounting, receipt, policy)

    assert len(rebuilt["message_allocations"]) == 1
    assert rebuilt["message_fee_budget"] == rebuilt["message_allocations"][0]["budget"]
    assert rebuilt["required_fee_value"] == required_fee_deposit(
        rebuilt["fees_distribution"],
        5,
        policy,
    )
    assert rebuilt["paid_fee_value"] == rebuilt["required_fee_value"]


def test_simulation_fee_accounting_rejects_insufficient_sdk_fee_value():
    fees_distribution = _env_fees_distribution(total_message_fees=55)
    with pytest.raises(JSONRPCError, match="InsufficientFees"):
        _simulation_fee_accounting(
            {
                "fees": {
                    "distribution": fees_distribution,
                    "feeValue": _required_env_fee_deposit(fees_distribution) - 1,
                },
                "numOfInitialValidators": 5,
            },
            sender="0x1111111111111111111111111111111111111111",
            user_value=0,
        )


def test_default_simulation_fees_are_injected_for_fee_estimation():
    params = {"type": "write", "to": "0x" + "22" * 20}

    updated = _with_default_simulation_fees(params)

    assert "fees" not in params
    assert updated["fees"]["distribution"]["leaderTimeunitsAllocation"] == "100"
    assert int(updated["fees"]["feeValue"]) > 0
    assert _with_default_simulation_fees(
        {"fees": {"distribution": _fees_distribution(total_message_fees=55)}}
    ) == {"fees": {"distribution": _fees_distribution(total_message_fees=55)}}

    caller_fees = {"fees": {"messageAllocations": [_allocation(budget=55)]}}
    assert _with_default_simulation_fees(caller_fees) == caller_fees


@pytest.mark.asyncio
async def test_sim_estimate_transaction_fees_returns_scenario_report_and_preset(
    monkeypatch,
):
    async def fake_sim_call(**kwargs):
        distribution = kwargs["params"]["fees"]["distribution"]
        assert distribution["leaderTimeunitsAllocation"] == "100"
        return {
            "genvm_result": {
                FEE_ACCOUNTING_KEY: {
                    "execution_fee_report": {"totalEstimatedFee": 123},
                    "recommended_fee_preset": {"feeValue": 456},
                }
            }
        }

    monkeypatch.setattr("backend.protocol_rpc.endpoints.sim_call", fake_sim_call)

    result = await sim_estimate_transaction_fees(
        session=None,
        accounts_manager=None,
        msg_handler=None,
        transactions_parser=None,
        validators_manager=None,
        genvm_manager=None,
        params={"scenarioName": "happy-path", "type": "write"},
    )

    assert result["scenario"] == "happy-path"
    assert result["feeReport"] == {"totalEstimatedFee": 123}
    assert result["recommendedPreset"] == {"feeValue": 456}
    assert result["feeAccounting"]["recommended_fee_preset"] == {"feeValue": 456}


@pytest.mark.asyncio
async def test_sim_estimate_transaction_fees_preserves_caller_fee_envelope(
    monkeypatch,
):
    fee_params = _encode_internal_fee_params()
    allocation = _allocation(budget=55, fee_params=fee_params)
    fees_distribution = _fees_distribution(total_message_fees=55)
    fees = {
        "distribution": fees_distribution,
        "feeValue": str(_required_env_fee_deposit(fees_distribution)),
        "messageAllocations": [allocation],
    }
    params = {
        "scenarioName": "mode-2-message",
        "type": "write",
        "to": "0x" + "22" * 20,
        "fees": fees,
    }
    seen = {}

    async def fake_sim_call(**kwargs):
        seen["params"] = kwargs["params"]
        return {
            "genvm_result": {
                FEE_ACCOUNTING_KEY: {
                    "execution_fee_report": {"messageFees": {"budget": 55}},
                    "recommended_fee_preset": {"messageAllocations": [allocation]},
                }
            }
        }

    monkeypatch.setattr("backend.protocol_rpc.endpoints.sim_call", fake_sim_call)

    result = await sim_estimate_transaction_fees(
        session=None,
        accounts_manager=None,
        msg_handler=None,
        transactions_parser=None,
        validators_manager=None,
        genvm_manager=None,
        params=params,
    )

    assert seen["params"]["fees"] == fees
    assert seen["params"]["_allow_low_execution_budget_for_estimate"] is True
    assert seen["params"]["_discover_message_allocations_for_estimate"] is True
    assert "_allow_low_execution_budget_for_estimate" not in params
    assert "_discover_message_allocations_for_estimate" not in params
    assert result["scenario"] == "mode-2-message"
    assert result["feeReport"] == {"messageFees": {"budget": 55}}
    assert result["recommendedPreset"] == {
        "messageAllocations": [
            {**allocation, "parentIndex": str(allocation["parentIndex"])}
        ]
    }


@pytest.mark.asyncio
async def test_sim_estimate_transaction_fees_returns_mode2_recommended_preset(
    monkeypatch,
):
    policy = StudioFeePolicy.from_env()
    execution_budget = policy.message_fee_params_budget_floor()
    fee_params = _encode_internal_fee_params(
        leader_timeunits=30,
        validator_timeunits=30,
        execution_budget_per_round=execution_budget,
        max_price_gen_per_time_unit=policy.gen_per_time_unit,
    )
    message_budget = calculate_round_fees(
        _fees_distribution(
            leader_timeunits=30,
            validator_timeunits=30,
            execution_budget_per_round=execution_budget,
            max_price_gen_per_time_unit=policy.gen_per_time_unit,
        ),
        5,
        policy=policy,
    )
    recipient = "0x2222222222222222222222222222222222222222"
    allocation = _allocation(
        recipient=recipient,
        budget=message_budget,
        fee_params="0x" + fee_params.hex(),
    )
    fees_distribution = _env_fees_distribution(
        execution_budget_per_round=execution_budget,
        total_message_fees=message_budget,
    )
    params = {
        "scenarioName": "mode-2-message",
        "type": "write",
        "from": "0x1111111111111111111111111111111111111111",
        "to": "0x" + "22" * 20,
        "fees": {
            "distribution": fees_distribution,
            "feeValue": str(required_fee_deposit(fees_distribution, 5, policy)),
            "messageAllocations": [allocation],
        },
    }

    async def fake_sim_call(**kwargs):
        accounting = _simulation_fee_accounting(
            kwargs["params"],
            sender=params["from"],
            user_value=0,
        )
        recorded = record_execution_fee_consumption(
            accounting,
            {
                "genvm_result": {
                    "data_fee_bucket_totals": [
                        execution_budget,
                        execution_budget,
                        message_budget,
                    ],
                    "data_fees_remaining": [
                        execution_budget - 80,
                        execution_budget,
                        0,
                    ],
                },
                "pending_transactions": [
                    {
                        "messageType": "Internal",
                        "recipient": recipient,
                        "data": "0x1234",
                        "onAcceptance": True,
                        "value": 0,
                        "declaredBudget": 0,
                        "callKey": CALL_KEY_WILDCARD,
                    }
                ],
            },
            policy,
        )
        return {"genvm_result": {FEE_ACCOUNTING_KEY: recorded}}

    monkeypatch.setattr("backend.protocol_rpc.endpoints.sim_call", fake_sim_call)

    result = await sim_estimate_transaction_fees(
        session=None,
        accounts_manager=None,
        msg_handler=None,
        transactions_parser=None,
        validators_manager=None,
        genvm_manager=None,
        params=params,
    )

    preset = result["recommendedPreset"]
    report = result["feeReport"]
    message = report["messageReveal"]["messages"][0]

    assert result["scenario"] == "mode-2-message"
    assert int(result["feeAccounting"]["message_fee_consumed"]) == message_budget
    assert result["feeAccounting"]["execution_fee_report"] == report
    assert report["chargeableExecution"]["totalExecution"] > 0
    assert report["genvmBuckets"]["totalExecution"] == 80
    assert int(report["genvmBuckets"]["message"]) == message_budget
    assert (
        report["executionMetering"]["chargeableExecutionFee"]
        == report["chargeableExecution"]["totalExecution"]
    )
    assert int(report["messageFees"]["budget"]) == message_budget
    assert int(report["messageFees"]["declaredConsumed"]) == message_budget
    assert report["messageFees"]["remaining"] == 0
    assert message["messageFeeMode"] == "mode2"
    assert int(message["declaredBudget"]) == message_budget
    assert preset["messageBudgetMode"] == "allocation-preserved"
    assert int(preset["distribution"]["totalMessageFees"]) == message_budget
    assert int(preset["messageAllocations"][0]["budget"]) == message_budget
    assert preset["messageAllocations"][0]["feeParams"] == "0x" + fee_params.hex()
    assert int(preset["observed"]["messageFeeBudget"]) == message_budget


@pytest.mark.asyncio
async def test_sim_estimate_transaction_fees_returns_mode1_observed_message_preset(
    monkeypatch,
):
    policy = StudioFeePolicy.from_env()
    fee_params = _encode_internal_fee_params(
        leader_timeunits=30,
        validator_timeunits=30,
        max_price_gen_per_time_unit=policy.gen_per_time_unit,
    )
    message_budget = calculate_round_fees(
        _fees_distribution(
            leader_timeunits=30,
            validator_timeunits=30,
            max_price_gen_per_time_unit=policy.gen_per_time_unit,
        ),
        5,
        policy=policy,
    )
    expected_padded_message_budget = (
        message_budget * DEFAULT_PRICE_CAP_HEADROOM_BPS + 9_999
    ) // 10_000
    recipient = "0x2222222222222222222222222222222222222222"
    fees_distribution = _env_fees_distribution(
        execution_budget_per_round=policy.message_fee_params_budget_floor(),
        total_message_fees=message_budget,
    )
    params = {
        "scenarioName": "mode-1-message",
        "type": "write",
        "from": "0x1111111111111111111111111111111111111111",
        "to": "0x" + "22" * 20,
        "fees": {
            "distribution": fees_distribution,
            "feeValue": str(required_fee_deposit(fees_distribution, 5, policy)),
        },
    }

    async def fake_sim_call(**kwargs):
        accounting = _simulation_fee_accounting(
            kwargs["params"],
            sender=params["from"],
            user_value=0,
        )
        recorded = record_execution_fee_consumption(
            accounting,
            {
                "genvm_result": {
                    "data_fees_consumed": [80, 0],
                    "eqBlocksOutputsLength": 0,
                    "messageFeesConsumed": message_budget,
                },
                "pending_transactions": [
                    {
                        "messageType": "Internal",
                        "recipient": recipient,
                        "data": "0x1234",
                        "onAcceptance": True,
                        "value": 1,
                        "feeParams": fee_params,
                        "declaredBudget": message_budget,
                        "callKey": "0x" + "12" * 32,
                    }
                ],
            },
            policy,
        )
        return {"genvm_result": {FEE_ACCOUNTING_KEY: recorded}}

    monkeypatch.setattr("backend.protocol_rpc.endpoints.sim_call", fake_sim_call)

    result = await sim_estimate_transaction_fees(
        session=None,
        accounts_manager=None,
        msg_handler=None,
        transactions_parser=None,
        validators_manager=None,
        genvm_manager=None,
        params=params,
    )

    report = result["feeReport"]
    preset = result["recommendedPreset"]
    message = report["messageReveal"]["messages"][0]

    assert result["scenario"] == "mode-1-message"
    assert int(result["feeAccounting"]["message_fee_consumed"]) == message_budget
    assert int(report["messageFees"]["budget"]) == message_budget
    assert int(report["messageFees"]["declaredConsumed"]) == message_budget
    assert int(report["messageFees"]["reportedTotal"]) == message_budget
    assert message["messageFeeMode"] == "mode1"
    assert message["messageType"] == "Internal"
    assert message["feeParams"] == "0x" + fee_params.hex()
    assert message["feeParamsDecoded"] == {
        "leaderTimeunitsAllocation": 30,
        "validatorTimeunitsAllocation": 30,
        "appealRounds": 0,
        "executionBudgetPerRound": 0,
        "rotations": [0],
        "maxPriceGenPerTimeUnit": policy.gen_per_time_unit,
        "storageFeeMaxGasPrice": str(2**200),
        "receiptFeeMaxGasPrice": str(2**200),
    }
    assert int(message["declaredBudget"]) == message_budget
    assert message["allocationSubtree"] == "0x"
    assert preset["messageBudgetMode"] == "observed"
    assert preset["messageAllocations"] == []
    assert (
        int(preset["distribution"]["totalMessageFees"])
        == expected_padded_message_budget
    )
    assert int(preset["observed"]["messageFeeBudget"]) == message_budget
    assert int(preset["observed"]["declaredMessageFees"]) == message_budget


@pytest.mark.asyncio
async def test_sim_estimate_transaction_fees_returns_external_message_fee_report(
    monkeypatch,
):
    policy = StudioFeePolicy.from_env()
    gas_limit = 21_000
    max_gas_price = 10
    message_budget = gas_limit * max_gas_price
    recipient = "0x3333333333333333333333333333333333333333"
    fee_params = _encode_external_fee_params(
        gas_limit=gas_limit,
        max_gas_price=max_gas_price,
    )
    allocation = _allocation(
        message_type=0,
        on_acceptance=False,
        parent_index=_root_parent_index(),
        recipient=recipient,
        call_key=CALL_KEY_WILDCARD,
        budget=message_budget,
        fee_params="0x" + fee_params.hex(),
    )
    fees_distribution = _env_fees_distribution(total_message_fees=message_budget)
    params = {
        "scenarioName": "external-transfer",
        "type": "write",
        "from": "0x1111111111111111111111111111111111111111",
        "to": "0x" + "22" * 20,
        "value": "0x1",
        "fees": {
            "distribution": fees_distribution,
            "feeValue": str(required_fee_deposit(fees_distribution, 5, policy)),
            "messageAllocations": [allocation],
        },
    }

    async def fake_sim_call(**kwargs):
        accounting = _simulation_fee_accounting(
            kwargs["params"],
            sender=params["from"],
            user_value=1,
        )
        recorded = record_execution_fee_consumption(
            accounting,
            {
                "genvm_result": {
                    "data_fees_consumed": [80, 0],
                    "eq_blocks_outputs_length": 0,
                },
                "pending_transactions": [
                    {
                        "isEthSend": True,
                        "recipient": recipient,
                        "data": "0x",
                        "onAcceptance": False,
                        "value": 1,
                        "declaredBudget": 0,
                        "callKey": CALL_KEY_WILDCARD,
                        "gasUsed": 11,
                    }
                ],
            },
            policy,
        )
        return {"genvm_result": {FEE_ACCOUNTING_KEY: recorded}}

    monkeypatch.setattr("backend.protocol_rpc.endpoints.sim_call", fake_sim_call)

    result = await sim_estimate_transaction_fees(
        session=None,
        accounts_manager=None,
        msg_handler=None,
        transactions_parser=None,
        validators_manager=None,
        genvm_manager=None,
        params=params,
    )

    report = result["feeReport"]
    preset = result["recommendedPreset"]
    message = report["messageReveal"]["messages"][0]
    # External fee params cap the reimbursable gas price independently of the
    # live policy; the v0.6 default is intentionally far above this fixture's
    # explicit maxGasPrice.
    effective_gas_price = min(policy.receipt_gas_price, max_gas_price)
    expected_reservation = gas_limit * effective_gas_price
    expected_reimbursement = 11 * effective_gas_price

    assert result["scenario"] == "external-transfer"
    assert (
        result["feeAccounting"]["external_message_fee_reserved"] == expected_reservation
    )
    assert (
        result["feeAccounting"]["external_message_fee_reimbursed"]
        == expected_reimbursement
    )
    assert result["feeAccounting"]["execution_fee_report"] == report
    assert report["messageFees"]["budget"] == message_budget
    assert report["messageFees"]["declaredConsumed"] == 0
    assert report["messageFees"]["externalReserved"] == expected_reservation
    assert report["messageFees"]["externalReimbursed"] == expected_reimbursement
    assert report["messageFees"]["externalRemainder"] == (
        expected_reservation - expected_reimbursement
    )
    assert message["messageFeeMode"] == "external"
    assert message["messageType"] == "External"
    assert message["callKey"] == CALL_KEY_WILDCARD
    assert message["feeParams"] == "0x" + fee_params.hex()
    assert message["feeParamsDecoded"] == {
        "gasLimit": gas_limit,
        "maxGasPrice": max_gas_price,
    }
    assert message["feeParamsBytes"] == len(fee_params)
    assert preset["messageBudgetMode"] == "allocation-preserved"
    assert preset["distribution"]["totalMessageFees"] == message_budget
    assert preset["messageAllocations"][0]["messageType"] == 0
    assert preset["messageAllocations"][0]["callKey"] == CALL_KEY_WILDCARD
    assert preset["messageAllocations"][0]["budget"] == message_budget
    assert preset["observed"]["externalMessageReserved"] == expected_reservation


def test_message_allocations_accept_root_internal_budget_matching_total():
    allocation = _allocation(budget=55)

    validate_message_allocations([allocation], total_message_fees=55)


def test_message_allocations_reject_root_budget_mismatch():
    allocation = _allocation(budget=55)

    with pytest.raises(MessageAllocationsNotEqualBudget):
        validate_message_allocations([allocation], total_message_fees=56)


def test_message_allocations_reject_parent_that_does_not_precede_child():
    allocation = _allocation(parent_index=0, budget=55)

    with pytest.raises(AllocationTreeMalformed):
        validate_message_allocations([allocation], total_message_fees=0)


def test_message_allocations_reject_lifecycle_budget_below_minimum():
    allocation = _allocation(budget=54)

    with pytest.raises(AllocationLifecycleBudgetInsufficient):
        validate_message_allocations([allocation], total_message_fees=54)


def test_message_allocations_enforce_on_acceptance_lifecycle_multiplier():
    fee_params = _encode_internal_fee_params(appeals=1, rotations=[0, 0])
    min_primary = calculate_round_fees(
        _fees_distribution(
            leader_timeunits=5,
            validator_timeunits=10,
            appeals=1,
            rotations=[0, 0],
        ),
        5,
    )

    with pytest.raises(AllocationLifecycleBudgetInsufficient):
        validate_message_allocations(
            [
                _allocation(
                    on_acceptance=True,
                    budget=(min_primary * 2) - 1,
                    fee_params=fee_params,
                )
            ],
            total_message_fees=(min_primary * 2) - 1,
        )

    validate_message_allocations(
        [
            _allocation(
                on_acceptance=True,
                budget=min_primary * 2,
                fee_params=fee_params,
            )
        ],
        total_message_fees=min_primary * 2,
    )


def test_message_allocations_do_not_multiply_on_finalization_budget():
    fee_params = _encode_internal_fee_params(appeals=1, rotations=[0, 0])
    min_primary = calculate_round_fees(
        _fees_distribution(
            leader_timeunits=5,
            validator_timeunits=10,
            appeals=1,
            rotations=[0, 0],
        ),
        5,
    )

    validate_message_allocations(
        [
            _allocation(
                on_acceptance=False,
                budget=min_primary,
                fee_params=fee_params,
            )
        ],
        total_message_fees=min_primary,
    )


def test_message_allocations_reject_parent_budget_below_child_sum_plus_minimum():
    allocations = [
        _allocation(budget=100),
        _allocation(parent_index=0, budget=55),
    ]

    with pytest.raises(AllocationTreeBudgetInconsistent):
        validate_message_allocations(allocations, total_message_fees=100)


def test_message_allocations_reject_duplicate_root_internal_keys():
    allocations = [
        _allocation(budget=55),
        _allocation(budget=55),
    ]

    with pytest.raises(AllocationDuplicateKey):
        validate_message_allocations(allocations, total_message_fees=110)


def test_message_allocations_reject_duplicate_normalized_root_internal_keys():
    allocations = [
        _allocation(budget=55, call_key="0x0"),
        _allocation(budget=55, call_key=EMPTY_CALL_KEY),
    ]

    with pytest.raises(AllocationDuplicateKey):
        validate_message_allocations(allocations, total_message_fees=110)


def test_message_allocations_reject_duplicate_sibling_keys():
    allocations = [
        _allocation(budget=200),
        _allocation(parent_index=0, budget=55),
        _allocation(parent_index=0, budget=55),
    ]

    with pytest.raises(AllocationDuplicateKey):
        validate_message_allocations(allocations, total_message_fees=200)


def test_message_allocations_reject_depth_above_default_cap():
    allocations = [
        _allocation(budget=330, parent_index=NODE_ROOT_SENTINEL),
        _allocation(budget=275, parent_index=0),
        _allocation(budget=220, parent_index=1),
        _allocation(budget=165, parent_index=2),
        _allocation(budget=110, parent_index=3),
        _allocation(budget=55, parent_index=4),
    ]

    with pytest.raises(AllocationTreeTooDeep):
        validate_message_allocations(allocations, total_message_fees=330)


def test_internal_allocation_defers_zero_price_cap_rejection_until_reveal():
    fee_params = _encode_internal_fee_params(
        max_price_gen_per_time_unit=0,
        storage_fee_max_gas_price=0,
        receipt_fee_max_gas_price=0,
    )
    allocation = _allocation(budget=55, fee_params=fee_params)

    validate_message_allocations([allocation], total_message_fees=55)
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=55),
        message_allocations=[allocation],
        num_of_validators=5,
        submitted_value=1_155,
        user_value=0,
    )

    with pytest.raises(FeeValueMustBeNonZero, match=r"FeeValueMustBeNonZero\(4\)"):
        consume_message_fees(
            accounting,
            [
                {
                    "messageType": 1,
                    "recipient": allocation["recipient"],
                    "onAcceptance": True,
                    "feeParams": fee_params,
                    "declaredBudget": 55,
                    "callKey": allocation["callKey"],
                }
            ],
        )


def test_message_allocations_accept_valid_external_allocation():
    allocation = _allocation(
        message_type=0,
        on_acceptance=False,
        budget=210_000,
        fee_params=_encode_external_fee_params(gas_limit=21_000, max_gas_price=10),
    )

    validate_message_allocations([allocation], total_message_fees=210_000)


def test_message_allocations_reject_invalid_external_allocation():
    allocation = _allocation(
        message_type=0,
        on_acceptance=False,
        budget=210_001,
        fee_params=_encode_external_fee_params(gas_limit=21_000, max_gas_price=10),
    )

    with pytest.raises(ExternalAllocationInvalid):
        validate_message_allocations([allocation], total_message_fees=210_001)


def test_message_allocations_reject_external_allocation_invariants():
    with pytest.raises(ExternalAllocationInvalid):
        validate_message_allocations(
            [
                _allocation(
                    message_type=0,
                    budget=210_000,
                    fee_params=_encode_external_fee_params(
                        gas_limit=0,
                        max_gas_price=10,
                    ),
                )
            ],
            total_message_fees=210_000,
        )

    with pytest.raises(ExternalAllocationInvalid):
        validate_message_allocations(
            [
                _allocation(
                    message_type=0,
                    budget=210_000,
                    fee_params=_encode_external_fee_params(
                        gas_limit=21_000,
                        max_gas_price=0,
                    ),
                )
            ],
            total_message_fees=210_000,
        )

    with pytest.raises(ExternalAllocationInvalid):
        validate_message_allocations(
            [
                _allocation(
                    message_type=0,
                    budget=0,
                    fee_params=_encode_external_fee_params(),
                )
            ],
            total_message_fees=0,
        )

    with pytest.raises(InvalidFeeParams):
        validate_message_allocations(
            [_allocation(message_type=0, budget=210_000, fee_params=b"\x01")],
            total_message_fees=210_000,
        )

    with pytest.raises(ExternalAllocationInvalid):
        validate_message_allocations(
            [
                _allocation(
                    message_type=0,
                    budget=210_000,
                    fee_params=_encode_external_fee_params(),
                ),
                _allocation(
                    message_type=0,
                    budget=210_000,
                    fee_params=_encode_external_fee_params(),
                ),
            ],
            total_message_fees=420_000,
        )

    with pytest.raises(ExternalAllocationInvalid):
        validate_message_allocations(
            [
                _allocation(
                    message_type=0,
                    call_key="0x0",
                    budget=210_000,
                    fee_params=_encode_external_fee_params(),
                ),
                _allocation(
                    message_type=0,
                    call_key=EMPTY_CALL_KEY,
                    budget=210_000,
                    fee_params=_encode_external_fee_params(),
                ),
            ],
            total_message_fees=420_000,
        )

    with pytest.raises(AllocationTreeMalformed):
        validate_message_allocations(
            [
                _allocation(
                    message_type=0,
                    parent_index=0,
                    budget=210_000,
                    fee_params=_encode_external_fee_params(),
                )
            ],
            total_message_fees=210_000,
        )

    with pytest.raises(AllocationTreeMalformed):
        validate_message_allocations(
            [
                _allocation(
                    message_type=0,
                    budget=210_000,
                    fee_params=_encode_external_fee_params(),
                ),
                _allocation(
                    message_type=1,
                    parent_index=0,
                    budget=55,
                    fee_params=_encode_internal_fee_params(),
                ),
            ],
            total_message_fees=210_000,
        )


def test_message_allocations_reject_external_on_acceptance():
    allocation = _allocation(
        message_type=0,
        on_acceptance=True,
        budget=210_000,
        fee_params=_encode_external_fee_params(gas_limit=21_000, max_gas_price=10),
    )

    with pytest.raises(
        ExternalAllocationInvalid, match="ExternalOnAcceptanceNotSupported"
    ):
        validate_message_allocations([allocation], total_message_fees=210_000)


def test_message_allocations_enforce_external_gas_limit_floor():
    allocation = _allocation(
        message_type=0,
        on_acceptance=False,
        budget=209_990,
        fee_params=_encode_external_fee_params(gas_limit=20_999, max_gas_price=10),
    )

    with pytest.raises(ExternalAllocationInvalid, match="ExternalGasLimitBelowMinimum"):
        validate_message_allocations(
            [allocation],
            total_message_fees=209_990,
            policy=StudioFeePolicy(min_external_gas_limit=21_000),
        )


def test_transaction_fee_validation_runs_message_allocation_checks():
    fees_distribution = _fees_distribution(total_message_fees=56)

    with pytest.raises(MessageAllocationsNotEqualBudget):
        validate_transaction_fee_deposit(
            fees_distribution=fees_distribution,
            message_allocations=[_allocation(budget=55)],
            num_of_validators=5,
            submitted_value=1156,
            user_value=0,
        )


def test_genvm_fee_context_uses_transaction_execution_budget_and_policy():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(execution_budget_per_round=123),
        num_of_validators=5,
        submitted_value=1223,
        user_value=0,
    )

    bucket_totals, gas_data = genvm_fee_context(
        accounting,
        StudioFeePolicy(
            gen_per_time_unit=2,
            storage_unit_price=3,
            receipt_gas_price=4,
            intrinsic_gas=5,
            bootloader_overhead=6,
            gas_per_changed_slot=7,
            calldata_gas_per_byte=9,
            fixed_propose_receipt_gas=8,
            fixed_message_reveal_gas=10,
        ),
    )

    assert bucket_totals == [
        123,
        0,
        GENVM_UNMETERED_DATA_FEE_BUCKET,
        GENVM_UNMETERED_DATA_FEE_BUCKET,
    ]
    assert gas_data == {
        "storageUnitPrice": "3",
        "receiptGasPerByte": "36",
        "gasPerChangedSlot": "28",
        "intrinsicGas": "20",
        "bootloaderOverhead": "24",
        "fixedProposeReceiptGas": "32",
        "fixedMessageRevealGas": "40",
        "genPerTimeUnit": "2",
    }


def test_genvm_fee_context_sends_price_policy_without_execution_bucket():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(
            leader_timeunits=0,
            validator_timeunits=0,
        ),
        num_of_validators=5,
        submitted_value=123,
        user_value=0,
    )

    bucket_totals, gas_data = genvm_fee_context(
        accounting,
        StudioFeePolicy(
            gen_per_time_unit=2,
            storage_unit_price=3,
            receipt_gas_price=4,
        ),
    )

    assert bucket_totals is None
    assert gas_data["genPerTimeUnit"] == "2"
    assert gas_data["storageUnitPrice"] == "3"
    assert gas_data["receiptGasPerByte"] == "64"
    assert gas_data["fixedProposeReceiptGas"] == "840000"
    assert gas_data["fixedMessageRevealGas"] == "400000"


def test_genvm_fee_context_sets_message_bucket_independently():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=55),
        message_allocations=[_allocation(budget=55)],
        num_of_validators=5,
        submitted_value=1155,
        user_value=0,
    )

    bucket_totals, _ = genvm_fee_context(accounting)

    assert bucket_totals == [
        GENVM_UNMETERED_DATA_FEE_BUCKET,
        55,
        GENVM_UNMETERED_DATA_FEE_BUCKET,
        GENVM_UNMETERED_DATA_FEE_BUCKET,
    ]


def test_genvm_message_fee_allocation_maps_studio_nodes():
    fee_params = _encode_internal_fee_params(leader_timeunits=6)
    fees_distribution = _fees_distribution(
        total_message_fees=60,
        max_price_gen_per_time_unit=11,
        storage_fee_max_gas_price=12,
        receipt_fee_max_gas_price=13,
    )
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        message_allocations=[
            _allocation(
                budget=60,
                fee_params=fee_params,
                recipient="0x2222222222222222222222222222222222222222",
                call_key="0x" + "12" * 32,
            )
        ],
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5),
        user_value=0,
    )

    allocations = genvm_message_fee_allocation(accounting)

    assert allocations[0] == {
        "recipient": "0x2222222222222222222222222222222222222222",
        "call_key": bytes.fromhex("12" * 32),
        "budget": 60,
        "on": "decided",
        "fee_params": {
            "Internal": {
                "leader_timeunits_allocation": 6,
                "validator_timeunits_allocation": 10,
                "execution_budget_per_round": 0,
                "rotations": [0],
                "max_price_gen_per_time_unit": 1,
                "storage_fee_max_gas_price": 2**200,
                "receipt_fee_max_gas_price": 2**200,
            },
        },
        "children": [],
    }
    assert len(allocations) == 1


def test_genvm_message_fee_allocation_nests_descendants_under_roots():
    root_fee_params = _encode_internal_fee_params(leader_timeunits=6)
    child_fee_params = _encode_internal_fee_params(leader_timeunits=7)
    root = _allocation(
        budget=120,
        fee_params=root_fee_params,
        recipient="0x2222222222222222222222222222222222222222",
        call_key="0x" + "12" * 32,
    )
    descendant = _allocation(
        parent_index=0,
        budget=60,
        fee_params=child_fee_params,
        recipient="0x3333333333333333333333333333333333333333",
        call_key="0x" + "34" * 32,
    )
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=120),
        message_allocations=[root, descendant],
        num_of_validators=5,
        submitted_value=1220,
        user_value=0,
    )

    allocations = genvm_message_fee_allocation(accounting)

    assert len(allocations) == 1
    assert allocations[0]["recipient"] == root["recipient"]
    assert allocations[0]["call_key"] == bytes.fromhex("12" * 32)
    assert allocations[0]["budget"] == 120
    assert allocations[0]["children"] == [
        {
            "recipient": descendant["recipient"],
            "call_key": bytes.fromhex("34" * 32),
            "budget": 60,
            "on": "decided",
            "fee_params": {
                "Internal": {
                    "leader_timeunits_allocation": 7,
                    "validator_timeunits_allocation": 10,
                    "execution_budget_per_round": 0,
                    "rotations": [0],
                    "max_price_gen_per_time_unit": 1,
                    "storage_fee_max_gas_price": 2**200,
                    "receipt_fee_max_gas_price": 2**200,
                },
            },
            "children": [],
        }
    ]


def test_genvm_message_fee_allocation_does_not_add_uncommitted_fallback():
    external = _allocation(
        message_type=0,
        on_acceptance=False,
        budget=210_000,
        fee_params=_encode_external_fee_params(gas_limit=21_000, max_gas_price=10),
        recipient="0x2222222222222222222222222222222222222222",
        call_key=_external_selector_call_key(b"\x12\x34\x56\x78"),
    )
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=210_000),
        message_allocations=[external],
        num_of_validators=5,
        submitted_value=211_100,
        user_value=0,
    )

    allocations = genvm_message_fee_allocation(accounting)

    assert allocations[0]["recipient"] == external["recipient"]
    assert allocations[0]["call_key"] == bytes.fromhex(
        _external_selector_call_key(b"\x12\x34\x56\x78").removeprefix("0x")
    )
    assert allocations[0]["budget"] == 210_000
    assert allocations[0]["on"] == "finalized"
    assert allocations[0]["fee_params"] == {
        "External": {
            "gas_limit": 21_000,
            "max_gas_price": 10,
        },
    }
    assert len(allocations) == 1


def test_genvm_message_fee_allocation_keeps_legacy_gasless_messages_unmetered():
    allocations = genvm_message_fee_allocation(None)

    assert [next(iter(node["fee_params"])) for node in allocations] == [
        "External",
        "Internal",
        "Internal",
    ]
    assert [node["on"] for node in allocations] == [
        "finalized",
        "finalized",
        "decided",
    ]
    assert all(node["recipient"] is None for node in allocations)
    assert all(node["call_key"] is None for node in allocations)


def test_genvm_message_fee_allocation_uses_empty_allocation_list_without_message_budget():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=0),
        num_of_validators=5,
        submitted_value=1100,
        user_value=0,
    )

    assert genvm_message_fee_allocation(accounting) == []


def test_genvm_message_fee_allocation_rejects_fee_bearing_mode1_until_genvm_supports_it():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=55),
        num_of_validators=5,
        submitted_value=1155,
        user_value=0,
    )

    with pytest.raises(Mode1MessageFeesRequireGenVMPerEmissionSupport):
        genvm_message_fee_allocation(accounting)


def test_create_fee_accounting_records_user_side_budgets():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=55),
        message_allocations=[_allocation(budget=55)],
        num_of_validators=5,
        submitted_value=1167,
        user_value=12,
        sender="0x1111111111111111111111111111111111111111",
    )

    assert accounting["status"] == "active"
    assert accounting["paid_fee_value"] == 1155
    assert accounting["primary_fee_required"] == 1100
    assert accounting["primary_fee_budget"] == 1100
    assert accounting["message_fee_budget"] == 55
    assert accounting["message_allocations"][0]["feeParams"].startswith("0x")


def test_create_fee_accounting_snapshots_locked_fee_policy():
    policy = StudioFeePolicy(
        gen_per_time_unit=7,
        storage_unit_price=11,
        receipt_gas_price=13,
        intrinsic_gas=17,
        bootloader_overhead=19,
        gas_per_changed_slot=23,
        calldata_gas_per_byte=29,
        fixed_propose_receipt_gas=31,
        fixed_message_reveal_gas=37,
    )
    fees_distribution = _fees_distribution(
        max_price_gen_per_time_unit=7,
        storage_fee_max_gas_price=11,
        receipt_fee_max_gas_price=13,
    )

    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5, policy),
        user_value=0,
        policy=policy,
    )

    assert accounting["policy_snapshot"]["gen_per_time_unit"] == 7
    assert accounting["policy_snapshot"]["storage_unit_price"] == 11
    assert accounting["policy_snapshot"]["receipt_gas_price"] == 13
    assert accounting["policy_snapshot"]["fixed_message_reveal_gas"] == 37


def test_genvm_fee_context_uses_locked_fee_policy_by_default():
    locked_policy = StudioFeePolicy(
        gen_per_time_unit=2,
        storage_unit_price=3,
        receipt_gas_price=4,
        calldata_gas_per_byte=9,
    )
    execution_budget = locked_policy.message_fee_params_budget_floor()
    fees_distribution = _fees_distribution(
        execution_budget_per_round=execution_budget,
        max_price_gen_per_time_unit=2,
        storage_fee_max_gas_price=3,
        receipt_fee_max_gas_price=4,
    )
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5, locked_policy),
        user_value=0,
        policy=locked_policy,
    )

    bucket_totals, gas_data = genvm_fee_context(accounting)

    assert bucket_totals == [
        execution_budget,
        0,
        GENVM_UNMETERED_DATA_FEE_BUCKET,
        GENVM_UNMETERED_DATA_FEE_BUCKET,
    ]
    assert gas_data["genPerTimeUnit"] == "2"
    assert gas_data["storageUnitPrice"] == "3"
    assert gas_data["receiptGasPerByte"] == "36"


def test_settle_fee_accounting_refunds_surplus_and_unused_message_bucket():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=55),
        num_of_validators=5,
        submitted_value=1267,
        user_value=12,
        sender="0x1111111111111111111111111111111111111111",
    )

    settled, refund = settle_fee_accounting(accounting)

    assert refund == 155
    assert settled["status"] == "settled"
    assert settled["primary_fee_spent"] == 1100
    assert settled["primary_fee_refunded"] == 100
    assert settled["message_fee_refunded"] == 55


def test_execution_fee_consumption_reduces_execution_budget_refund():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(execution_budget_per_round=100),
        num_of_validators=5,
        submitted_value=1200,
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
    )
    receipt = {"genvm_result": {"data_fees_consumed": [40]}}

    recorded = record_execution_fee_consumption(accounting, receipt)
    settled, refund = settle_fee_accounting(recorded)

    assert recorded["execution_fee_consumed"] == 40
    assert settled["primary_fee_spent"] == 1140
    assert refund == 60


def test_execution_fee_consumption_derives_spend_from_genvm_bucket_remaining():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(execution_budget_per_round=100),
        num_of_validators=5,
        submitted_value=1200,
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
    )
    receipt = {
        "genvm_result": {
            "data_fee_bucket_totals": [100, 80, 60],
            "data_fees_remaining": [70, 90, 10],
        }
    }

    recorded = record_execution_fee_consumption(accounting, receipt)
    settled, refund = settle_fee_accounting(recorded)

    assert recorded["genvm_fee_consumed_buckets"] == [30, 0, 50]
    assert recorded["execution_fee_consumed_buckets"] == [30, 0]
    assert recorded["execution_fee_consumed"] == 30
    assert recorded["genvm_fee_bucket_report"] == {
        "receiptAndNondetOutput": 30,
        "storage": 0,
        "message": 50,
        "totalExecution": 30,
        "totalWithMessage": 80,
        "executionBudgetPerRound": 100,
        "executionBudgetRemaining": 70,
        "executionBudgetOverrun": 0,
        "executionBudgetExceeded": False,
        "buckets": [
            {"index": 0, "name": "receiptAndNondetOutput", "consumed": 30},
            {"index": 1, "name": "storage", "consumed": 0},
            {"index": 2, "name": "message", "consumed": 50},
        ],
    }
    assert (
        recorded["execution_fee_report"]["genvmBuckets"]
        == recorded["genvm_fee_bucket_report"]
    )


def test_execution_fee_report_uses_locked_receipt_price_by_default():
    locked_policy = StudioFeePolicy(receipt_gas_price=2)
    fees_distribution = _fees_distribution(
        execution_budget_per_round=locked_policy.message_fee_params_budget_floor(),
        receipt_fee_max_gas_price=2,
    )
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5, locked_policy),
        user_value=0,
        policy=locked_policy,
    )
    receipt = {
        "eq_outputs": {"0": base64.b64encode(b"aa").decode("ascii")},
        "genvm_result": {"data_fees_consumed": [40]},
    }

    recorded = record_execution_fee_consumption(accounting, receipt)

    report = recorded["execution_fee_report"]
    assert report["receiptGasPrice"] == 2
    assert report["proposalReceipt"]["fee"] == (
        report["proposalReceipt"]["estimatedGas"] * 2
    )


def test_execution_fee_report_enforces_consensus_eq_outputs_byte_cap():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(execution_budget_per_round=1_000_000),
        num_of_validators=5,
        submitted_value=1_001_100,
        user_value=0,
    )
    receipt = {"genvm_result": {"eq_blocks_outputs_length": 9}}

    with pytest.raises(EqOutputsTooLarge, match=r"EqOutputsTooLarge\(9,8\)"):
        record_execution_fee_consumption(
            accounting,
            receipt,
            StudioFeePolicy(max_eq_outputs_bytes=8),
        )


def test_execution_fee_consumption_reports_execution_budget_overrun():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(execution_budget_per_round=100),
        num_of_validators=5,
        submitted_value=1200,
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
    )
    receipt = {
        "genvm_result": {
            "data_fees_consumed": [80, 35, 0],
        }
    }

    recorded = record_execution_fee_consumption(accounting, receipt)
    bucket_report = recorded["execution_fee_report"]["genvmBuckets"]

    assert recorded["execution_fee_consumed"] == 115
    assert bucket_report["totalExecution"] == 115
    assert bucket_report["executionBudgetPerRound"] == 100
    assert bucket_report["executionBudgetRemaining"] == 0
    assert bucket_report["executionBudgetOverrun"] == 15
    assert bucket_report["executionBudgetExceeded"] is True
    assert (
        recorded["execution_fee_report"]["budgetExhaustionReason"]
        == "ExecutionBudgetExceeded"
    )


def test_execution_fee_consumption_reports_zero_execution_budget_overrun():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(execution_budget_per_round=0),
        num_of_validators=5,
        submitted_value=1100,
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
    )
    receipt = {
        "genvm_result": {
            "data_fees_consumed": [1, 0, 0],
        }
    }

    recorded = record_execution_fee_consumption(accounting, receipt)
    bucket_report = recorded["execution_fee_report"]["genvmBuckets"]

    assert recorded["execution_fee_consumed"] == 1
    assert bucket_report["executionBudgetPerRound"] == 0
    assert bucket_report["executionBudgetRemaining"] == 0
    assert bucket_report["executionBudgetOverrun"] == 1
    assert bucket_report["executionBudgetExceeded"] is True
    assert (
        recorded["execution_fee_report"]["budgetExhaustionReason"]
        == "ExecutionBudgetExceeded"
    )


def test_execution_fee_report_preserves_genvm_budget_exhaustion_reason():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(execution_budget_per_round=100),
        num_of_validators=5,
        submitted_value=1200,
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
    )
    receipt = {
        "genvm_result": {
            "budgetExhaustionReason": "MessageBudgetExceeded",
            "error_code": "ExecutionBudgetExceeded",
            "data_fees_consumed": [10, 0, 90],
        }
    }

    recorded = record_execution_fee_consumption(accounting, receipt)

    assert (
        recorded["execution_fee_report"]["budgetExhaustionReason"]
        == "MessageBudgetExceeded"
    )


def test_budget_exhaustion_discards_receipt_messages_from_fee_consumption():
    fee_params = _encode_internal_fee_params()
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=55),
        num_of_validators=5,
        submitted_value=1_155,
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
    )
    receipt = {
        "genvm_result": {
            "budgetExhaustionReason": "MessageBudgetExceeded",
            "data_fees_consumed": [10, 3, 55],
        },
        "pending_transactions": [
            {
                "messageType": "Internal",
                "recipient": "0x2222222222222222222222222222222222222222",
                "data": "0x1234",
                "onAcceptance": True,
                "value": 0,
                "feeParams": fee_params,
                "declaredBudget": 55,
                "callKey": CALL_KEY_WILDCARD,
            }
        ],
    }

    recorded = record_execution_fee_consumption(
        accounting,
        receipt,
        StudioFeePolicy(receipt_gas_price=0),
    )

    report = recorded["execution_fee_report"]
    assert recorded["message_fee_consumed"] == 0
    assert recorded["genvm_message_fee_consumed"] == 55
    assert recorded["execution_fee_consumed_buckets"] == [10, 0]
    assert "message_fees_recorded_from_receipt" not in recorded
    assert "messageReveal" not in report
    assert report["budgetExhaustionReason"] == "MessageBudgetExceeded"
    assert report["messageFees"]["declaredConsumed"] == 0
    assert report["messageFees"]["genvmMeteredConsumed"] == 55


def test_simulation_fee_consumption_rejects_declared_message_without_bucket():
    fee_params = _encode_internal_fee_params()
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=0),
        num_of_validators=5,
        submitted_value=1_100,
        user_value=0,
    )
    receipt = {
        "pending_transactions": [
            {
                "messageType": "Internal",
                "recipient": "0x2222222222222222222222222222222222222222",
                "data": "0x1234",
                "onAcceptance": True,
                "value": 0,
                "feeParams": fee_params,
                "declaredBudget": 55,
                "callKey": CALL_KEY_WILDCARD,
            }
        ],
    }

    with pytest.raises(MessageBudgetExceeded):
        record_execution_fee_consumption(accounting, receipt, StudioFeePolicy())


def test_simulation_fee_consumption_skips_legacy_unmetered_message_without_bucket():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=0),
        num_of_validators=5,
        submitted_value=1_100,
        user_value=0,
    )
    receipt = {
        "pending_transactions": [
            {
                "messageType": "Internal",
                "recipient": "0x2222222222222222222222222222222222222222",
                "data": "0x1234",
                "onAcceptance": True,
                "value": 0,
                "declaredBudget": 0,
                "callKey": CALL_KEY_WILDCARD,
            }
        ],
    }

    recorded = record_execution_fee_consumption(accounting, receipt, StudioFeePolicy())
    message = recorded["execution_fee_report"]["messageReveal"]["messages"][0]

    assert recorded["message_fee_consumed"] == 0
    assert "message_fees_recorded_from_receipt" not in recorded
    assert message["messageFeeMode"] == "mode1"
    assert message["declaredBudget"] == 0
    assert message["feeParamsDecoded"] is None


def test_simulation_fee_consumption_fills_mode2_payload_from_allocation():
    fee_params = _encode_internal_fee_params()
    recipient = "0x2222222222222222222222222222222222222222"
    allocation = _allocation(
        recipient=recipient,
        budget=75,
        fee_params=fee_params,
    )
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(
            execution_budget_per_round=1_000,
            total_message_fees=75,
        ),
        message_allocations=[allocation],
        num_of_validators=5,
        submitted_value=2_175,
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
    )
    receipt = {
        "genvm_result": {
            "data_fee_bucket_totals": [1_000, 1_000, 75],
            "data_fees_remaining": [920, 1_000, 20],
        },
        "pending_transactions": [
            {
                "messageType": "Internal",
                "recipient": recipient,
                "data": "0x1234",
                "onAcceptance": True,
                "value": 0,
                "declaredBudget": 0,
                "callKey": CALL_KEY_WILDCARD,
            }
        ],
    }

    recorded = record_execution_fee_consumption(accounting, receipt, StudioFeePolicy())
    recorded_again = record_execution_fee_consumption(
        recorded,
        receipt,
        StudioFeePolicy(),
    )
    message = recorded["execution_fee_report"]["messageReveal"]["messages"][0]
    assert recorded["execution_fee_consumed"] == 80
    assert recorded["execution_fee_consumed_buckets"] == [80, 0]
    assert recorded["genvm_fee_consumed_buckets"] == [80, 0, 55]
    assert recorded["genvm_message_fee_consumed"] == 55
    assert recorded["message_fee_consumed"] == 75
    assert recorded["allocation_consumed"] == {"0": 75}
    assert recorded["message_fees_recorded_from_receipt"] is True
    assert recorded["execution_fee_report"]["messageFees"] == {
        "budget": 75,
        "declaredConsumed": 75,
        "genvmMeteredConsumed": 55,
        "declaredRefunded": 0,
        "remaining": 0,
        "meteringDelta": 20,
    }
    assert message["messageFeeMode"] == "mode2"
    assert message["feeParams"] == "0x" + fee_params.hex()
    assert message["declaredBudget"] == 75
    assert message["allocationSubtree"] == "0x"
    assert message["allocationSubtreeBytes"] == 0
    assert recorded_again["message_fee_consumed"] == 75
    assert recorded_again["allocation_consumed"] == {"0": 75}


def test_settlement_refreshes_message_fee_report_after_message_refund():
    fee_params = _encode_internal_fee_params()
    recipient = "0x2222222222222222222222222222222222222222"
    unused_recipient = "0x3333333333333333333333333333333333333333"
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=110),
        message_allocations=[
            _allocation(recipient=recipient, budget=55, fee_params=fee_params),
            _allocation(
                recipient=unused_recipient,
                budget=55,
                fee_params=fee_params,
            ),
        ],
        num_of_validators=5,
        submitted_value=1_210,
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
    )
    receipt = {
        "pending_transactions": [
            {
                "messageType": "Internal",
                "recipient": recipient,
                "data": "0x1234",
                "onAcceptance": True,
                "value": 0,
                "declaredBudget": 0,
                "callKey": CALL_KEY_WILDCARD,
            }
        ],
    }

    recorded = record_execution_fee_consumption(accounting, receipt, StudioFeePolicy())

    assert recorded["execution_fee_report"]["messageFees"] == {
        "budget": 110,
        "declaredConsumed": 55,
        "genvmMeteredConsumed": 0,
        "declaredRefunded": 0,
        "remaining": 55,
        "meteringDelta": 55,
    }

    settled, refund = settle_fee_accounting(recorded)

    assert refund == 55
    assert settled["message_fee_refunded"] == 55
    assert settled["execution_fee_report"]["messageFees"] == {
        "budget": 110,
        "declaredConsumed": 55,
        "genvmMeteredConsumed": 0,
        "declaredRefunded": 55,
        "remaining": 0,
        "meteringDelta": 55,
    }


def test_cancel_refreshes_message_fee_report_after_partial_message_consumption():
    fee_params = _encode_internal_fee_params()
    recipient = "0x2222222222222222222222222222222222222222"
    unused_recipient = "0x3333333333333333333333333333333333333333"
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=110),
        message_allocations=[
            _allocation(recipient=recipient, budget=55, fee_params=fee_params),
            _allocation(
                recipient=unused_recipient,
                budget=55,
                fee_params=fee_params,
            ),
        ],
        num_of_validators=5,
        submitted_value=1_210,
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
    )
    recorded = record_execution_fee_consumption(
        accounting,
        {
            "pending_transactions": [
                {
                    "messageType": "Internal",
                    "recipient": recipient,
                    "data": "0x1234",
                    "onAcceptance": True,
                    "value": 0,
                    "declaredBudget": 0,
                    "callKey": CALL_KEY_WILDCARD,
                }
            ],
        },
        StudioFeePolicy(),
    )

    canceled, refund = cancel_fee_accounting(recorded)

    assert refund == 1_155
    assert canceled["message_fee_refunded"] == 55
    assert canceled["execution_fee_report"]["messageFees"] == {
        "budget": 110,
        "declaredConsumed": 55,
        "genvmMeteredConsumed": 0,
        "declaredRefunded": 55,
        "remaining": 0,
        "meteringDelta": 55,
    }


def test_top_up_refreshes_message_fee_report_after_budget_increase():
    fee_params = _encode_internal_fee_params()
    recipient = "0x2222222222222222222222222222222222222222"
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=55),
        message_allocations=[
            _allocation(recipient=recipient, budget=55, fee_params=fee_params)
        ],
        num_of_validators=5,
        submitted_value=1_155,
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
    )
    recorded = record_execution_fee_consumption(
        accounting,
        {
            "pending_transactions": [
                {
                    "messageType": "Internal",
                    "recipient": recipient,
                    "data": "0x1234",
                    "onAcceptance": True,
                    "value": 0,
                    "declaredBudget": 0,
                    "callKey": CALL_KEY_WILDCARD,
                }
            ],
        },
        StudioFeePolicy(),
    )

    assert recorded["execution_fee_report"]["messageFees"]["remaining"] == 0

    topped_up = apply_fee_top_up(
        recorded,
        fees_distribution=_top_up_distribution(
            total_message_fees=25,
        ),
        amount=25,
    )

    assert topped_up["message_fee_budget"] == 80
    assert topped_up["execution_fee_report"]["messageFees"] == {
        "budget": 80,
        "declaredConsumed": 55,
        "genvmMeteredConsumed": 0,
        "declaredRefunded": 0,
        "remaining": 25,
        "meteringDelta": 55,
    }


def test_simulation_fee_report_labels_prepopulated_allocation_messages_as_mode2():
    fee_params = _encode_internal_fee_params()
    recipient = "0x2222222222222222222222222222222222222222"
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=55),
        message_allocations=[
            _allocation(
                recipient=recipient,
                budget=55,
                fee_params=fee_params,
            )
        ],
        num_of_validators=5,
        submitted_value=1_155,
        user_value=0,
    )
    receipt = {
        "pending_transactions": [
            {
                "messageType": "Internal",
                "recipient": recipient,
                "data": "0x1234",
                "onAcceptance": True,
                "value": 0,
                "feeParams": fee_params,
                "declaredBudget": 55,
                "callKey": CALL_KEY_WILDCARD,
            }
        ],
    }

    recorded = record_execution_fee_consumption(accounting, receipt, StudioFeePolicy())
    message = recorded["execution_fee_report"]["messageReveal"]["messages"][0]

    assert recorded["message_fee_consumed"] == 55
    assert message["messageFeeMode"] == "mode2"


def test_flat_array_simulation_fee_report_preserves_missing_receipt_subtree():
    fee_params = _encode_internal_fee_params()
    child_fee_params = _encode_internal_fee_params()
    recipient = "0x2222222222222222222222222222222222222222"
    child_recipient = "0x3333333333333333333333333333333333333333"
    call_key = "0x" + "12" * 32
    child_call_key = "0x" + "34" * 32
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=110),
        message_allocations=[
            _allocation(
                recipient=recipient,
                call_key=call_key,
                budget=110,
                fee_params=fee_params,
            ),
            _allocation(
                parent_index=0,
                recipient=child_recipient,
                call_key=child_call_key,
                budget=55,
                fee_params=child_fee_params,
            ),
        ],
        num_of_validators=5,
        submitted_value=1_210,
        user_value=0,
    )
    receipt = {
        "pending_transactions": [
            {
                "messageType": "Internal",
                "recipient": recipient,
                "data": "0x1234",
                "onAcceptance": True,
                "value": 0,
                "feeParams": fee_params,
                "declaredBudget": 110,
                "callKey": call_key,
            }
        ],
    }

    recorded = record_execution_fee_consumption(accounting, receipt, StudioFeePolicy())
    message = recorded["execution_fee_report"]["messageReveal"]["messages"][0]
    assert recorded["message_fee_consumed"] == 110
    assert recorded["allocation_consumed"] == {"0": 110}
    assert message["messageFeeMode"] == "mode2"
    assert message["allocationSubtree"] == "0x"
    assert message["allocationSubtreeBytes"] == 0


def test_simulation_fee_consumption_records_external_allocation_reservation():
    call_key = _external_selector_call_key(bytes.fromhex("aabbccdd"))
    recipient = "0x4444444444444444444444444444444444444444"
    fee_params = _encode_external_fee_params(gas_limit=100, max_gas_price=10)
    allocation = _allocation(
        message_type=0,
        on_acceptance=False,
        recipient=recipient,
        call_key=call_key,
        budget=1_000,
        fee_params=fee_params,
    )
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=1_000),
        message_allocations=[allocation],
        num_of_validators=5,
        submitted_value=2_100,
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
    )
    receipt = {
        "pending_transactions": [
            {
                "isEthSend": True,
                "recipient": recipient,
                "data": "0xaabbccdd0102",
                "onAcceptance": False,
                "value": 0,
                "declaredBudget": 0,
                "gasUsed": 11,
            }
        ],
    }

    recorded = record_execution_fee_consumption(
        accounting,
        receipt,
        StudioFeePolicy(receipt_gas_price=7),
    )
    message = recorded["execution_fee_report"]["messageReveal"]["messages"][0]

    assert recorded["message_fee_consumed"] == 700
    assert recorded["allocation_consumed"] == {"0": 700}
    assert recorded["external_message_fee_reserved"] == 700
    assert recorded["external_message_fee_reimbursed"] == 77
    assert recorded["external_message_fee_remainder"] == 623
    assert recorded["external_message_events"] == [
        {
            "recipient": recipient,
            "callKey": call_key,
            "allocationIndex": 0,
            "gasLimit": 100,
            "lockedGasPrice": 7,
            "reservation": 700,
            "gasUsed": 11,
            "reimbursement": 77,
            "remainder": 623,
            "executionRecorded": True,
            "fundingOffset": 0,
            "fundingOwners": [
                {
                    "recipient": "0x1111111111111111111111111111111111111111",
                    "amount": 700,
                }
            ],
        }
    ]
    assert recorded["execution_fee_report"]["messageFees"] == {
        "budget": 1_000,
        "declaredConsumed": 0,
        "genvmMeteredConsumed": 0,
        "declaredRefunded": 0,
        "remaining": 300,
        "meteringDelta": 0,
        "externalReserved": 700,
        "externalReimbursed": 77,
        "externalRemainder": 623,
        "externalSettled": 700,
        "totalConsumed": 700,
    }
    assert recorded["external_message_fee_payouts"] == [
        {
            "recipient": "0x1111111111111111111111111111111111111111",
            "amount": 77,
            "source": "external-executor-reimbursement",
        },
        {
            "recipient": "0x1111111111111111111111111111111111111111",
            "amount": 623,
            "source": "external-execution-remainder",
        },
    ]
    preset = recorded["recommended_fee_preset"]
    assert preset["messageBudgetMode"] == "allocation-preserved"
    assert preset["distribution"]["totalMessageFees"] == 1_000
    assert preset["messageAllocations"] == accounting["message_allocations"]
    assert preset["observed"]["messageFeeBudget"] == 700
    assert preset["observed"]["declaredMessageFees"] == 0
    assert preset["observed"]["externalMessageReserved"] == 700
    assert message["messageFeeMode"] == "external"
    assert message["messageType"] == "External"
    assert message["callKey"] == call_key

    refunded = refund_failed_external_message_fee(
        recorded,
        {
            "messageType": 0,
            "recipient": recipient,
            "onAcceptance": False,
            "declaredBudget": 0,
            "callKey": call_key,
        },
    )

    assert refunded["message_fee_consumed"] == 700
    assert refunded["allocation_consumed"] == {"0": 700}
    assert refunded["external_message_fee_reimbursed"] == 77
    assert refunded["external_message_events"][0]["failureRefunded"] is True
    assert refunded["external_message_refund_events"][0]["feeRefunded"] == 0
    assert (
        refunded["execution_fee_report"]["messageFees"]
        == recorded["execution_fee_report"]["messageFees"]
    )


def test_execution_fee_consumption_reports_deterministic_receipt_fee_components():
    fee_params = _encode_internal_fee_params()
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(
            execution_budget_per_round=1_000_000,
            total_message_fees=55,
        ),
        message_allocations=[
            _allocation(
                recipient="0x2222222222222222222222222222222222222222",
                call_key="0x" + "34" * 32,
                budget=55,
                fee_params=fee_params,
            )
        ],
        num_of_validators=5,
        submitted_value=1_001_155,
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
    )
    policy = StudioFeePolicy(receipt_gas_price=2)
    allocation_subtree = [
        {
            "messageType": 1,
            "onAcceptance": True,
            "parentIndex": NODE_ROOT_SENTINEL,
            "recipient": "0x2222222222222222222222222222222222222222",
            "callKey": "0x" + "34" * 32,
            "budget": 55,
            "feeParams": base64.b64encode(fee_params).decode("ascii"),
        },
    ]
    receipt = {
        "result": base64.b64encode(b"\x00ok").decode("ascii"),
        "eq_outputs": {"0": base64.b64encode(b"aa").decode("ascii")},
        "pending_transactions": [
            {
                "address": "0x2222222222222222222222222222222222222222",
                "calldata": base64.b64encode(b"\x12\x34").decode("ascii"),
                "on": "accepted",
                "value": 7,
                "fee_params": base64.b64encode(fee_params).decode("ascii"),
                "declared_budget": 55,
                "allocation_subtree": allocation_subtree,
                "call_key": "0x" + "34" * 32,
            }
        ],
        "genvm_result": {"data_fees_consumed": [40]},
    }

    recorded = record_execution_fee_consumption(accounting, receipt, policy)
    report = recorded["execution_fee_report"]
    proposal = report["proposalReceipt"]
    message_reveal = report["messageReveal"]
    expected_subtree = encode(
        [MESSAGE_ALLOCATION_NODE_ABI_TYPE],
        [
            [
                (
                    1,
                    True,
                    NODE_ROOT_SENTINEL,
                    "0x2222222222222222222222222222222222222222",
                    bytes.fromhex("34" * 32),
                    55,
                    fee_params,
                ),
            ]
        ],
    )
    expected_message_bytes = len(
        encode(
            [SUBMITTED_MESSAGE_ABI_TYPE],
            [
                [
                    (
                        1,
                        "0x2222222222222222222222222222222222222222",
                        7,
                        b"\x12\x34",
                        True,
                        0,
                        fee_params,
                        55,
                        expected_subtree,
                        bytes.fromhex("34" * 32),
                        False,
                    )
                ]
            ],
        )
    )

    assert proposal["eqBlocksOutputsLength"] == len(rlp.encode([b"aa", b"padded"]))
    assert proposal["receiptBytes"] == (
        policy.receipt_wrapper_bytes + proposal["eqBlocksOutputsLength"]
    )
    assert proposal["estimatedGas"] == policy.estimate_propose_receipt_gas(
        proposal["receiptBytes"]
    )
    assert proposal["fee"] == proposal["estimatedGas"] * policy.receipt_gas_price
    assert message_reveal["messageCount"] == 1
    assert message_reveal["messageBytes"] == expected_message_bytes
    assert message_reveal["estimatedGas"] == policy.estimate_message_reveal_gas(
        message_reveal["messageBytes"],
        message_reveal["messageCount"],
    )
    assert message_reveal[
        "consensusAdditionalGas"
    ] == policy.estimate_consensus_message_reveal_gas(
        message_reveal["messageBytes"],
        message_reveal["messageCount"],
    )
    assert message_reveal["consensusAdditionalFee"] == (
        message_reveal["consensusAdditionalGas"] * policy.receipt_gas_price
    )
    assert message_reveal["studioFixedOverheadGas"] == (
        message_reveal["estimatedGas"] - message_reveal["consensusAdditionalGas"]
    )
    assert message_reveal["studioFixedOverheadFee"] == (
        message_reveal["fee"] - message_reveal["consensusAdditionalFee"]
    )
    assert message_reveal["messages"] == [
        {
            "messageFeeMode": "mode2",
            "messageType": "Internal",
            "recipient": "0x2222222222222222222222222222222222222222",
            "value": 7,
            "dataBytes": 2,
            "onAcceptance": True,
            "saltNonce": 0,
            "feeParams": "0x" + fee_params.hex(),
            "feeParamsDecoded": {
                "leaderTimeunitsAllocation": 5,
                "validatorTimeunitsAllocation": 10,
                "appealRounds": 0,
                "executionBudgetPerRound": 0,
                "rotations": [0],
                "maxPriceGenPerTimeUnit": 1,
                "storageFeeMaxGasPrice": 2**200,
                "receiptFeeMaxGasPrice": 2**200,
            },
            "feeParamsBytes": len(fee_params),
            "declaredBudget": 55,
            "allocationSubtree": "0x" + expected_subtree.hex(),
            "allocationSubtreeBytes": len(expected_subtree),
            "callKey": "0x" + "34" * 32,
            "useBalance": False,
        }
    ]
    assert message_reveal["fee"] == (
        message_reveal["estimatedGas"] * policy.receipt_gas_price
    )
    assert report["totalEstimatedFee"] == (
        proposal["fee"] + message_reveal["consensusAdditionalFee"]
    )
    assert report["totalStudioMeteredFee"] == proposal["fee"] + message_reveal["fee"]
    assert recorded["execution_fee_consumed"] == report["totalEstimatedFee"]
    assert recorded["execution_fee_consumed_buckets"] == [
        report["totalEstimatedFee"],
        0,
    ]
    assert (
        report["chargeableExecution"]["totalExecution"] == report["totalEstimatedFee"]
    )
    assert report["genvmBuckets"]["totalExecution"] == 40
    assert report["executionMetering"] == {
        "chargeableExecutionFee": report["totalEstimatedFee"],
        "genvmReportedExecution": 40,
        "genvmDeltaFromChargeable": 40 - report["totalEstimatedFee"],
    }
    assert recorded["message_fee_consumed"] == 55


def test_execution_fee_consumption_ignores_genvm_message_reveal_precharge_without_messages():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(execution_budget_per_round=300),
        num_of_validators=5,
        submitted_value=1_400,
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
    )
    policy = StudioFeePolicy(
        receipt_gas_price=2,
        intrinsic_gas=10,
        bootloader_overhead=20,
        gas_per_changed_slot=3,
        calldata_gas_per_byte=4,
        fixed_propose_receipt_gas=30,
        fixed_message_reveal_gas=40,
        receipt_wrapper_bytes=0,
    )
    proposal_fee = policy.estimate_propose_receipt_gas(0) * policy.receipt_gas_price
    reveal_precharge = (
        policy.estimate_message_reveal_gas(0, 0) * policy.receipt_gas_price
    )
    storage_fee = 15
    receipt = {
        "genvm_result": {
            "eqBlocksOutputsLength": 0,
            "data_fees_consumed": [proposal_fee + reveal_precharge, storage_fee],
        }
    }

    recorded = record_execution_fee_consumption(accounting, receipt, policy)
    report = recorded["execution_fee_report"]

    assert "messageReveal" not in report
    assert report["totalEstimatedFee"] == proposal_fee
    assert recorded["execution_fee_consumed_buckets"] == [
        proposal_fee,
        storage_fee,
    ]
    assert recorded["execution_fee_consumed"] == proposal_fee + storage_fee
    assert "budgetExhaustionReason" not in report
    assert report["chargeableExecution"]["executionBudgetExceeded"] is False
    assert report["genvmBuckets"]["executionBudgetExceeded"] is True
    assert report["executionMetering"] == {
        "chargeableExecutionFee": proposal_fee + storage_fee,
        "genvmReportedExecution": proposal_fee + reveal_precharge + storage_fee,
        "genvmDeltaFromChargeable": reveal_precharge,
    }


def test_execution_fee_consumption_charges_consensus_message_reveal_fee_only():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(execution_budget_per_round=1_000),
        num_of_validators=5,
        submitted_value=2_100,
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
    )
    policy = StudioFeePolicy(
        receipt_gas_price=2,
        intrinsic_gas=10,
        bootloader_overhead=20,
        gas_per_changed_slot=3,
        calldata_gas_per_byte=4,
        fixed_propose_receipt_gas=30,
        fixed_message_reveal_gas=40,
        receipt_wrapper_bytes=0,
    )
    storage_fee = 17
    receipt = {
        "genvm_result": {
            "eqBlocksOutputsLength": 0,
            "data_fees_consumed": [999, storage_fee],
        },
        "pending_transactions": [
            {
                "messageType": "Internal",
                "recipient": "0x2222222222222222222222222222222222222222",
                "data": "0x1234",
                "onAcceptance": True,
                "value": 0,
                "declaredBudget": 0,
                "callKey": CALL_KEY_WILDCARD,
            }
        ],
    }

    recorded = record_execution_fee_consumption(accounting, receipt, policy)
    report = recorded["execution_fee_report"]
    proposal_fee = report["proposalReceipt"]["fee"]
    message_reveal = report["messageReveal"]
    chargeable_receipt_fee = proposal_fee + message_reveal["consensusAdditionalFee"]

    assert message_reveal["fee"] > message_reveal["consensusAdditionalFee"]
    assert report["totalEstimatedFee"] == chargeable_receipt_fee
    assert report["totalStudioMeteredFee"] == proposal_fee + message_reveal["fee"]
    assert recorded["execution_fee_consumed_buckets"] == [
        chargeable_receipt_fee,
        storage_fee,
    ]
    assert recorded["execution_fee_consumed"] == chargeable_receipt_fee + storage_fee
    assert report["executionMetering"] == {
        "chargeableExecutionFee": chargeable_receipt_fee + storage_fee,
        "genvmReportedExecution": 999 + storage_fee,
        "genvmDeltaFromChargeable": (
            999 + storage_fee - chargeable_receipt_fee - storage_fee
        ),
    }


def test_execution_fee_consumption_reports_chargeable_budget_overrun():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(execution_budget_per_round=300),
        num_of_validators=5,
        submitted_value=1_400,
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
    )
    policy = StudioFeePolicy(
        receipt_gas_price=2,
        intrinsic_gas=10,
        bootloader_overhead=20,
        gas_per_changed_slot=3,
        calldata_gas_per_byte=4,
        fixed_propose_receipt_gas=30,
        fixed_message_reveal_gas=40,
        receipt_wrapper_bytes=0,
    )
    receipt = {
        "genvm_result": {
            "eqBlocksOutputsLength": 0,
            "data_fees_consumed": [10, 0],
        },
        "pending_transactions": [
            {
                "messageType": "Internal",
                "recipient": "0x2222222222222222222222222222222222222222",
                "data": "0x1234",
                "onAcceptance": True,
                "value": 0,
                "declaredBudget": 0,
                "callKey": CALL_KEY_WILDCARD,
            }
        ],
    }

    recorded = record_execution_fee_consumption(accounting, receipt, policy)
    report = recorded["execution_fee_report"]

    assert report["genvmBuckets"]["executionBudgetExceeded"] is False
    assert report["chargeableExecution"]["executionBudgetExceeded"] is True
    assert report["chargeableExecution"]["executionBudgetOverrun"] == (
        recorded["execution_fee_consumed"] - 300
    )
    assert report["budgetExhaustionReason"] == "ExecutionBudgetExceeded"


def test_message_fee_consumption_allows_overreported_total_and_consumes_recalculated_sum():
    fee_params_a = _encode_internal_fee_params(leader_timeunits=6)
    fee_params_b = _encode_internal_fee_params(leader_timeunits=7)
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=200),
        num_of_validators=5,
        submitted_value=1_300,
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
    )
    receipt = {
        "genvm_result": {
            "messageFeesConsumed": 200,
        },
        "pending_transactions": [
            {
                "messageType": "Internal",
                "recipient": "0x2222222222222222222222222222222222222222",
                "data": "0x1234",
                "onAcceptance": True,
                "value": 1,
                "feeParams": fee_params_a,
                "declaredBudget": 60,
                "callKey": "0x" + "12" * 32,
            },
            {
                "messageType": "Internal",
                "recipient": "0x3333333333333333333333333333333333333333",
                "data": "0xab",
                "onAcceptance": False,
                "value": 2,
                "feeParams": fee_params_b,
                "declaredBudget": 70,
                "callKey": "0x" + "34" * 32,
            },
        ],
    }

    recorded = record_execution_fee_consumption(accounting, receipt, StudioFeePolicy())

    assert recorded["message_fee_consumed"] == 130
    assert recorded["reported_message_fees_total"] == 200
    assert recorded["message_consumption_events"][-1] == {
        "consumed": 130,
        "internalConsumed": 130,
        "externalReimbursed": 0,
        "remaining": 70,
    }
    assert recorded["execution_fee_report"]["messageFees"]["reportedTotal"] == 200
    assert recorded["execution_fee_report"]["messageFees"]["declaredConsumed"] == 130
    assert recorded["execution_fee_report"]["messageFees"]["remaining"] == 70


def test_execution_fee_consumption_attaches_padded_recommended_fee_preset():
    policy = StudioFeePolicy(
        gen_per_time_unit=1,
        storage_unit_price=0,
        receipt_gas_price=0,
    )
    fee_params_a = _encode_internal_fee_params(leader_timeunits=6)
    fee_params_b = _encode_internal_fee_params(leader_timeunits=7)
    fees_distribution = _fees_distribution(
        execution_budget_per_round=100,
        total_message_fees=130,
    )
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5, policy),
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
        policy=policy,
    )
    receipt = {
        "genvm_result": {"data_fees_consumed": [80, 20, 130]},
        "pending_transactions": [
            {
                "messageType": "Internal",
                "recipient": "0x2222222222222222222222222222222222222222",
                "data": "0x1234",
                "onAcceptance": True,
                "value": 0,
                "feeParams": fee_params_a,
                "declaredBudget": 60,
                "callKey": "0x" + "12" * 32,
            },
            {
                "messageType": "Internal",
                "recipient": "0x3333333333333333333333333333333333333333",
                "data": "0xab",
                "onAcceptance": False,
                "value": 0,
                "feeParams": fee_params_b,
                "declaredBudget": 70,
                "callKey": "0x" + "34" * 32,
            },
        ],
    }

    recorded = record_execution_fee_consumption(accounting, receipt, policy)
    preset = recorded["recommended_fee_preset"]

    assert recorded["execution_fee_consumed"] == 100
    assert preset["paddingBps"] == DEFAULT_PRICE_CAP_HEADROOM_BPS
    assert preset["distribution"]["executionBudgetPerRound"] == 120
    assert preset["distribution"]["totalMessageFees"] == 156
    assert preset["messageBudgetMode"] == "observed"
    assert preset["feeValue"] == required_fee_deposit(
        preset["distribution"],
        5,
        policy,
    )
    assert preset["observed"] == {
        "executionFee": 100,
        "messageFeeBudget": 130,
        "declaredMessageFees": 130,
        "externalMessageReserved": 0,
        "totalEstimatedFee": 0,
        "totalStudioMeteredFee": 0,
    }


def test_recommended_fee_preset_preserves_mode2_allocation_budget():
    policy = StudioFeePolicy(
        gen_per_time_unit=1,
        storage_unit_price=0,
        receipt_gas_price=0,
    )
    fee_params = _encode_internal_fee_params(leader_timeunits=6)
    child_fee_params = _encode_internal_fee_params(leader_timeunits=7)
    recipient = "0x2222222222222222222222222222222222222222"
    child_recipient = "0x3333333333333333333333333333333333333333"
    call_key = "0x" + "12" * 32
    child_call_key = "0x" + "34" * 32
    fees_distribution = _fees_distribution(
        execution_budget_per_round=100,
        total_message_fees=300,
    )
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        message_allocations=[
            _allocation(
                recipient=recipient,
                call_key=call_key,
                budget=300,
                fee_params=fee_params,
            ),
            _allocation(
                parent_index=0,
                recipient=child_recipient,
                call_key=child_call_key,
                budget=60,
                fee_params=child_fee_params,
            ),
        ],
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5, policy),
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
        policy=policy,
    )
    receipt = {
        "genvm_result": {"data_fees_consumed": [80]},
        "pending_transactions": [
            {
                "messageType": "Internal",
                "recipient": recipient,
                "data": "0x1234",
                "onAcceptance": True,
                "value": 0,
                "feeParams": fee_params,
                "declaredBudget": 110,
                "callKey": call_key,
            }
        ],
    }

    recorded = record_execution_fee_consumption(accounting, receipt, policy)
    preset = recorded["recommended_fee_preset"]
    message = recorded["execution_fee_report"]["messageReveal"]["messages"][0]

    assert recorded["message_fee_consumed"] == 110
    assert recorded["allocation_consumed"] == {"0": 110}
    assert message["messageFeeMode"] == "mode2"
    assert preset["distribution"]["executionBudgetPerRound"] == 96
    assert preset["distribution"]["totalMessageFees"] == 300
    assert preset["messageBudgetMode"] == "allocation-preserved"
    assert preset["messageAllocations"] == accounting["message_allocations"]
    assert preset["feeValue"] == required_fee_deposit(
        preset["distribution"],
        5,
        policy,
    )
    assert preset["observed"]["messageFeeBudget"] == 110
    assert preset["observed"]["declaredMessageFees"] == 110


def test_recommended_fee_preset_adds_message_execution_headroom_over_floor():
    policy = StudioFeePolicy(
        gen_per_time_unit=1,
        storage_unit_price=0,
        receipt_gas_price=1,
    )
    floor = policy.message_fee_params_budget_floor()
    fee_params = _encode_internal_fee_params()
    fees_distribution = _fees_distribution(
        execution_budget_per_round=floor,
        total_message_fees=55,
        receipt_fee_max_gas_price=1,
    )
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        message_allocations=[
            _allocation(
                budget=55,
                fee_params=fee_params,
            ),
        ],
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5, policy),
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
        policy=policy,
    )
    recorded = record_execution_fee_consumption(
        accounting,
        {"genvm_result": {"data_fees_consumed": [10, 0, 0]}},
        policy,
    )

    preset = recorded["recommended_fee_preset"]
    observed_with_padding = (
        recorded["execution_fee_report"]["totalEstimatedFee"]
        * DEFAULT_PRICE_CAP_HEADROOM_BPS
        + 9_999
    ) // 10_000
    assert preset["distribution"]["executionBudgetPerRound"] == max(
        floor + 10_000,
        observed_with_padding,
        policy.genvm_start_budget_floor(),
    )
    assert preset["distribution"]["totalMessageFees"] == 55


def test_execution_fee_report_handles_mode1_internal_messages_without_allocations():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(
            execution_budget_per_round=1_000_000,
            total_message_fees=130,
        ),
        num_of_validators=5,
        submitted_value=1_001_230,
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
    )
    policy = StudioFeePolicy(receipt_gas_price=3)
    fee_params_a = _encode_internal_fee_params(leader_timeunits=6)
    fee_params_b = _encode_internal_fee_params(leader_timeunits=7)
    receipt = {
        "genvm_result": {
            "eqBlocksOutputsLength": 10,
            "messageFeesConsumed": 130,
        },
        "pending_transactions": [
            {
                "messageType": "Internal",
                "recipient": "0x2222222222222222222222222222222222222222",
                "data": "0x1234",
                "onAcceptance": True,
                "value": 1,
                "feeParams": fee_params_a,
                "declaredBudget": 60,
                "callKey": "0x" + "12" * 32,
            },
            {
                "message_type": 1,
                "recipient": "0x3333333333333333333333333333333333333333",
                "calldata": base64.b64encode(b"\xab").decode("ascii"),
                "on": "finalized",
                "value": 2,
                "fee_params": fee_params_b,
                "declared_budget": 70,
                "call_key": "0x" + "34" * 32,
            },
        ],
    }

    recorded = record_execution_fee_consumption(accounting, receipt, policy)
    message_reveal = recorded["execution_fee_report"]["messageReveal"]
    expected_messages = [
        (
            1,
            "0x2222222222222222222222222222222222222222",
            1,
            b"\x12\x34",
            True,
            0,
            fee_params_a,
            60,
            b"",
            bytes.fromhex("12" * 32),
            False,
        ),
        (
            1,
            "0x3333333333333333333333333333333333333333",
            2,
            b"\xab",
            False,
            0,
            fee_params_b,
            70,
            b"",
            bytes.fromhex("34" * 32),
            False,
        ),
    ]

    assert message_reveal["messageCount"] == 2
    assert message_reveal["messageBytes"] == len(
        encode([SUBMITTED_MESSAGE_ABI_TYPE], [expected_messages])
    )
    assert message_reveal["estimatedGas"] == policy.estimate_message_reveal_gas(
        message_reveal["messageBytes"],
        2,
    )
    assert message_reveal["messages"] == [
        {
            "messageFeeMode": "mode1",
            "messageType": "Internal",
            "recipient": "0x2222222222222222222222222222222222222222",
            "value": 1,
            "dataBytes": 2,
            "onAcceptance": True,
            "saltNonce": 0,
            "feeParams": "0x" + fee_params_a.hex(),
            "feeParamsDecoded": {
                "leaderTimeunitsAllocation": 6,
                "validatorTimeunitsAllocation": 10,
                "appealRounds": 0,
                "executionBudgetPerRound": 0,
                "rotations": [0],
                "maxPriceGenPerTimeUnit": 1,
                "storageFeeMaxGasPrice": 2**200,
                "receiptFeeMaxGasPrice": 2**200,
            },
            "feeParamsBytes": len(fee_params_a),
            "declaredBudget": 60,
            "allocationSubtree": "0x",
            "allocationSubtreeBytes": 0,
            "callKey": "0x" + "12" * 32,
            "useBalance": False,
        },
        {
            "messageFeeMode": "mode1",
            "messageType": "Internal",
            "recipient": "0x3333333333333333333333333333333333333333",
            "value": 2,
            "dataBytes": 1,
            "onAcceptance": False,
            "saltNonce": 0,
            "feeParams": "0x" + fee_params_b.hex(),
            "feeParamsDecoded": {
                "leaderTimeunitsAllocation": 7,
                "validatorTimeunitsAllocation": 10,
                "appealRounds": 0,
                "executionBudgetPerRound": 0,
                "rotations": [0],
                "maxPriceGenPerTimeUnit": 1,
                "storageFeeMaxGasPrice": 2**200,
                "receiptFeeMaxGasPrice": 2**200,
            },
            "feeParamsBytes": len(fee_params_b),
            "declaredBudget": 70,
            "allocationSubtree": "0x",
            "allocationSubtreeBytes": 0,
            "callKey": "0x" + "34" * 32,
            "useBalance": False,
        },
    ]
    assert recorded["message_fee_consumed"] == 130
    assert recorded["message_fees_recorded_from_receipt"] is True
    assert recorded["reported_message_fees_total"] == 130
    assert recorded["execution_fee_report"]["messageFees"]["reportedTotal"] == 130


def test_execution_fee_report_rejects_underreported_message_fee_total():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=130),
        num_of_validators=5,
        submitted_value=1_230,
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
    )
    fee_params_a = _encode_internal_fee_params(leader_timeunits=6)
    fee_params_b = _encode_internal_fee_params(leader_timeunits=7)
    receipt = {
        "genvm_result": {"messageFeesConsumed": 129},
        "pending_transactions": [
            {
                "messageType": "Internal",
                "recipient": "0x2222222222222222222222222222222222222222",
                "data": "0x1234",
                "onAcceptance": True,
                "value": 1,
                "feeParams": fee_params_a,
                "declaredBudget": 60,
                "callKey": "0x" + "12" * 32,
            },
            {
                "messageType": "Internal",
                "recipient": "0x3333333333333333333333333333333333333333",
                "data": "0xab",
                "onAcceptance": False,
                "value": 2,
                "feeParams": fee_params_b,
                "declaredBudget": 70,
                "callKey": "0x" + "34" * 32,
            },
        ],
    }

    with pytest.raises(MessageFeesReportMismatch):
        record_execution_fee_consumption(accounting, receipt, StudioFeePolicy())


def test_execution_fee_consumption_ignores_messages_from_error_receipt():
    fees_distribution = _fees_distribution(
        execution_budget_per_round=1_000,
        total_message_fees=1_000,
    )
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5),
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
        message_allocations=[
            _allocation(
                message_type=0,
                on_acceptance=False,
                recipient="0x4444444444444444444444444444444444444444",
                call_key="0x" + "78" * 32,
                budget=1_000,
                fee_params=_encode_external_fee_params(
                    gas_limit=100,
                    max_gas_price=10,
                ),
            )
        ],
    )
    receipt = {
        "execution_result": "ERROR",
        "genvm_result": {
            "eq_blocks_outputs_length": 0,
            "data_fees_consumed": [12, 34, 56],
            "messageFeesConsumed": 1_000,
        },
        "pending_transactions": [
            {
                "isEthSend": True,
                "recipient": "0x4444444444444444444444444444444444444444",
                "data": "0xaabbccdd",
                "onAcceptance": False,
                "value": 3,
                "declaredBudget": 0,
                "callKey": "0x" + "78" * 32,
                "gasUsed": 70,
            }
        ],
    }

    recorded = record_execution_fee_consumption(accounting, receipt, StudioFeePolicy())

    assert recorded["message_fee_consumed"] == 0
    assert recorded.get("message_fees_recorded_from_receipt") is None
    assert recorded["allocation_consumed"] == {}
    assert recorded["external_message_fee_reserved"] == 0
    assert "messageReveal" not in recorded["execution_fee_report"]
    assert recorded["genvm_message_fee_consumed"] == 56
    assert recorded["execution_fee_consumed_buckets"] == [12, 0]
    assert recorded["execution_fee_consumed"] == 12
    assert (
        recorded["execution_fee_report"]["chargeableExecution"]["totalExecution"] == 12
    )
    assert recorded["execution_fee_report"]["genvmBuckets"]["totalExecution"] == 46
    assert recorded["execution_fee_report"]["executionMetering"] == {
        "chargeableExecutionFee": 12,
        "genvmReportedExecution": 46,
        "genvmDeltaFromChargeable": 34,
    }


def test_execution_fee_consumption_discards_storage_fee_for_error_receipt():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(execution_budget_per_round=1_000),
        num_of_validators=5,
        submitted_value=2_100,
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
    )
    policy = StudioFeePolicy(
        receipt_gas_price=2,
        intrinsic_gas=10,
        bootloader_overhead=20,
        gas_per_changed_slot=3,
        calldata_gas_per_byte=4,
        fixed_propose_receipt_gas=30,
        fixed_message_reveal_gas=40,
        receipt_wrapper_bytes=0,
    )
    receipt = {
        "execution_result": "FinishedWithError",
        "genvm_result": {
            "eqBlocksOutputsLength": 5,
            "data_fees_consumed": [999, 321],
        },
        "pending_transactions": [
            {
                "messageType": "Internal",
                "recipient": "0x2222222222222222222222222222222222222222",
                "data": "0x1234",
                "onAcceptance": True,
                "value": 0,
                "declaredBudget": 0,
                "callKey": CALL_KEY_WILDCARD,
            }
        ],
    }

    recorded = record_execution_fee_consumption(accounting, receipt, policy)
    report = recorded["execution_fee_report"]
    proposal_fee = report["proposalReceipt"]["fee"]

    assert "messageReveal" not in report
    assert report["totalEstimatedFee"] == proposal_fee
    assert recorded["execution_fee_consumed_buckets"] == [proposal_fee, 0]
    assert recorded["execution_fee_consumed"] == proposal_fee
    assert report["chargeableExecution"]["storage"] == 0
    assert report["genvmBuckets"]["storage"] == 321
    assert report["executionMetering"] == {
        "chargeableExecutionFee": proposal_fee,
        "genvmReportedExecution": 999 + 321,
        "genvmDeltaFromChargeable": 999 + 321 - proposal_fee,
    }


def test_execution_fee_report_handles_external_message_reveal_encoding():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(execution_budget_per_round=1_000_000),
        num_of_validators=5,
        submitted_value=1_001_100,
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
    )
    policy = StudioFeePolicy(receipt_gas_price=5)
    receipt = {
        "genvm_result": {"eq_blocks_outputs_length": 0},
        "pending_transactions": [
            {
                "isEthSend": True,
                "recipient": "0x4444444444444444444444444444444444444444",
                "data": "0xaabbccdd",
                "onAcceptance": False,
                "value": 3,
                "feeParams": _encode_external_fee_params(
                    gas_limit=100,
                    max_gas_price=10,
                ),
                "declaredBudget": 0,
                "callKey": "0x" + "78" * 32,
            }
        ],
    }

    recorded = record_execution_fee_consumption(accounting, receipt, policy)
    message_reveal = recorded["execution_fee_report"]["messageReveal"]
    expected_messages = [
        (
            0,
            "0x4444444444444444444444444444444444444444",
            3,
            b"\xaa\xbb\xcc\xdd",
            False,
            0,
            b"",
            0,
            b"",
            bytes.fromhex("78" * 32),
            False,
        )
    ]

    assert message_reveal["messageBytes"] == len(
        encode([SUBMITTED_MESSAGE_ABI_TYPE], [expected_messages])
    )
    assert message_reveal["messages"] == [
        {
            "messageFeeMode": "external",
            "messageType": "External",
            "recipient": "0x4444444444444444444444444444444444444444",
            "value": 3,
            "dataBytes": 4,
            "onAcceptance": False,
            "saltNonce": 0,
            "feeParams": "0x",
            "feeParamsDecoded": None,
            "feeParamsBytes": 0,
            "declaredBudget": 0,
            "allocationSubtree": "0x",
            "allocationSubtreeBytes": 0,
            "callKey": "0x" + "78" * 32,
            "useBalance": False,
        }
    ]


def test_execution_fee_report_derives_external_call_key_from_calldata_selector():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(execution_budget_per_round=1_000_000),
        num_of_validators=5,
        submitted_value=1_001_100,
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
    )
    receipt = {
        "genvm_result": {"eq_blocks_outputs_length": 0},
        "pending_transactions": [
            {
                "isEthSend": True,
                "recipient": "0x4444444444444444444444444444444444444444",
                "data": "0xaabbccdd0102",
                "onAcceptance": False,
                "value": 3,
                "declaredBudget": 0,
            }
        ],
    }

    recorded = record_execution_fee_consumption(accounting, receipt, StudioFeePolicy())
    message = recorded["execution_fee_report"]["messageReveal"]["messages"][0]

    assert message["callKey"] == _external_selector_call_key(bytes.fromhex("aabbccdd"))


def test_settle_fee_accounting_uses_full_unified_budget_after_early_finish():
    fees_distribution = _fees_distribution(
        appeals=2,
        rotations=[0, 0, 0],
        execution_budget_per_round=100,
    )
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5),
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
    )
    receipt = {"genvm_result": {"data_fees_consumed": [300]}}

    settled, refund = settle_fee_accounting(
        accounting,
        receipt=receipt,
        actual_final_round=0,
        num_of_validators=5,
    )

    assert settled["execution_fee_consumed"] == 300
    assert settled["actual_final_round"] == 0
    # Consensus caps cumulative execution against the complete configured
    # unified reserve (5 leader slots * 100), even though the transaction
    # finalized in round 0. Future-slot capacity remains spendable backing;
    # only its unused residual is reported/refunded.
    assert accounting["execution_budget_total"] == 500
    assert settled["primary_fee_spent"] == 1400
    assert refund == 21900


def test_settle_fee_accounting_uses_actual_round_for_primary_refund():
    fees_distribution = _fees_distribution(appeals=2, rotations=[0, 0, 0])
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5),
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
    )

    settled, refund = settle_fee_accounting(
        accounting,
        actual_final_round=0,
        num_of_validators=5,
    )

    assert settled["primary_fee_spent"] == 1100
    assert settled["actual_final_round"] == 0
    assert refund == 21700


def test_unavailable_initial_committee_charges_no_invented_validator_work():
    fees_distribution = _fees_distribution()
    deposit = required_fee_deposit(fees_distribution, 7)
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=7,
        submitted_value=deposit,
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
    )
    consensus_history = {
        "consensus_results": [
            {
                "consensus_round": "Undetermined",
                "leader_result": None,
                "validator_results": [],
            }
        ]
    }

    settled, refund = settle_fee_accounting(
        accounting,
        actual_final_round=0,
        num_of_validators=7,
        consensus_history=consensus_history,
    )

    assert settled["primary_fee_spent"] == 0
    assert refund == deposit
    assert settled["settlement_rounds"][0]["rule"] == "committee_unavailable"


def test_leader_timeout_settlement_matches_fee_simulator_50_paid_1050_refunded():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(),
        num_of_validators=5,
        submitted_value=1100,
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
    )
    consensus_history = {
        "consensus_results": [
            {"consensus_round": "Leader Timeout"},
        ]
    }

    settled, refund = settle_fee_accounting(
        accounting,
        actual_final_round=0,
        num_of_validators=5,
        consensus_history=consensus_history,
    )

    assert settled["primary_fee_spent"] == 50
    assert refund == 1050
    assert settled["settlement_rounds"] == [
        {
            "round": 0,
            "outcome": "Leader Timeout",
            "rule": "leader_timeout",
            "rotations": 0,
            "timeUnitAmount": 50,
        }
    ]


def test_leader_timeout_half_is_rounded_after_locked_gen_price_scaling():
    policy = StudioFeePolicy(gen_per_time_unit=2)
    fees_distribution = _fees_distribution(
        leader_timeunits=101,
        validator_timeunits=200,
        max_price_gen_per_time_unit=2,
    )
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5, policy),
        user_value=0,
        policy=policy,
    )

    settled, refund = settle_fee_accounting(
        accounting,
        actual_final_round=0,
        num_of_validators=5,
        consensus_history={
            "consensus_results": [{"consensus_round": "Leader Timeout"}]
        },
        policy=policy,
    )

    # Consensus first prices the 101-TU allocation to 202 wei, then pays
    # floor(202 / 2) = 101. Rounding 101 / 2 before pricing would pay 100.
    assert settled["primary_fee_spent"] == 101
    assert settled["settlement_rounds"][0]["timeUnitAmount"] == 101
    assert refund == required_fee_deposit(fees_distribution, 5, policy) - 101


def test_normal_settlement_refunds_non_aligned_validator_allocations():
    leader = "0x1111111111111111111111111111111111111111"
    validator_addresses = [
        leader,
        "0x2222222222222222222222222222222222222222",
        "0x3333333333333333333333333333333333333333",
        "0x4444444444444444444444444444444444444444",
        "0x5555555555555555555555555555555555555555",
    ]
    votes = ["agree", "agree", "agree", "disagree", "disagree"]
    proposal = _history_receipt(mode="leader", address=leader)
    validation = [
        _history_receipt(mode="validator", address=address, vote=vote)
        for address, vote in zip(validator_addresses, votes)
    ]
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(),
        num_of_validators=5,
        submitted_value=1100,
        user_value=0,
        sender=leader,
    )

    settled, refund = settle_fee_accounting(
        accounting,
        actual_final_round=0,
        num_of_validators=5,
        consensus_history={
            "consensus_results": [
                {
                    "consensus_round": "Accepted",
                    "leader_result": [proposal, validation[0]],
                    "validator_results": validation[1:],
                }
            ]
        },
    )

    assert settled["primary_fee_spent"] == 700
    assert settled["settlement_rounds"][0]["timeUnitAmount"] == 700
    assert refund == 400


def test_leader_only_settlement_does_not_charge_unexecuted_validator_seats():
    leader = "0x1111111111111111111111111111111111111111"
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(),
        num_of_validators=5,
        submitted_value=1100,
        user_value=0,
        sender=leader,
    )

    settled, refund = settle_fee_accounting(
        accounting,
        actual_final_round=0,
        num_of_validators=5,
        execution_mode="LEADER_ONLY",
        consensus_history={
            "consensus_results": [
                {
                    "consensus_round": "Accepted",
                    "leader_result": [_history_receipt(mode="leader", address=leader)],
                    "validator_results": [],
                }
            ]
        },
    )

    assert settled["primary_fee_spent"] == 100
    assert settled["settlement_rounds"][0]["timeUnitAmount"] == 100
    assert refund == 1000


def test_settlement_refunds_storage_division_dust_like_consensus():
    leader = "0x1111111111111111111111111111111111111111"
    validator_addresses = [
        leader,
        "0x2222222222222222222222222222222222222222",
        "0x3333333333333333333333333333333333333333",
    ]
    fees_distribution = _fees_distribution(execution_budget_per_round=100)
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5),
        user_value=0,
        sender=leader,
    )
    history = {
        "consensus_results": [
            {
                "consensus_round": "Accepted",
                "leader_result": [
                    _history_receipt(mode="leader", address=leader),
                    _history_receipt(mode="validator", address=leader, vote="agree"),
                ],
                "validator_results": [
                    _history_receipt(mode="validator", address=address, vote="agree")
                    for address in validator_addresses[1:]
                ],
            }
        ]
    }

    settled, refund = settle_fee_accounting(
        accounting,
        receipt={"genvm_result": {"data_fees_consumed": [0, 5]}},
        actual_final_round=0,
        num_of_validators=5,
        consensus_history=history,
    )

    # Consensus pays floor(5 / 3) to each tracked validator and returns the
    # two-wei remainder. Time-unit spend is 100 + 3 * 200 = 700.
    assert settled["storage_fee_recipient_count"] == 3
    assert settled["storage_fee_dust_refunded"] == 2
    assert settled["primary_fee_spent"] == 703
    assert refund == required_fee_deposit(fees_distribution, 5) - 703


def test_settlement_refunds_all_storage_when_zero_value_round_tracks_nobody():
    leader = "0x1111111111111111111111111111111111111111"
    fees_distribution = _fees_distribution(
        leader_timeunits=0,
        validator_timeunits=0,
        execution_budget_per_round=100,
    )
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5),
        user_value=0,
        sender=leader,
    )
    history = {
        "consensus_results": [
            {
                "consensus_round": "Accepted",
                "leader_result": [
                    _history_receipt(mode="leader", address=leader),
                    _history_receipt(mode="validator", address=leader, vote="agree"),
                ],
                "validator_results": [
                    _history_receipt(
                        mode="validator",
                        address="0x2222222222222222222222222222222222222222",
                        vote="agree",
                    )
                ],
            }
        ]
    }

    settled, refund = settle_fee_accounting(
        accounting,
        receipt={"genvm_result": {"data_fees_consumed": [0, 5]}},
        actual_final_round=0,
        num_of_validators=5,
        consensus_history=history,
    )

    # FeesProcessor does not index zero-value rewards or zero penalties, so
    # storage has no recipient denominator and the complete bucket is dust.
    assert settled["storage_fee_recipient_count"] == 0
    assert settled["storage_fee_dust_refunded"] == 5
    assert settled["primary_fee_spent"] == 0
    assert refund == 100


def test_leader_appeal_history_expands_placeholder_and_replay_round_fees():
    fees_distribution = _fees_distribution(appeals=1, rotations=[0, 0])
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5),
        user_value=0,
    )
    accounting = record_appeal_bond(
        accounting,
        amount=2_300,
        appealer="0x9999999999999999999999999999999999999999",
        current_round=0,
        status="UNDETERMINED",
    )
    original_leader = "0x1111111111111111111111111111111111111111"
    replay_leader = "0x2222222222222222222222222222222222222222"
    replay_validators = [
        _history_receipt(
            mode="validator",
            address=f"0x{index:040x}",
            vote="agree",
        )
        for index in range(20, 31)
    ]
    history = {
        "consensus_results": [
            {
                "consensus_round": "Undetermined",
                "leader_result": [
                    _history_receipt(mode="leader", address=original_leader)
                ],
                "validator_results": [],
            },
            {
                "consensus_round": "Leader Appeal Successful",
                "leader_result": [
                    _history_receipt(mode="leader", address=replay_leader)
                ],
                "validator_results": replay_validators,
            },
        ]
    }

    settled, refund = settle_fee_accounting(
        accounting,
        actual_final_round=_infer_final_round(history),
        num_of_validators=5,
        consensus_history=history,
    )

    assert _infer_final_round(history) == 2
    assert settled["primary_fee_spent"] == 5_750
    assert refund == 2_600
    assert [item["rule"] for item in settled["settlement_rounds"]] == [
        "successful_appeal_skip",
        "leader_appeal",
        "leader_appeal_replay",
    ]
    assert settled["settlement_rounds"][2]["timeUnitAmount"] == 2_300


def test_chained_validator_appeal_preserves_empty_even_fee_round():
    history = {
        "consensus_results": [
            {"consensus_round": "Accepted"},
            {"consensus_round": "Validator Appeal Failed"},
            {"consensus_round": "Validator Appeal Successful"},
        ]
    }
    fees_distribution = _fees_distribution(appeals=2, rotations=[0, 0, 0])
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5),
        user_value=0,
    )

    settled, _ = settle_fee_accounting(
        accounting,
        actual_final_round=_infer_final_round(history),
        num_of_validators=5,
        consensus_history=history,
    )

    assert _infer_final_round(history) == 3
    assert settled["settlement_rounds"][2]["rule"] == "empty_round"
    assert settled["settlement_rounds"][2]["timeUnitAmount"] == 0


def test_chained_successful_validator_appeal_vindicates_last_non_appeal_round():
    fees_distribution = _fees_distribution(appeals=2, rotations=[0, 0, 0])
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5),
        user_value=0,
    )
    original_validators = [
        _history_receipt(
            mode="validator",
            address=f"0x{index:040x}",
            vote=vote,
        )
        for index, vote in enumerate(
            ["agree", "agree", "agree", "disagree", "disagree"],
            start=1,
        )
    ]
    failed_appeal_validators = [
        _history_receipt(
            mode="validator",
            address=f"0x{index:040x}",
            vote=vote,
        )
        for index, vote in enumerate(["agree", "agree", "disagree"], start=11)
    ]
    successful_appeal_validators = [
        _history_receipt(
            mode="validator",
            address=f"0x{index:040x}",
            vote=vote,
        )
        for index, vote in enumerate(
            ["disagree", "disagree", "disagree", "disagree", "agree", "agree", "agree"],
            start=21,
        )
    ]
    history = {
        "consensus_results": [
            {
                "consensus_round": "Accepted",
                "leader_result": [
                    _history_receipt(
                        mode="leader",
                        address="0x1111111111111111111111111111111111111111",
                    ),
                    original_validators[0],
                ],
                "validator_results": original_validators[1:],
            },
            {
                "consensus_round": "Validator Appeal Failed",
                "validator_results": failed_appeal_validators,
            },
            {
                "consensus_round": "Validator Appeal Successful",
                "validator_results": successful_appeal_validators,
            },
        ]
    }

    settled, _ = settle_fee_accounting(
        accounting,
        actual_final_round=3,
        num_of_validators=5,
        consensus_history=history,
    )

    # Solidity leaves the first failed-appeal jury payable (3 * 200), keeps
    # the empty logical gap, and scans past both to vindicate the two Disagree
    # voters from the last non-appeal round (4 jury + 2 vindicated) * 200.
    assert settled["settlement_rounds"][1]["rule"] == (
        "validator_appeal_failed_redistribution"
    )
    assert settled["settlement_rounds"][1]["timeUnitAmount"] == 600
    assert settled["settlement_rounds"][2]["rule"] == "empty_round"
    assert settled["settlement_rounds"][3]["timeUnitAmount"] == 1_200


def test_successful_validator_appeal_with_no_majority_does_not_vindicate_original_round():
    fees_distribution = _fees_distribution(appeals=1, rotations=[0, 0])
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5),
        user_value=0,
    )
    original_validators = [
        _history_receipt(
            mode="validator",
            address=f"0x{index:040x}",
            vote=vote,
        )
        for index, vote in enumerate(
            ["agree", "agree", "agree", "disagree", "disagree"],
            start=1,
        )
    ]
    appeal_validators = [
        _history_receipt(
            mode="validator",
            address=f"0x{index:040x}",
            vote=vote,
        )
        for index, vote in enumerate(
            ["agree", "agree", "agree", "disagree", "disagree", "timeout", "timeout"],
            start=11,
        )
    ]
    history = {
        "consensus_results": [
            {
                "consensus_round": "Accepted",
                "leader_result": [
                    _history_receipt(
                        mode="leader",
                        address="0x1111111111111111111111111111111111111111",
                    ),
                    original_validators[0],
                ],
                "validator_results": original_validators[1:],
            },
            {
                "consensus_round": "Validator Appeal Successful",
                "validator_results": appeal_validators,
            },
        ]
    }

    settled, _ = settle_fee_accounting(
        accounting,
        actual_final_round=1,
        num_of_validators=5,
        consensus_history=history,
    )

    # Consensus pays every NoMajority appeal revealer, but vindicates nobody
    # from the overturned round because the appeal established no clear side.
    assert settled["settlement_rounds"][0]["timeUnitAmount"] == 0
    assert settled["settlement_rounds"][1]["timeUnitAmount"] == 1_400


def test_terminal_settlement_uses_frozen_electorate_threshold_not_local_majority():
    fees_distribution = _fees_distribution(appeals=1, rotations=[0, 0])
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5),
        user_value=0,
    )
    accounting["selection_pool_count"] = 22
    # Within-quota appeal funding still covers the ten seats above the
    # scheduled 11-seat replacement: 1,400 bond + 2,000 funding.
    accounting = record_appeal_bond(
        accounting,
        amount=3_400,
        appealer="0x9999999999999999999999999999999999999999",
        current_round=0,
        status="ACCEPTED",
        terminal_committee_upper_bound=21,
    )
    original = [
        _history_receipt(
            mode="validator",
            address=f"0x{index:040x}",
            vote="agree" if index <= 3 else "disagree",
        )
        for index in range(1, 6)
    ]
    jury = [
        _history_receipt(
            mode="validator",
            address=f"0x{index:040x}",
            vote="disagree",
        )
        for index in range(30, 37)
    ]
    terminal = [
        _history_receipt(
            mode="validator",
            address=f"0x{index:040x}",
            vote="agree" if offset < 11 else "disagree",
        )
        for offset, index in enumerate(range(50, 71))
    ]
    history = {
        "consensus_results": [
            {
                "consensus_round": "Accepted",
                "leader_result": [
                    _history_receipt(
                        mode="leader",
                        address="0x1111111111111111111111111111111111111111",
                    ),
                    original[0],
                ],
                "validator_results": original[1:],
            },
            {
                "consensus_round": "Validator Appeal Successful",
                "leader_result": None,
                "validator_results": jury,
            },
            {
                "consensus_round": "Undetermined",
                "leader_result": [
                    _history_receipt(
                        mode="leader",
                        address="0x2222222222222222222222222222222222222222",
                    ),
                    terminal[0],
                ],
                "validator_results": terminal[1:],
            },
        ]
    }

    settled, _ = settle_fee_accounting(
        accounting,
        actual_final_round=2,
        num_of_validators=5,
        consensus_history=history,
    )

    terminal_round = settled["settlement_rounds"][2]
    assert terminal_round["alignmentResult"] == "no_majority"
    assert terminal_round["timeUnitAmount"] == 100 + 21 * 200


def test_failed_validator_appeal_charges_only_revealers_and_refunds_distribution_dust():
    fees_distribution = _fees_distribution(appeals=1, rotations=[0, 0])
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5),
        user_value=0,
    )
    accounting = record_appeal_bond(
        accounting,
        amount=1_400,
        appealer="0x9999999999999999999999999999999999999999",
        current_round=0,
        status="ACCEPTED",
    )
    original_validators = [
        _history_receipt(
            mode="validator",
            address=f"0x{index:040x}",
            vote="agree",
        )
        for index in range(1, 6)
    ]
    appeal_validators = [
        _history_receipt(
            mode="validator",
            address=f"0x{index:040x}",
            vote=vote,
        )
        for index, vote in enumerate(
            ["agree", "agree", "agree", "disagree"],
            start=11,
        )
    ]
    history = {
        "consensus_results": [
            {
                "consensus_round": "Accepted",
                "leader_result": [
                    _history_receipt(
                        mode="leader",
                        address="0x1111111111111111111111111111111111111111",
                    ),
                    original_validators[0],
                ],
                "validator_results": original_validators[1:],
            },
            {
                "consensus_round": "Validator Appeal Failed",
                "validator_results": appeal_validators,
            },
        ]
    }

    settled, refund = settle_fee_accounting(
        accounting,
        actual_final_round=1,
        num_of_validators=5,
        consensus_history=history,
    )

    # Four of seven scheduled appeal seats revealed, so only 4 * 200 of the
    # sender pool is consumed. (800 + 1400) / 3 leaves one wei of bond dust.
    assert settled["settlement_rounds"][1]["timeUnitAmount"] == 800
    assert settled["appeal_bond_sender_refunded"] == 1
    assert settled["appeal_bond_settlements"][0]["bondDistributed"] == 1_399
    assert settled["appeal_bond_settlements"][0]["senderRefund"] == 1
    assert settled["refunds"][0]["appealBond"] == 1
    assert refund >= 1


def test_failed_validator_appeal_dust_uses_priced_validator_allocation():
    policy = StudioFeePolicy(gen_per_time_unit=2)
    fees_distribution = _fees_distribution(
        appeals=1,
        rotations=[0, 0],
        max_price_gen_per_time_unit=2,
    )
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5, policy),
        user_value=0,
        policy=policy,
    )
    accounting = record_appeal_bond(
        accounting,
        amount=2_800,
        appealer="0x9999999999999999999999999999999999999999",
        current_round=0,
        status="ACCEPTED",
        policy=policy,
    )
    appeal_validators = [
        _history_receipt(
            mode="validator",
            address=f"0x{index:040x}",
            vote=vote,
        )
        for index, vote in enumerate(
            ["agree", "agree", "agree", "disagree"],
            start=11,
        )
    ]
    history = {
        "consensus_results": [
            {"consensus_round": "Accepted"},
            {
                "consensus_round": "Validator Appeal Failed",
                "validator_results": appeal_validators,
            },
        ]
    }

    settled, _ = settle_fee_accounting(
        accounting,
        actual_final_round=1,
        num_of_validators=5,
        consensus_history=history,
        policy=policy,
    )

    # (4 revealers * (200 TU * 2 wei/TU) + 2,800 bond) % 3 aligned = 2.
    assert settled["settlement_rounds"][1]["timeUnitAmount"] == 1_600
    assert settled["appeal_bond_sender_refunded"] == 2
    assert settled["appeal_bond_settlements"][0]["bondDistributed"] == 2_798


def test_failed_leader_appeal_dv_replay_withholds_leader_and_refunds_bond_dust():
    fees_distribution = _fees_distribution(appeals=1, rotations=[0, 0])
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5),
        user_value=0,
    )
    accounting = record_appeal_bond(
        accounting,
        amount=2_300,
        appealer="0x9999999999999999999999999999999999999999",
        current_round=0,
        status="UNDETERMINED",
    )
    original_validators = [
        _history_receipt(
            mode="validator",
            address=f"0x{index:040x}",
            vote=vote,
        )
        for index, vote in enumerate(
            ["agree", "agree", "disagree", "disagree", "timeout"],
            start=1,
        )
    ]
    replay_validators = [
        _history_receipt(
            mode="validator",
            address=f"0x{index:040x}",
            vote=("deterministic_violation" if index < 26 else "agree"),
        )
        for index in range(20, 31)
    ]
    history = {
        "consensus_results": [
            {
                "consensus_round": "Undetermined",
                "leader_result": [
                    _history_receipt(
                        mode="leader",
                        address="0x1111111111111111111111111111111111111111",
                    ),
                    original_validators[0],
                ],
                "validator_results": original_validators[1:],
            },
            {
                "consensus_round": "Leader Appeal Failed",
                "leader_result": [
                    _history_receipt(
                        mode="leader",
                        address="0x2222222222222222222222222222222222222222",
                    ),
                    replay_validators[0],
                ],
                "validator_results": replay_validators[1:],
            },
        ]
    }

    settled, _ = settle_fee_accounting(
        accounting,
        actual_final_round=_infer_final_round(history),
        num_of_validators=5,
        consensus_history=history,
    )

    assert settled["settlement_rounds"][2]["rule"] == (
        "deterministic_violation_leader_withheld"
    )
    assert settled["settlement_rounds"][2]["timeUnitAmount"] == 1_200
    assert settled["appeal_bond_sender_refunded"] == 2
    assert settled["appeal_bond_settlements"][0]["bondDistributed"] == 2_298


def test_failed_leader_appeal_bond_recipients_share_storage_with_zero_validator_work():
    leader = f"0x{20:040x}"
    replay_validators = [
        _history_receipt(
            mode="validator",
            address=f"0x{index:040x}",
            vote="agree" if index < 23 else "disagree",
        )
        for index in range(20, 25)
    ]
    history = {
        "consensus_results": [
            {"consensus_round": "Undetermined"},
            {
                "consensus_round": "Leader Appeal Failed",
                "leader_result": [
                    _history_receipt(mode="leader", address=leader),
                    replay_validators[0],
                ],
                "validator_results": replay_validators[1:],
            },
        ]
    }

    recipients = _settlement_storage_recipient_count(
        history,
        [
            {
                "round": 2,
                "outcome": "Leader Appeal Failed",
                "rule": "leader_appeal_replay",
                "rotations": 0,
                "timeUnitAmount": 100,
            }
        ],
        _fees_distribution(leader_timeunits=100, validator_timeunits=0),
        StudioFeePolicy(),
        "NORMAL",
        bond_settlements=[
            {
                "outcomeRound": 2,
                "bondDistributed": 2_298,
            }
        ],
    )

    # Three aligned replay voters receive the bond; the leader overlaps the
    # first of those seats, so the unique on-chain distribution index has 3.
    assert recipients == 3


def test_zero_failed_leader_appeal_bond_does_not_create_storage_recipients():
    leader = f"0x{20:040x}"
    replay_validators = [
        _history_receipt(
            mode="validator",
            address=f"0x{index:040x}",
            vote="agree" if index < 23 else "disagree",
        )
        for index in range(20, 25)
    ]
    history = {
        "consensus_results": [
            {"consensus_round": "Undetermined"},
            {
                "consensus_round": "Leader Appeal Failed",
                "leader_result": [
                    _history_receipt(mode="leader", address=leader),
                    replay_validators[0],
                ],
                "validator_results": replay_validators[1:],
            },
        ]
    }

    recipients = _settlement_storage_recipient_count(
        history,
        [
            {
                "round": 2,
                "outcome": "Leader Appeal Failed",
                "rule": "leader_appeal_replay",
                "rotations": 0,
                "timeUnitAmount": 0,
            }
        ],
        _fees_distribution(leader_timeunits=0, validator_timeunits=0),
        StudioFeePolicy(),
        "NORMAL",
        bond_settlements=[
            {
                "outcomeRound": 2,
                "bondDistributed": 0,
            }
        ],
    )

    assert recipients == 0


def test_zero_failed_timeout_leader_share_does_not_create_storage_recipient():
    leader = "0x2222222222222222222222222222222222222222"
    history = {
        "consensus_results": [
            {"consensus_round": "Leader Timeout"},
            {
                "consensus_round": "Leader Timeout Appeal Failed",
                "leader_result": [
                    _history_receipt(mode="leader", address=leader, timeout=True)
                ],
                "validator_results": [],
            },
        ]
    }
    settlement_rounds = [
        {
            "round": 2,
            "outcome": "Leader Timeout Appeal Failed",
            "rule": "post_timeout_appeal_repeated_timeout",
            "rotations": 0,
            "timeUnitAmount": 0,
        }
    ]
    fees = _fees_distribution(leader_timeunits=0, validator_timeunits=0)

    no_payout = _settlement_storage_recipient_count(
        history,
        settlement_rounds,
        fees,
        StudioFeePolicy(),
        "NORMAL",
        bond_settlements=[{"outcomeRound": 2, "leaderPayout": 0}],
    )
    paid = _settlement_storage_recipient_count(
        history,
        settlement_rounds,
        fees,
        StudioFeePolicy(),
        "NORMAL",
        bond_settlements=[{"outcomeRound": 2, "leaderPayout": 1}],
    )

    assert no_payout == 0
    assert paid == 1


def test_zero_failed_validator_appeal_bond_does_not_create_storage_recipients():
    validators = [
        _history_receipt(
            mode="validator",
            address=f"0x{index:040x}",
            vote="agree" if index < 3 else "disagree",
        )
        for index in range(1, 6)
    ]
    history = {
        "consensus_results": [
            {"consensus_round": "Accepted"},
            {
                "consensus_round": "Validator Appeal Failed",
                "leader_result": validators[0],
                "validator_results": validators[1:],
            },
        ]
    }
    settlement_rounds = [
        {
            "round": 1,
            "outcome": "Validator Appeal Failed",
            "rule": "validator_appeal_failed_redistribution",
            "rotations": 0,
            "timeUnitAmount": 0,
        }
    ]
    fees = _fees_distribution(leader_timeunits=0, validator_timeunits=0)

    no_payout = _settlement_storage_recipient_count(
        history,
        settlement_rounds,
        fees,
        StudioFeePolicy(),
        "NORMAL",
        bond_settlements=[{"outcomeRound": 1, "bondDistributed": 0}],
    )
    paid = _settlement_storage_recipient_count(
        history,
        settlement_rounds,
        fees,
        StudioFeePolicy(),
        "NORMAL",
        bond_settlements=[{"outcomeRound": 1, "bondDistributed": 9}],
    )

    assert no_payout == 0
    assert paid == 3


def test_failed_leader_timeout_appeal_refunds_sender_half_of_bond():
    fees_distribution = _fees_distribution(appeals=1, rotations=[0, 0])
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5),
        user_value=0,
    )
    accounting = record_appeal_bond(
        accounting,
        amount=1_100,
        appealer="0x9999999999999999999999999999999999999999",
        current_round=0,
        status="LEADER_TIMEOUT",
        leader_timeout_live_seats=5,
    )
    history = {
        "consensus_results": [
            {"consensus_round": "Leader Timeout"},
            {
                "consensus_round": "Leader Timeout Appeal Failed",
                "leader_result": [
                    _history_receipt(
                        mode="leader",
                        address="0x2222222222222222222222222222222222222222",
                        timeout=True,
                    )
                ],
                "validator_results": [],
            },
        ]
    }

    settled, refund = settle_fee_accounting(
        accounting,
        actual_final_round=_infer_final_round(history),
        num_of_validators=5,
        consensus_history=history,
    )

    assert settled["primary_fee_spent"] == 50
    assert settled["appeal_bond_sender_refunded"] == 550
    assert refund == 8_850
    assert settled["refunds"][0]["appealBond"] == 550
    assert settled["appeal_bond_settlements"][0] == {
        "bondIndex": 0,
        "appealer": "0x9999999999999999999999999999999999999999",
        "amount": 1_100,
        "round": 0,
        "status": "forfeited",
        "payout": 0,
        "outcomeRound": 2,
        "outcome": "Leader Timeout Appeal Failed",
        "bond_forfeited": 1_100,
        "distribution": "leader-timeout-split",
        "leaderPayout": 550,
        "senderRefund": 550,
    }


def test_fee_alignment_classifies_idle_ballots_as_onchain_timeouts():
    leader = "0x1111111111111111111111111111111111111111"
    addresses = [
        leader,
        "0x2222222222222222222222222222222222222222",
        "0x3333333333333333333333333333333333333333",
        "0x4444444444444444444444444444444444444444",
        "0x5555555555555555555555555555555555555555",
    ]
    votes = ["idle", "idle", "idle", "disagree", "disagree"]
    proposal = _history_receipt(mode="leader", address=leader)
    validation = [
        _history_receipt(mode="validator", address=address, vote=vote)
        for address, vote in zip(addresses, votes)
    ]
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(),
        num_of_validators=5,
        submitted_value=1_100,
        user_value=0,
        sender=leader,
    )

    settled, refund = settle_fee_accounting(
        accounting,
        actual_final_round=0,
        num_of_validators=5,
        consensus_history={
            "consensus_results": [
                {
                    "consensus_round": "Accepted",
                    "leader_result": [proposal, validation[0]],
                    "validator_results": validation[1:],
                }
            ]
        },
    )

    assert settled["primary_fee_spent"] == 700
    assert refund == 400


def test_deterministic_violation_withholds_leader_time_and_receipt_fees():
    policy = StudioFeePolicy(receipt_gas_price=1)
    fees_distribution = _fees_distribution(execution_budget_per_round=1_000_000)
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5, policy),
        user_value=0,
        policy=policy,
    )
    leader = _history_receipt(
        mode="leader",
        address="0x1111111111111111111111111111111111111111",
        eq_outputs_length=64,
    )
    validators = [
        _history_receipt(
            mode="validator",
            address=f"0x{index:040x}",
            vote=vote,
        )
        for index, vote in enumerate(
            [
                "deterministic_violation",
                "deterministic_violation",
                "deterministic_violation",
                "agree",
                "agree",
            ],
            start=2,
        )
    ]
    history = {
        "consensus_results": [
            {
                "consensus_round": "Undetermined",
                "leader_result": [leader, validators[0]],
                "validator_results": validators[1:],
            }
        ]
    }

    settled, _ = settle_fee_accounting(
        accounting,
        receipt=leader,
        actual_final_round=0,
        num_of_validators=5,
        consensus_history=history,
        policy=policy,
    )

    assert settled["settlement_rounds"][0]["rule"] == (
        "deterministic_violation_leader_withheld"
    )
    assert settled["settlement_rounds"][0]["timeUnitAmount"] == 600
    assert settled["execution_fee_consumed"] == 0
    assert settled["historical_execution_attempts"] == [
        {
            "round": 0,
            "attempt": 0,
            "leaderTimeout": False,
            "deterministicViolation": True,
            "receiptFee": 0,
            "receiptGasPrice": 1,
        }
    ]


def test_settlement_accumulates_receipt_fees_for_every_leader_attempt():
    policy = StudioFeePolicy(receipt_gas_price=1)
    fees_distribution = _fees_distribution(
        rotations=[1],
        execution_budget_per_round=1_000_000,
    )
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5, policy),
        user_value=0,
        policy=policy,
    )
    addresses = [f"0x{index:040x}" for index in range(1, 7)]

    def attempt(leader_index: int, eq_outputs_length: int) -> tuple[dict, list[dict]]:
        proposal = _history_receipt(
            mode="leader",
            address=addresses[leader_index],
            eq_outputs_length=eq_outputs_length,
        )
        validators = [
            _history_receipt(
                mode="validator",
                address=address,
                vote="agree",
            )
            for address in addresses[leader_index : leader_index + 5]
        ]
        return proposal, validators

    first_proposal, first_validators = attempt(0, 0)
    final_proposal, final_validators = attempt(1, 64)
    final_proposal["genvm_result"]["data_fees_consumed"] = [0, 37]
    history = {
        "consensus_results": [
            {
                "consensus_round": "Leader Rotation",
                "leader_result": [first_proposal, first_validators[0]],
                "validator_results": first_validators[1:],
            },
            {
                "consensus_round": "Accepted",
                "leader_result": [final_proposal, final_validators[0]],
                "validator_results": final_validators[1:],
            },
        ]
    }

    settled, _ = settle_fee_accounting(
        accounting,
        receipt=final_proposal,
        actual_final_round=0,
        num_of_validators=5,
        consensus_history=history,
        policy=policy,
    )

    expected_receipts = sum(
        policy.estimate_propose_receipt_gas(
            policy.estimate_propose_receipt_bytes(eq_outputs_length)
        )
        for eq_outputs_length in (0, 64)
    )
    assert settled["execution_fee_consumed_buckets"] == [expected_receipts, 37]
    assert settled["execution_fee_consumed"] == expected_receipts + 37
    assert len(settled["historical_execution_attempts"]) == 2


def test_settlement_does_not_fabricate_validator_pay_for_timeout_rotation():
    fees_distribution = _fees_distribution(rotations=[1])
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5),
        user_value=0,
    )
    rotated_out = _history_receipt(
        mode="leader",
        address="0x1111111111111111111111111111111111111111",
    )
    final_leader = _history_receipt(
        mode="leader",
        address="0x2222222222222222222222222222222222222222",
    )
    final_validators = [
        _history_receipt(
            mode="validator",
            address=f"0x{index:040x}",
            vote="agree",
        )
        for index in range(20, 25)
    ]
    history = {
        "consensus_results": [
            {
                "consensus_round": "Leader Rotation",
                "leader_result": [rotated_out],
                "validator_results": [],
            },
            {
                "consensus_round": "Accepted",
                "leader_result": [final_leader, final_validators[0]],
                "validator_results": final_validators[1:],
            },
        ]
    }

    settled, _ = settle_fee_accounting(
        accounting,
        actual_final_round=0,
        num_of_validators=5,
        consensus_history=history,
    )

    # Consensus pays the timed-out, rotated leader 50%, then pays the final
    # normal attempt. The empty prior validator array is not five hidden votes.
    assert settled["settlement_rounds"][0]["timeUnitAmount"] == 50 + 100 + 5 * 200


def test_settlement_preserves_activation_committed_metering_snapshot():
    submission_policy = StudioFeePolicy(receipt_gas_price=2)
    fees_distribution = _fees_distribution(
        rotations=[1],
        execution_budget_per_round=1_000,
    )
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(
            fees_distribution,
            5,
            submission_policy,
        ),
        user_value=0,
        policy=submission_policy,
        allow_low_execution_budget=True,
    )
    activated, should_cancel = activate_fee_accounting(
        accounting,
        StudioFeePolicy(
            receipt_gas_price=2,
            intrinsic_gas=0,
            bootloader_overhead=0,
            gas_per_changed_slot=0,
            calldata_gas_per_byte=0,
            fixed_propose_receipt_gas=11,
            receipt_wrapper_bytes=0,
        ),
    )
    assert should_cancel is False

    first = _history_receipt(
        mode="leader",
        address="0x1111111111111111111111111111111111111111",
    )
    final = _history_receipt(
        mode="leader",
        address="0x2222222222222222222222222222222222222222",
    )
    first_policy = stamp_receipt_execution_policy(
        first,
        activated,
        StudioFeePolicy(
            receipt_gas_price=90,
            intrinsic_gas=0,
            bootloader_overhead=0,
            gas_per_changed_slot=0,
            calldata_gas_per_byte=0,
            fixed_propose_receipt_gas=11,
            receipt_wrapper_bytes=0,
        ),
    )
    final_policy = stamp_receipt_execution_policy(
        final,
        activated,
        StudioFeePolicy(
            receipt_gas_price=99,
            intrinsic_gas=0,
            bootloader_overhead=0,
            gas_per_changed_slot=0,
            calldata_gas_per_byte=0,
            fixed_propose_receipt_gas=17,
            receipt_wrapper_bytes=0,
        ),
    )
    assert first_policy.receipt_gas_price == 2
    assert final_policy.receipt_gas_price == 2
    assert (
        first["genvm_result"][FEE_POLICY_SNAPSHOT_KEY]["fixed_propose_receipt_gas"]
        == 11
    )

    settled, _ = settle_fee_accounting(
        activated,
        receipt=final,
        actual_final_round=0,
        num_of_validators=5,
        consensus_history={
            "consensus_results": [
                {
                    "consensus_round": "Leader Rotation",
                    "leader_result": [first],
                    "validator_results": [],
                },
                {
                    "consensus_round": "Accepted",
                    "leader_result": [final],
                    "validator_results": [],
                },
            ]
        },
        policy=StudioFeePolicy(
            receipt_gas_price=500,
            fixed_propose_receipt_gas=999,
        ),
    )

    assert [
        attempt["receiptFee"] for attempt in settled["historical_execution_attempts"]
    ] == [22, 22]
    assert settled["execution_fee_consumed"] == 44


def test_historical_error_attempt_does_not_charge_message_reveal():
    policy = StudioFeePolicy(
        receipt_gas_price=1,
        intrinsic_gas=0,
        bootloader_overhead=0,
        gas_per_changed_slot=0,
        calldata_gas_per_byte=1,
        fixed_propose_receipt_gas=10,
        fixed_message_reveal_gas=1_000,
        receipt_wrapper_bytes=0,
    )
    fees_distribution = _fees_distribution(execution_budget_per_round=10_000)
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5, policy),
        user_value=0,
        policy=policy,
    )
    failed = _history_receipt(
        mode="leader",
        address="0x1111111111111111111111111111111111111111",
    )
    failed["execution_result"] = "FinishedWithError"
    failed["pending_transactions"] = [
        {
            "messageType": "Internal",
            "recipient": "0x2222222222222222222222222222222222222222",
            "data": "0x1234",
            "onAcceptance": True,
            "value": 0,
            "declaredBudget": 0,
            "callKey": CALL_KEY_WILDCARD,
        }
    ]

    settled, _ = settle_fee_accounting(
        accounting,
        receipt=failed,
        actual_final_round=0,
        num_of_validators=5,
        consensus_history={
            "consensus_results": [
                {
                    "consensus_round": "Undetermined",
                    "leader_result": [failed],
                    "validator_results": [],
                }
            ]
        },
        policy=policy,
    )

    assert settled["historical_execution_attempts"][0]["receiptFee"] == 10
    assert settled["execution_fee_consumed"] == 10


def test_leader_timeout_attempt_consumes_no_receipt_fee():
    policy = StudioFeePolicy(receipt_gas_price=1)
    fees_distribution = _fees_distribution(execution_budget_per_round=1_000_000)
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5, policy),
        user_value=0,
        policy=policy,
    )
    timeout_receipt = _history_receipt(
        mode="leader",
        address="0x1111111111111111111111111111111111111111",
        timeout=True,
    )

    settled, _ = settle_fee_accounting(
        accounting,
        receipt=timeout_receipt,
        actual_final_round=0,
        num_of_validators=5,
        consensus_history={
            "consensus_results": [
                {
                    "consensus_round": "Leader Timeout",
                    "leader_result": [timeout_receipt],
                    "validator_results": [],
                }
            ]
        },
        policy=policy,
    )

    assert settled["execution_fee_consumed"] == 0
    assert settled["historical_execution_attempts"][0]["leaderTimeout"] is True
    assert settled["historical_execution_attempts"][0]["receiptFee"] == 0


def test_cancel_fee_accounting_refunds_unspent_buckets_and_is_idempotent():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=55),
        num_of_validators=5,
        submitted_value=1255,
        user_value=100,
        sender="0x1111111111111111111111111111111111111111",
    )

    canceled, refund = cancel_fee_accounting(accounting)
    canceled_again, second_refund = cancel_fee_accounting(canceled)

    assert refund == 1155
    assert canceled["status"] == "canceled"
    assert canceled["primary_fee_refunded"] == 1100
    assert canceled["message_fee_refunded"] == 55
    assert canceled["total_refunded"] == 1155
    assert canceled["refunds"][0] == {
        "reason": "canceled",
        "primary": 1100,
        "message": 55,
        "appealBond": 0,
        "amount": 1155,
    }
    assert second_refund == 0
    assert canceled_again["total_refunded"] == 1155


def test_consume_message_fees_tracks_bucket_and_allocation_usage():
    fee_params = _encode_internal_fee_params()
    allocation = _allocation(budget=55, fee_params=fee_params)
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=55),
        message_allocations=[allocation],
        num_of_validators=5,
        submitted_value=1155,
        user_value=0,
    )

    updated = consume_message_fees(
        accounting,
        [
            {
                "messageType": 1,
                "recipient": allocation["recipient"],
                "onAcceptance": True,
                "feeParams": fee_params,
                "declaredBudget": 55,
                "callKey": allocation["callKey"],
            }
        ],
    )

    assert updated["message_fee_consumed"] == 55
    assert updated["allocation_consumed"] == {"0": 55}
    settled, refund = settle_fee_accounting(updated)
    assert refund == 0
    assert settled["message_fee_refunded"] == 0


def test_mode1_message_fees_consume_global_bucket_without_allocations():
    fee_params = _encode_internal_fee_params()
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=120),
        num_of_validators=5,
        submitted_value=1220,
        user_value=0,
    )

    updated = consume_message_fees(
        accounting,
        [
            {
                "messageType": 1,
                "recipient": "0x2222222222222222222222222222222222222222",
                "onAcceptance": True,
                "feeParams": fee_params,
                "declaredBudget": 55,
                "callKey": "0x" + "12" * 32,
            },
            {
                "messageType": 1,
                "recipient": "0x3333333333333333333333333333333333333333",
                "onAcceptance": False,
                "feeParams": fee_params,
                "declaredBudget": 60,
                "callKey": "0x" + "34" * 32,
            },
        ],
        reported_total=120,
    )

    assert updated["message_fee_consumed"] == 115
    assert updated["allocation_consumed"] == {}
    assert updated["message_consumption_events"][0] == {
        "consumed": 115,
        "internalConsumed": 115,
        "externalReimbursed": 0,
        "remaining": 5,
    }
    settled, refund = settle_fee_accounting(updated)
    assert refund == 5
    assert settled["message_fee_refunded"] == 5


def test_mode1_message_fees_reject_bucket_overrun_and_underreported_total():
    fee_params = _encode_internal_fee_params()
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=100),
        num_of_validators=5,
        submitted_value=1200,
        user_value=0,
    )

    with pytest.raises(MessageBudgetExceeded):
        consume_message_fees(
            accounting,
            [
                {
                    "messageType": 1,
                    "recipient": "0x2222222222222222222222222222222222222222",
                    "onAcceptance": True,
                    "feeParams": fee_params,
                    "declaredBudget": 55,
                },
                {
                    "messageType": 1,
                    "recipient": "0x3333333333333333333333333333333333333333",
                    "onAcceptance": True,
                    "feeParams": fee_params,
                    "declaredBudget": 55,
                },
            ],
        )

    with pytest.raises(MessageFeesReportMismatch):
        consume_message_fees(
            accounting,
            [
                {
                    "messageType": 1,
                    "recipient": "0x2222222222222222222222222222222222222222",
                    "onAcceptance": True,
                    "feeParams": fee_params,
                    "declaredBudget": 55,
                },
                {
                    "messageType": 1,
                    "recipient": "0x3333333333333333333333333333333333333333",
                    "onAcceptance": True,
                    "feeParams": fee_params,
                    "declaredBudget": 55,
                },
            ],
            reported_total=100,
        )


def test_mode1_message_fees_reject_declared_budget_below_child_minimum():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=100),
        num_of_validators=5,
        submitted_value=1200,
        user_value=0,
    )

    with pytest.raises(MessageDeclaredBudgetInsufficient):
        consume_message_fees(
            accounting,
            [
                {
                    "messageType": 1,
                    "recipient": "0x2222222222222222222222222222222222222222",
                    "onAcceptance": True,
                    "feeParams": _encode_internal_fee_params(),
                    "declaredBudget": 54,
                }
            ],
        )


def test_use_balance_message_validates_floor_without_consuming_sender_bucket():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=0),
        num_of_validators=5,
        submitted_value=1_100,
        user_value=0,
    )

    updated = consume_message_fees(
        accounting,
        [
            {
                "messageType": 1,
                "recipient": "0x2222222222222222222222222222222222222222",
                "onAcceptance": True,
                "feeParams": _encode_internal_fee_params(),
                "declaredBudget": 55,
                "callKey": EMPTY_CALL_KEY,
                "useBalance": True,
            }
        ],
    )

    assert updated["message_fee_budget"] == 0
    assert updated["message_fee_consumed"] == 0
    assert updated["allocation_consumed"] == {}
    assert updated["message_consumption_events"][-1]["internalConsumed"] == 0


def test_mode2_message_fees_use_exact_then_wildcard_allocation_match():
    exact_call_key = "0x" + "12" * 32
    wildcard_call_key = CALL_KEY_WILDCARD
    exact_fee_params = _encode_internal_fee_params(leader_timeunits=6)
    wildcard_fee_params = _encode_internal_fee_params(leader_timeunits=7)
    recipient = "0x2222222222222222222222222222222222222222"
    allocations = [
        _allocation(
            recipient=recipient,
            call_key=exact_call_key,
            budget=60,
            fee_params=exact_fee_params,
        ),
        _allocation(
            recipient=recipient,
            call_key=wildcard_call_key,
            budget=70,
            fee_params=wildcard_fee_params,
        ),
    ]
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=130),
        message_allocations=allocations,
        num_of_validators=5,
        submitted_value=1230,
        user_value=0,
    )

    updated = consume_message_fees(
        accounting,
        [
            {
                "messageType": 1,
                "recipient": recipient,
                "onAcceptance": True,
                "feeParams": exact_fee_params,
                "declaredBudget": 60,
                "callKey": exact_call_key,
            },
            {
                "messageType": 1,
                "recipient": recipient,
                "onAcceptance": True,
                "feeParams": wildcard_fee_params,
                "declaredBudget": 60,
                "callKey": "0x" + "34" * 32,
            },
        ],
    )

    assert updated["message_fee_consumed"] == 120
    assert updated["allocation_consumed"] == {"0": 60, "1": 60}


def test_fill_message_fee_payload_from_allocation_uses_matching_policy_and_subtree():
    fee_params = _encode_internal_fee_params()
    child_fee_params = _encode_internal_fee_params(leader_timeunits=6)
    recipient = "0x2222222222222222222222222222222222222222"
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=111),
        message_allocations=[
            _allocation(
                recipient=recipient,
                budget=111,
                fee_params=fee_params,
            ),
            _allocation(
                parent_index=0,
                recipient="0x3333333333333333333333333333333333333333",
                budget=56,
                fee_params=child_fee_params,
            ),
        ],
        num_of_validators=5,
        submitted_value=1211,
        user_value=0,
    )

    message = fill_message_fee_payload_from_allocation(
        accounting,
        {
            "messageType": 1,
            "recipient": recipient,
            "onAcceptance": True,
            "declaredBudget": 0,
            "callKey": "0x" + "0" * 64,
        },
    )

    assert message["declaredBudget"] == 111
    assert message["feeParams"].startswith("0x")
    assert "allocationSubtree" not in message
    resolved = message["_studioResolvedAllocationSubtree"]
    assert len(resolved) == 2
    assert resolved[0]["parentIndex"] == NODE_ROOT_SENTINEL
    assert resolved[0]["budget"] == 111
    assert resolved[1]["parentIndex"] == 0
    assert resolved[1]["budget"] == 56


def test_flat_array_message_fee_payload_ignores_mismatched_receipt_subtree():
    fee_params = _encode_internal_fee_params()
    child_fee_params = _encode_internal_fee_params(leader_timeunits=6)
    recipient = "0x2222222222222222222222222222222222222222"
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=111),
        message_allocations=[
            _allocation(
                recipient=recipient,
                budget=111,
                fee_params=fee_params,
            ),
            _allocation(
                parent_index=0,
                recipient="0x3333333333333333333333333333333333333333",
                budget=56,
                fee_params=child_fee_params,
            ),
        ],
        num_of_validators=5,
        submitted_value=1211,
        user_value=0,
    )

    supplied = [
        _allocation(
            recipient=recipient,
            budget=111,
            fee_params=fee_params,
        )
    ]
    message = fill_message_fee_payload_from_allocation(
        accounting,
        {
            "messageType": 1,
            "recipient": recipient,
            "onAcceptance": True,
            "declaredBudget": 111,
            "feeParams": fee_params,
            "callKey": "0x" + "0" * 64,
            "allocationSubtree": supplied,
        },
    )

    assert message["allocationSubtree"] == supplied
    assert len(message["_studioResolvedAllocationSubtree"]) == 2


def test_mode2_message_fees_reject_missing_allocation_and_phase_mismatch():
    fee_params = _encode_internal_fee_params()
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=55),
        message_allocations=[
            _allocation(
                recipient="0x2222222222222222222222222222222222222222",
                budget=55,
                fee_params=fee_params,
            )
        ],
        num_of_validators=5,
        submitted_value=1155,
        user_value=0,
    )

    with pytest.raises(MessageNoMatchingAllocation):
        consume_message_fees(
            accounting,
            [
                {
                    "messageType": 1,
                    "recipient": "0x3333333333333333333333333333333333333333",
                    "onAcceptance": True,
                    "feeParams": fee_params,
                    "declaredBudget": 55,
                    "callKey": "0x" + "12" * 32,
                }
            ],
        )

    phase_accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=55),
        message_allocations=[
            _allocation(
                on_acceptance=False,
                budget=55,
                fee_params=fee_params,
            )
        ],
        num_of_validators=5,
        submitted_value=1155,
        user_value=0,
    )
    with pytest.raises(MessageEmissionPhaseMismatch):
        consume_message_fees(
            phase_accounting,
            [
                {
                    "messageType": 1,
                    "recipient": "0x2222222222222222222222222222222222222222",
                    "onAcceptance": True,
                    "feeParams": fee_params,
                    "declaredBudget": 55,
                    "callKey": "0x" + "0" * 32,
                }
            ],
        )


def test_mode2_external_message_fees_require_matching_committed_allocation():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=55),
        message_allocations=[
            _allocation(
                message_type=1,
                budget=55,
                fee_params=_encode_internal_fee_params(),
            )
        ],
        num_of_validators=5,
        submitted_value=1155,
        user_value=0,
    )

    with pytest.raises(MessageNoMatchingAllocation):
        consume_message_fees(
            accounting,
            [
                {
                    "messageType": 0,
                    "recipient": "0x2222222222222222222222222222222222222222",
                    "onAcceptance": False,
                    "declaredBudget": 0,
                    "callKey": "0x" + "0" * 32,
                    "gasUsed": 1_000,
                }
            ],
            policy=StudioFeePolicy(receipt_gas_price=1),
        )


def test_mode2_external_message_fees_reject_on_acceptance_execution_reservation():
    allocation = _allocation(
        message_type=0,
        on_acceptance=False,
        budget=210_000,
        fee_params=_encode_external_fee_params(gas_limit=21_000, max_gas_price=10),
    )
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=210_000),
        message_allocations=[allocation],
        num_of_validators=5,
        submitted_value=211_100,
        user_value=0,
    )

    with pytest.raises(
        ExternalAllocationInvalid, match="ExternalOnAcceptanceNotSupported"
    ):
        consume_message_fees(
            accounting,
            [
                {
                    "messageType": 0,
                    "recipient": allocation["recipient"],
                    "onAcceptance": True,
                    "declaredBudget": 0,
                    "callKey": allocation["callKey"],
                    "gasUsed": 1_000,
                }
            ],
            policy=StudioFeePolicy(receipt_gas_price=7),
        )


def test_external_allocation_rejects_zero_live_execution_price():
    allocation = _allocation(
        message_type=0,
        on_acceptance=False,
        budget=1_000,
        fee_params=_encode_external_fee_params(gas_limit=100, max_gas_price=10),
    )
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=1_000),
        message_allocations=[allocation],
        num_of_validators=5,
        submitted_value=2_100,
        user_value=0,
    )

    with pytest.raises(
        ExternalAllocationInvalid, match="ExternalExecutionPriceUnavailable"
    ):
        consume_message_fees(
            accounting,
            [
                {
                    "messageType": 0,
                    "recipient": allocation["recipient"],
                    "onAcceptance": False,
                    "declaredBudget": 0,
                    "callKey": allocation["callKey"],
                }
            ],
            policy=StudioFeePolicy(receipt_gas_price=0),
        )


def test_consume_external_message_fees_use_exact_then_wildcard_allocation_match():
    recipient = "0x2222222222222222222222222222222222222222"
    exact_call_key = "0x" + "12" * 32
    allocations = [
        _allocation(
            message_type=0,
            on_acceptance=False,
            recipient=recipient,
            call_key=exact_call_key,
            budget=1_000,
            fee_params=_encode_external_fee_params(gas_limit=100, max_gas_price=10),
        ),
        _allocation(
            message_type=0,
            on_acceptance=False,
            recipient=recipient,
            call_key=CALL_KEY_WILDCARD,
            budget=2_000,
            fee_params=_encode_external_fee_params(gas_limit=200, max_gas_price=10),
        ),
    ]
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=3_000),
        message_allocations=allocations,
        num_of_validators=5,
        submitted_value=4_100,
        user_value=0,
    )

    updated = consume_message_fees(
        accounting,
        [
            {
                "messageType": 0,
                "recipient": recipient,
                "onAcceptance": False,
                "declaredBudget": 0,
                "callKey": exact_call_key,
                "gasUsed": 10,
            },
            {
                "messageType": 0,
                "recipient": recipient,
                "onAcceptance": False,
                "declaredBudget": 0,
                "callKey": "0x" + "34" * 32,
                "gasUsed": 20,
            },
        ],
        policy=StudioFeePolicy(receipt_gas_price=7),
    )

    assert updated["allocation_consumed"] == {"0": 700, "1": 1_400}
    assert updated["external_message_fee_reserved"] == 2_100
    assert updated["external_message_fee_reimbursed"] == 210
    assert updated["message_fee_consumed"] == 2_100


def test_external_message_reservation_spills_from_exhausted_exact_to_wildcard():
    recipient = "0x2222222222222222222222222222222222222222"
    exact_call_key = "0x" + "12" * 32
    message = {
        "messageType": 0,
        "recipient": recipient,
        "onAcceptance": False,
        "declaredBudget": 0,
        "callKey": exact_call_key,
        "gasUsed": 10,
    }
    allocations = [
        _allocation(
            message_type=0,
            on_acceptance=False,
            recipient=recipient,
            call_key=exact_call_key,
            budget=1_000,
            fee_params=_encode_external_fee_params(gas_limit=100, max_gas_price=10),
        ),
        _allocation(
            message_type=0,
            on_acceptance=False,
            recipient=recipient,
            call_key=CALL_KEY_WILDCARD,
            budget=1_000,
            fee_params=_encode_external_fee_params(gas_limit=100, max_gas_price=10),
        ),
    ]
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=2_000),
        message_allocations=allocations,
        num_of_validators=5,
        submitted_value=3_100,
        user_value=0,
    )

    revealed = record_reveal_message_fees(
        accounting,
        [message, message],
        policy=StudioFeePolicy(receipt_gas_price=10),
    )

    assert revealed["allocation_consumed"] == {"0": 1_000, "1": 1_000}
    assert [
        event["allocationIndex"] for event in revealed["external_message_events"]
    ] == [
        0,
        1,
    ]

    unwound = unwind_reveal_message_fees(
        revealed,
        [message, message],
    )

    assert unwound["allocation_consumed"] == {}
    assert unwound["external_message_fee_reserved"] == 0
    assert [
        event["allocationIndex"] for event in unwound["external_message_events"]
    ] == [
        0,
        1,
    ]
    assert all(event["unreserved"] for event in unwound["external_message_events"])


def test_consume_message_fees_rejects_internal_message_without_internal_allocation():
    fee_params = _encode_internal_fee_params()
    recipient = "0x2222222222222222222222222222222222222222"
    call_key = "0x" + "12" * 32
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=210_000),
        message_allocations=[
            _allocation(
                message_type=0,
                on_acceptance=False,
                recipient=recipient,
                call_key=call_key,
                budget=210_000,
                fee_params=_encode_external_fee_params(),
            )
        ],
        num_of_validators=5,
        submitted_value=211_100,
        user_value=0,
    )

    with pytest.raises(MessageNoMatchingAllocation):
        consume_message_fees(
            accounting,
            [
                {
                    "messageType": 1,
                    "recipient": recipient,
                    "onAcceptance": True,
                    "feeParams": fee_params,
                    "declaredBudget": 55,
                    "callKey": call_key,
                }
            ],
        )


def test_consume_external_message_fees_reserves_and_reimburses_executor_gas():
    allocation = _allocation(
        message_type=0,
        on_acceptance=False,
        budget=210_000,
        fee_params=_encode_external_fee_params(gas_limit=21_000, max_gas_price=10),
    )
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=210_000),
        message_allocations=[allocation],
        num_of_validators=5,
        submitted_value=211_100,
        user_value=0,
    )

    updated = consume_message_fees(
        accounting,
        [
            {
                "messageType": 0,
                "recipient": allocation["recipient"],
                "onAcceptance": False,
                "declaredBudget": 0,
                "callKey": allocation["callKey"],
                "gasUsed": 1_000,
            }
        ],
        policy=StudioFeePolicy(receipt_gas_price=7),
    )

    assert updated["allocation_consumed"] == {"0": 147_000}
    assert updated["external_message_fee_reserved"] == 147_000
    assert updated["external_message_fee_reimbursed"] == 7_000
    assert updated["external_message_fee_remainder"] == 140_000
    assert updated["message_fee_consumed"] == 147_000

    settled, refund = settle_fee_accounting(updated)
    assert refund == 63_000
    assert settled["message_fee_refunded"] == 63_000


def test_consume_external_message_fees_caps_reimbursement_at_reserved_gas_limit():
    allocation = _allocation(
        message_type=0,
        on_acceptance=False,
        budget=1_000,
        fee_params=_encode_external_fee_params(gas_limit=100, max_gas_price=10),
    )
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=1_000),
        message_allocations=[allocation],
        num_of_validators=5,
        submitted_value=2_100,
        user_value=0,
    )

    updated = consume_message_fees(
        accounting,
        [
            {
                "messageType": 0,
                "recipient": allocation["recipient"],
                "onAcceptance": False,
                "declaredBudget": 0,
                "callKey": allocation["callKey"],
                "gasUsed": 175,
            }
        ],
        policy=StudioFeePolicy(receipt_gas_price=7),
    )

    assert updated["allocation_consumed"] == {"0": 700}
    assert updated["external_message_fee_reserved"] == 700
    assert updated["external_message_fee_reimbursed"] == 700
    assert updated["external_message_fee_remainder"] == 0
    assert updated["message_fee_consumed"] == 700
    assert updated["external_message_events"][0]["gasUsed"] == 175
    assert updated["external_message_events"][0]["gasLimit"] == 100


def test_consume_external_message_fees_locks_allocation_max_gas_price_cap():
    allocation = _allocation(
        message_type=0,
        on_acceptance=False,
        budget=1_000,
        fee_params=_encode_external_fee_params(gas_limit=100, max_gas_price=10),
    )
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=1_000),
        message_allocations=[allocation],
        num_of_validators=5,
        submitted_value=2_100,
        user_value=0,
    )

    updated = consume_message_fees(
        accounting,
        [
            {
                "messageType": 0,
                "recipient": allocation["recipient"],
                "onAcceptance": False,
                "declaredBudget": 0,
                "callKey": allocation["callKey"],
                "gasUsed": 60,
            }
        ],
        policy=StudioFeePolicy(receipt_gas_price=25),
    )

    assert updated["allocation_consumed"] == {"0": 1_000}
    assert updated["external_message_fee_reserved"] == 1_000
    assert updated["external_message_fee_reimbursed"] == 600
    assert updated["external_message_fee_remainder"] == 400
    assert updated["external_message_events"][0]["lockedGasPrice"] == 10


def test_reveal_external_message_reserves_then_execution_reimburses_once():
    allocation = _allocation(
        message_type=0,
        on_acceptance=False,
        budget=1_000,
        fee_params=_encode_external_fee_params(gas_limit=100, max_gas_price=10),
    )
    message = {
        "messageType": 0,
        "recipient": allocation["recipient"],
        "onAcceptance": False,
        "declaredBudget": 0,
        "callKey": allocation["callKey"],
        "gasUsed": 60,
    }
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=1_000),
        message_allocations=[allocation],
        num_of_validators=5,
        submitted_value=2_100,
        user_value=0,
    )

    revealed = record_reveal_message_fees(
        accounting,
        [message],
        policy=StudioFeePolicy(receipt_gas_price=7),
    )

    assert revealed["message_fees_recorded_at_reveal"] is True
    assert revealed["allocation_consumed"] == {"0": 700}
    assert revealed["message_fee_consumed"] == 0
    assert revealed["external_message_fee_reserved"] == 700
    assert revealed["external_message_fee_reimbursed"] == 0
    assert revealed["external_message_fee_remainder"] == 0
    assert revealed["external_message_events"][0]["executionRecorded"] is False

    executed = record_external_message_execution_fees(
        revealed,
        [message],
        policy=StudioFeePolicy(receipt_gas_price=7),
    )
    executed_again = record_external_message_execution_fees(
        executed,
        [message],
        policy=StudioFeePolicy(receipt_gas_price=7),
    )

    assert executed["message_fee_consumed"] == 700
    assert executed["external_message_fee_reserved"] == 700
    assert executed["external_message_fee_reimbursed"] == 420
    assert executed["external_message_fee_remainder"] == 280
    assert executed["external_message_events"][0]["executionRecorded"] is True
    assert executed["external_message_events"][0]["gasUsed"] == 60
    assert executed_again == executed


def test_external_retry_uses_fresh_reservation_after_reveal_unwind():
    allocation = _allocation(
        message_type=0,
        on_acceptance=False,
        budget=1_000,
        fee_params=_encode_external_fee_params(gas_limit=100, max_gas_price=10),
    )
    message = {
        "messageType": 0,
        "recipient": allocation["recipient"],
        "onAcceptance": False,
        "declaredBudget": 0,
        "callKey": allocation["callKey"],
        "gasUsed": 60,
    }
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=1_000),
        message_allocations=[allocation],
        num_of_validators=5,
        submitted_value=2_100,
        user_value=0,
    )
    policy = StudioFeePolicy(receipt_gas_price=7)

    first = record_reveal_message_fees(accounting, [message], policy=policy)
    unwound = unwind_reveal_message_fees(first, [message])
    retry = record_reveal_message_fees(unwound, [message], policy=policy)

    assert first["allocation_consumed"] == {"0": 700}
    assert unwound["allocation_consumed"] == {}
    assert unwound["external_message_events"][0]["unreserved"] is True
    assert retry["allocation_consumed"] == {"0": 700}
    assert retry["external_message_fee_reserved"] == 700
    assert len(retry["external_message_events"]) == 2
    assert retry["external_message_events"][1]["executionRecorded"] is False
    assert not retry["external_message_events"][1].get("unreserved", False)


def test_external_execution_uses_activation_locked_receipt_price():
    allocation = _allocation(
        message_type=0,
        on_acceptance=False,
        budget=1_000,
        fee_params=_encode_external_fee_params(gas_limit=100, max_gas_price=10),
    )
    message = {
        "messageType": 0,
        "recipient": allocation["recipient"],
        "onAcceptance": False,
        "declaredBudget": 0,
        "callKey": allocation["callKey"],
        "gasUsed": 60,
    }
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=1_000),
        message_allocations=[allocation],
        num_of_validators=5,
        submitted_value=2_100,
        user_value=0,
    )
    activated, should_cancel = activate_fee_accounting(
        accounting,
        StudioFeePolicy(receipt_gas_price=7),
    )
    revealed = record_reveal_message_fees(
        activated,
        [message],
        policy=StudioFeePolicy(receipt_gas_price=9),
    )

    executed = record_external_message_execution_fees(
        revealed,
        [message],
        policy=StudioFeePolicy(receipt_gas_price=10),
    )

    assert should_cancel is False
    assert revealed["external_message_events"][0]["lockedGasPrice"] == 7
    assert executed["external_message_fee_reimbursed"] == 420
    assert executed["external_message_fee_remainder"] == 280


def test_external_execution_routes_reimbursement_and_remainder_like_consensus():
    depositor = "0x1111111111111111111111111111111111111111"
    executor = "0x9999999999999999999999999999999999999999"
    allocation = _allocation(
        message_type=0,
        on_acceptance=False,
        budget=1_000,
        fee_params=_encode_external_fee_params(gas_limit=100, max_gas_price=10),
    )
    message = {
        "messageType": 0,
        "recipient": allocation["recipient"],
        "onAcceptance": False,
        "declaredBudget": 0,
        "callKey": allocation["callKey"],
        "gasUsed": 60,
    }
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=1_000),
        message_allocations=[allocation],
        num_of_validators=5,
        submitted_value=2_100,
        user_value=0,
        sender=depositor,
    )
    revealed = record_reveal_message_fees(
        accounting,
        [message],
        policy=StudioFeePolicy(receipt_gas_price=7),
    )

    executed = record_external_message_execution_fees(
        revealed,
        [message],
        policy=StudioFeePolicy(receipt_gas_price=7),
        executor=executor,
    )
    settled, refund = settle_fee_accounting(executed)

    assert executed["message_fee_consumed"] == 700
    assert executed["external_message_fee_settled"] == 700
    assert executed["external_message_fee_payouts"] == [
        {
            "recipient": executor,
            "amount": 420,
            "source": "external-executor-reimbursement",
        },
        {
            "recipient": depositor,
            "amount": 280,
            "source": "external-execution-remainder",
        },
    ]
    assert refund == 300
    assert settled["message_fee_refunded"] == 300


def test_refund_failed_external_message_fee_preserves_spent_executor_gas():
    allocation = _allocation(
        message_type=0,
        on_acceptance=False,
        budget=210_000,
        fee_params=_encode_external_fee_params(gas_limit=21_000, max_gas_price=10),
    )
    message = {
        "messageType": 0,
        "recipient": allocation["recipient"],
        "onAcceptance": False,
        "declaredBudget": 0,
        "callKey": allocation["callKey"],
        "gasUsed": 1_000,
    }
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=210_000),
        message_allocations=[allocation],
        num_of_validators=5,
        submitted_value=211_100,
        user_value=0,
    )
    consumed = consume_message_fees(
        accounting,
        [message],
        policy=StudioFeePolicy(receipt_gas_price=7),
    )

    refunded = refund_failed_external_message_fee(consumed, message)
    refunded_again = refund_failed_external_message_fee(refunded, message)

    assert refunded["allocation_consumed"] == {"0": 147_000}
    assert refunded["message_fee_consumed"] == 147_000
    assert refunded["external_message_fee_reserved"] == 147_000
    assert refunded["external_message_fee_reimbursed"] == 7_000
    assert refunded["external_message_fee_remainder"] == 140_000
    assert refunded["external_message_events"][0]["failureRefunded"] is True
    assert refunded["external_message_refund_events"] == [
        {
            "recipient": allocation["recipient"].lower(),
            "callKey": allocation["callKey"],
            "allocationIndex": 0,
            "reservation": 147_000,
            "reimbursement": 7_000,
            "remainder": 140_000,
            "feeRefunded": 0,
        }
    ]
    assert refunded_again == refunded

    settled, refund = settle_fee_accounting(refunded)
    assert refund == 63_000
    assert settled["message_fee_refunded"] == 63_000


def test_refund_failed_external_message_fee_marks_exact_or_wildcard_match_only():
    recipient = "0x2222222222222222222222222222222222222222"
    exact_call_key = "0x" + "12" * 32
    wildcard_message = {
        "messageType": 0,
        "recipient": recipient,
        "onAcceptance": False,
        "declaredBudget": 0,
        "callKey": "0x" + "34" * 32,
        "gasUsed": 20,
    }
    allocations = [
        _allocation(
            message_type=0,
            on_acceptance=False,
            recipient=recipient,
            call_key=exact_call_key,
            budget=1_000,
            fee_params=_encode_external_fee_params(gas_limit=100, max_gas_price=10),
        ),
        _allocation(
            message_type=0,
            on_acceptance=False,
            recipient=recipient,
            call_key=CALL_KEY_WILDCARD,
            budget=2_000,
            fee_params=_encode_external_fee_params(gas_limit=200, max_gas_price=10),
        ),
    ]
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=3_000),
        message_allocations=allocations,
        num_of_validators=5,
        submitted_value=4_100,
        user_value=0,
    )
    consumed = consume_message_fees(
        accounting,
        [
            {
                "messageType": 0,
                "recipient": recipient,
                "onAcceptance": False,
                "declaredBudget": 0,
                "callKey": exact_call_key,
                "gasUsed": 10,
            },
            wildcard_message,
        ],
        policy=StudioFeePolicy(receipt_gas_price=7),
    )

    refunded = refund_failed_external_message_fee(consumed, wildcard_message)

    assert refunded["allocation_consumed"] == {"0": 700, "1": 1_400}
    assert refunded["message_fee_consumed"] == 2_100
    assert refunded["external_message_fee_reserved"] == 2_100
    assert refunded["external_message_fee_reimbursed"] == 210
    assert refunded["external_message_fee_remainder"] == 1_890
    assert refunded["external_message_events"][0].get("failureRefunded") is None
    assert refunded["external_message_events"][1]["failureRefunded"] is True
    assert refunded["external_message_refund_events"] == [
        {
            "recipient": recipient,
            "callKey": "0x" + "34" * 32,
            "allocationIndex": 1,
            "reservation": 1_400,
            "reimbursement": 140,
            "remainder": 1_260,
            "feeRefunded": 0,
        }
    ]


def test_refund_failed_external_message_fee_is_noop_without_matching_reservation():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=55),
        num_of_validators=5,
        submitted_value=1155,
        user_value=0,
    )

    updated = refund_failed_external_message_fee(
        accounting,
        {
            "messageType": 0,
            "recipient": "0x2222222222222222222222222222222222222222",
            "onAcceptance": False,
            "declaredBudget": 0,
            "callKey": "0x" + "12" * 32,
        },
    )

    assert updated == accounting


def test_unwind_reveal_message_fees_rolls_back_empty_rereveal_before_acceptance():
    fee_params = _encode_internal_fee_params()
    internal_allocation = _allocation(
        message_type=1,
        on_acceptance=True,
        budget=55,
        fee_params=fee_params,
    )
    external_allocation = _allocation(
        message_type=0,
        on_acceptance=False,
        recipient="0x3333333333333333333333333333333333333333",
        call_key="0x" + "34" * 32,
        budget=1_000,
        fee_params=_encode_external_fee_params(gas_limit=100, max_gas_price=10),
    )
    internal_message = {
        "messageType": 1,
        "recipient": internal_allocation["recipient"],
        "onAcceptance": True,
        "feeParams": fee_params,
        "declaredBudget": 55,
        "callKey": internal_allocation["callKey"],
    }
    external_message = {
        "messageType": 0,
        "recipient": external_allocation["recipient"],
        "onAcceptance": False,
        "declaredBudget": 0,
        "callKey": external_allocation["callKey"],
        "gasUsed": 0,
    }
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=1_055),
        message_allocations=[internal_allocation, external_allocation],
        num_of_validators=5,
        submitted_value=2_155,
        user_value=0,
    )
    consumed = record_reveal_message_fees(
        accounting,
        [internal_message, external_message],
        reported_total=55,
        policy=StudioFeePolicy(receipt_gas_price=7),
    )

    unwound = unwind_reveal_message_fees(
        consumed,
        [internal_message, external_message],
    )

    assert consumed["message_fee_consumed"] == 55
    assert consumed["allocation_consumed"] == {"0": 55, "1": 700}
    assert unwound["message_fee_consumed"] == 0
    assert unwound["allocation_consumed"] == {}
    assert unwound["external_message_fee_reserved"] == 0
    assert unwound["external_message_fee_reimbursed"] == 0
    assert unwound["external_message_fee_remainder"] == 0
    assert unwound["external_message_events"][0]["unreserved"] is True
    assert unwound["message_consumption_events"] == []
    assert unwound["message_fee_unwind_events"] == [
        {
            "acceptanceDispatched": False,
            "internalRefunded": 55,
            "externalUnreserved": 700,
            "externalReimbursementRolledBack": 0,
            "externalRemainderRolledBack": 0,
            "remaining": 1_055,
        }
    ]


def test_unwind_reveal_message_fees_preserves_acceptance_consumption_after_dispatch():
    fee_params = _encode_internal_fee_params()
    accepted_message = {
        "messageType": 1,
        "recipient": "0x2222222222222222222222222222222222222222",
        "onAcceptance": True,
        "feeParams": fee_params,
        "declaredBudget": 55,
        "callKey": "0x" + "12" * 32,
    }
    finalized_message = {
        "messageType": 1,
        "recipient": "0x3333333333333333333333333333333333333333",
        "onAcceptance": False,
        "feeParams": fee_params,
        "declaredBudget": 60,
        "callKey": "0x" + "34" * 32,
    }
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=115),
        num_of_validators=5,
        submitted_value=1_215,
        user_value=0,
    )
    consumed = consume_message_fees(
        accounting,
        [accepted_message, finalized_message],
        reported_total=115,
    )

    unwound = unwind_reveal_message_fees(
        consumed,
        [accepted_message, finalized_message],
        acceptance_dispatched=True,
    )

    assert unwound["message_fee_consumed"] == 55
    assert unwound["message_consumption_events"] == []
    assert unwound["message_fee_unwind_events"][0] == {
        "acceptanceDispatched": True,
        "internalRefunded": 60,
        "externalUnreserved": 0,
        "externalReimbursementRolledBack": 0,
        "externalRemainderRolledBack": 0,
        "remaining": 60,
    }

    rerevealed = record_execution_fee_consumption(
        unwound,
        {
            "genvm_result": {"messageFeesConsumed": 60},
            "pending_transactions": [
                {
                    "messageType": "Internal",
                    "recipient": finalized_message["recipient"],
                    "data": "0x",
                    "onAcceptance": False,
                    "value": 0,
                    "feeParams": fee_params,
                    "declaredBudget": 60,
                    "callKey": finalized_message["callKey"],
                }
            ],
        },
        StudioFeePolicy(),
    )

    assert rerevealed["message_fee_consumed"] == 115
    assert rerevealed["reported_message_fees_total"] == 60
    assert rerevealed["execution_fee_report"]["messageFees"]["remaining"] == 0


def test_consume_external_message_fees_rejects_allocation_reservation_overrun():
    allocation = _allocation(
        message_type=0,
        on_acceptance=False,
        budget=210_000,
        fee_params=_encode_external_fee_params(gas_limit=21_000, max_gas_price=10),
    )
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=210_000),
        message_allocations=[allocation],
        num_of_validators=5,
        submitted_value=211_100,
        user_value=0,
    )

    with pytest.raises(MessageBudgetExceeded):
        consume_message_fees(
            accounting,
            [
                {
                    "messageType": 0,
                    "recipient": allocation["recipient"],
                    "onAcceptance": False,
                    "declaredBudget": 0,
                    "callKey": allocation["callKey"],
                },
                {
                    "messageType": 0,
                    "recipient": allocation["recipient"],
                    "onAcceptance": False,
                    "declaredBudget": 0,
                    "callKey": allocation["callKey"],
                },
            ],
            policy=StudioFeePolicy(receipt_gas_price=10),
        )


def test_consume_message_fees_respects_max_messages_per_tx_policy():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=110),
        num_of_validators=5,
        submitted_value=1210,
        user_value=0,
    )

    with pytest.raises(TooManyMessages):
        consume_message_fees(
            accounting,
            [
                {
                    "messageType": 1,
                    "recipient": "0x2222222222222222222222222222222222222222",
                    "onAcceptance": True,
                    "feeParams": _encode_internal_fee_params(),
                    "declaredBudget": 55,
                },
                {
                    "messageType": 1,
                    "recipient": "0x3333333333333333333333333333333333333333",
                    "onAcceptance": True,
                    "feeParams": _encode_internal_fee_params(),
                    "declaredBudget": 55,
                },
            ],
            policy=StudioFeePolicy(max_messages_per_tx=1),
        )


def test_consume_message_fees_enforces_protocol_hard_cap_by_default():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=0),
        num_of_validators=5,
        submitted_value=1_100,
        user_value=0,
    )
    messages = [
        {
            "messageType": 0,
            "recipient": f"0x{index:040x}",
            "onAcceptance": False,
            "declaredBudget": 0,
        }
        for index in range(1, 22)
    ]

    assert len(messages[:20]) == 20
    consume_message_fees(accounting, messages[:20])
    with pytest.raises(TooManyMessages):
        consume_message_fees(accounting, messages)


def test_identical_accepted_message_rereveal_keeps_single_lifetime_charge():
    tx_id = "0x" + ("ab" * 32)
    message = {
        "messageType": 1,
        "recipient": "0x2222222222222222222222222222222222222222",
        "value": 0,
        "data": b"same-call",
        "onAcceptance": True,
        "saltNonce": 0,
        "feeParams": _encode_internal_fee_params(),
        "declaredBudget": 55,
        "callKey": "0x" + ("12" * 32),
        "useBalance": False,
    }
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=55),
        num_of_validators=5,
        submitted_value=1_155,
        user_value=0,
    )

    first = prepare_reveal_message_generation(accounting, tx_id, [message])
    delivered = mark_message_effects_delivered(first, tx_id, [message], "accepted")
    second = prepare_reveal_message_generation(delivered, tx_id, [message])

    assert first["message_fee_consumed"] == 55
    assert delivered["active_message_generation"]["acceptanceDispatched"] is True
    assert second["message_fee_consumed"] == 55
    assert second["active_message_generation"]["novelty"] == [False]
    assert second["message_consumption_events"][-1]["consumed"] == 0
    # The generation ledger is persisted in transaction JSON, so bytes must be
    # encoded rather than leaking a non-serializable Python value into jsonb.
    json.dumps(second["active_message_generation"])


def test_empty_acceptance_phase_remains_repairable_until_helper_acknowledges_it():
    tx_id = "0x" + ("ac" * 32)

    prepared = prepare_reveal_message_generation({}, tx_id, [])
    assert prepared["active_message_generation"]["acceptanceDispatchRequired"] is True
    assert prepared["active_message_generation"]["acceptanceDispatched"] is False

    delivered = mark_message_effects_delivered(prepared, tx_id, [], "accepted")
    assert delivered["active_message_generation"]["acceptanceDispatched"] is True
    assert delivered["message_phase_emitted"]["accepted"] is True


def test_message_occurrence_descriptor_rejects_value_or_fee_drift():
    tx_id = "0x" + ("bc" * 32)
    message = {
        "messageType": 1,
        "recipient": "0x2222222222222222222222222222222222222222",
        "value": 0,
        "data": b"same-call",
        "onAcceptance": True,
        "saltNonce": 0,
        "feeParams": _encode_internal_fee_params(),
        "declaredBudget": 55,
        "callKey": "0x" + ("12" * 32),
        "useBalance": False,
    }
    accounting = mark_message_effects_delivered({}, tx_id, [message], "accepted")
    drifted = {**message, "value": 1}

    with pytest.raises(MessageEffectDescriptorMismatch):
        message_novelty_mask(accounting, tx_id, [drifted])


def test_message_occurrence_ordinals_are_permutation_stable_for_duplicates():
    tx_id = "0x" + ("cd" * 32)

    def message(recipient: str):
        return {
            "messageType": 0,
            "recipient": recipient,
            "value": 0,
            "data": b"selector",
            "onAcceptance": True,
            "saltNonce": 0,
            "feeParams": b"",
            "declaredBudget": 0,
            "callKey": "0x" + ("12" * 32),
            "useBalance": False,
        }

    a = message("0x2222222222222222222222222222222222222222")
    b = message("0x3333333333333333333333333333333333333333")
    accounting = mark_message_effects_delivered({}, tx_id, [a, b, a], "accepted")

    assert message_novelty_mask(accounting, tx_id, [b, a, a, a]) == [
        False,
        False,
        False,
        True,
    ]


def test_receipt_admission_applies_message_count_cap_to_fee_free_messages():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(execution_budget_per_round=1_000_000),
        num_of_validators=5,
        submitted_value=1_001_100,
        user_value=0,
    )
    messages = [
        {
            "is_eth_send": True,
            "address": f"0x{index:040x}",
            "calldata": b"",
            "on": "finalized",
            "value": 0,
        }
        for index in (1, 2)
    ]

    with pytest.raises(TooManyMessages):
        record_execution_fee_consumption(
            accounting,
            {"pending_transactions": messages},
            StudioFeePolicy(max_messages_per_tx=1),
        )


def test_receipt_admission_enforces_protocol_hard_cap_for_fee_free_messages():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(execution_budget_per_round=1_000_000),
        num_of_validators=5,
        submitted_value=1_001_100,
        user_value=0,
    )
    messages = [
        {
            "is_eth_send": True,
            "address": f"0x{index:040x}",
            "calldata": b"",
            "on": "finalized",
            "value": 0,
        }
        for index in range(1, 22)
    ]

    record_execution_fee_consumption(
        accounting,
        {"pending_transactions": messages[:20]},
        StudioFeePolicy(),
    )
    with pytest.raises(TooManyMessages):
        record_execution_fee_consumption(
            accounting,
            {"pending_transactions": messages},
            StudioFeePolicy(),
        )


def test_record_reveal_message_fees_enforces_canonical_message_byte_cap():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=55),
        num_of_validators=5,
        submitted_value=1_155,
        user_value=0,
    )
    message = {
        "messageType": 1,
        "recipient": "0x2222222222222222222222222222222222222222",
        "onAcceptance": True,
        "feeParams": _encode_internal_fee_params(),
        "declaredBudget": 55,
        "useBalance": False,
    }

    with pytest.raises(SubmittedMessagesTooLarge, match="SubmittedMessagesTooLarge"):
        record_reveal_message_fees(
            accounting,
            [message],
            policy=StudioFeePolicy(max_submitted_messages_bytes=1),
        )


def test_consume_message_fees_rejects_allocation_overrun_and_fee_param_mismatch():
    fee_params = _encode_internal_fee_params()
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=55),
        message_allocations=[_allocation(budget=55, fee_params=fee_params)],
        num_of_validators=5,
        submitted_value=1155,
        user_value=0,
    )

    with pytest.raises(MessageBudgetExceeded):
        consume_message_fees(
            accounting,
            [
                {
                    "messageType": 1,
                    "recipient": "0x2222222222222222222222222222222222222222",
                    "onAcceptance": True,
                    "feeParams": fee_params,
                    "declaredBudget": 55,
                },
                {
                    "messageType": 1,
                    "recipient": "0x2222222222222222222222222222222222222222",
                    "onAcceptance": True,
                    "feeParams": fee_params,
                    "declaredBudget": 55,
                },
            ],
        )

    mismatch_accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=56),
        message_allocations=[_allocation(budget=56, fee_params=fee_params)],
        num_of_validators=5,
        submitted_value=1156,
        user_value=0,
    )
    with pytest.raises(MessageFeeParamsMismatch):
        consume_message_fees(
            mismatch_accounting,
            [
                {
                    "messageType": 1,
                    "recipient": "0x2222222222222222222222222222222222222222",
                    "onAcceptance": True,
                    "feeParams": _encode_internal_fee_params(leader_timeunits=6),
                    "declaredBudget": 56,
                }
            ],
        )


def test_record_appeal_bond_validates_minimum_and_keeps_bond_separate():
    fees_distribution = _fees_distribution(appeals=1, rotations=[0, 0])
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5),
        user_value=0,
    )

    assert (
        calculate_min_appeal_bond(
            accounting["fees_distribution"],
            current_round=0,
            status="ACCEPTED",
        )
        == 1400
    )

    with pytest.raises(InvalidAppealBond):
        record_appeal_bond(
            accounting,
            amount=1399,
            appealer="0x1111111111111111111111111111111111111111",
            current_round=0,
            status="ACCEPTED",
        )

    updated = record_appeal_bond(
        accounting,
        amount=1400,
        appealer="0x1111111111111111111111111111111111111111",
        current_round=0,
        status="ACCEPTED",
    )

    assert updated["appeal_bonds_total"] == 1400
    assert updated["paid_fee_value"] == accounting["paid_fee_value"]
    assert updated["primary_fee_budget"] == accounting["primary_fee_budget"]
    assert updated["top_ups"] == accounting["top_ups"]
    assert updated["appeal_bonds"][0]["minimumRequired"] == 1400


def test_settle_fee_accounting_pays_successful_appeal_bond_plus_profit():
    fees_distribution = _fees_distribution(appeals=1, rotations=[0, 0])
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5),
        user_value=0,
    )
    recorded = record_appeal_bond(
        accounting,
        amount=1400,
        appealer="0x1111111111111111111111111111111111111111",
        current_round=0,
        status="ACCEPTED",
    )
    consensus_history = {
        "consensus_results": [
            {"consensus_round": "Accepted"},
            {"consensus_round": "Validator Appeal Successful"},
        ]
    }

    settled, refund = settle_fee_accounting(
        recorded,
        actual_final_round=1,
        num_of_validators=5,
        consensus_history=consensus_history,
    )

    assert refund == 4850
    assert settled["primary_fee_spent"] == 3500
    assert settled["appeal_bonds_payout_total"] == 3500
    assert settled["appeal_profit_spent"] == 2100
    assert settled["appeal_bond_settlements"] == [
        {
            "bondIndex": 0,
            "appealer": "0x1111111111111111111111111111111111111111",
            "amount": 1400,
            "round": 0,
            "status": "successful",
            "payout": 3500,
            "outcomeRound": 1,
            "outcome": "Validator Appeal Successful",
        }
    ]


def test_successful_appeal_refunds_exact_separate_primary_and_overlay_pools():
    sender = "0x2222222222222222222222222222222222222222"
    policy = StudioFeePolicy(gen_per_time_unit=1, time_unit_overlay_bps=1_500)
    fees_distribution = _fees_distribution(
        appeals=1,
        rotations=[0, 0],
        max_price_gen_per_time_unit=1,
    )
    deposit = required_fee_deposit(fees_distribution, 5, policy)
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=deposit,
        user_value=0,
        sender=sender,
        policy=policy,
    )
    recorded = record_appeal_bond(
        accounting,
        amount=1_400,
        appealer="0x1111111111111111111111111111111111111111",
        current_round=0,
        status="ACCEPTED",
    )

    settled, refund = settle_fee_accounting(
        recorded,
        actual_final_round=1,
        num_of_validators=5,
        consensus_history={
            "consensus_results": [
                {"consensus_round": "Accepted"},
                {"consensus_round": "Validator Appeal Successful"},
            ]
        },
    )

    # The successful validator appeal spends 1,400 of time-unit work, its
    # exact 2,100 profit reserve, and only the overlay on the 1,400 work leg.
    assert settled["appeal_profit_spent"] == 2_100
    assert settled["time_unit_overlay_spent"] == 247
    assert settled["primary_fee_spent"] == 3_747
    assert refund == deposit - 3_747
    assert sum(item["amount"] for item in settled["fee_refund_settlements"]) == refund
    assert settled["fee_refund_settlements"] == [
        {
            "recipient": sender,
            "amount": 4_332,
            "source": "primary-fifo",
        },
        {
            "recipient": sender,
            "amount": 1_135,
            "source": "overlay-fifo",
        },
    ]


def test_settle_fee_accounting_clamps_unfunded_appeal_profit_to_principal():
    fees_distribution = _fees_distribution(appeals=1, rotations=[0, 0])
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5),
        user_value=0,
    )
    recorded = record_appeal_bond(
        accounting,
        amount=1_400,
        appealer="0x1111111111111111111111111111111111111111",
        current_round=0,
        status="ACCEPTED",
    )
    # Adversarial legacy state: less fee money remains than the complete
    # 2,100-wei profit leg. Consensus promises no partial profit in this case.
    recorded["primary_fee_budget"] = 2_000
    history = {
        "consensus_results": [
            {"consensus_round": "Accepted"},
            {
                "consensus_round": "Validator Appeal Successful",
                "validator_results": [
                    _history_receipt(
                        mode="validator",
                        address="0x2222222222222222222222222222222222222222",
                        vote="agree",
                    )
                ],
            },
        ]
    }

    settled, refund = settle_fee_accounting(
        recorded,
        actual_final_round=1,
        num_of_validators=5,
        consensus_history=history,
    )

    assert settled["appeal_profit_requested"] == 2_100
    assert settled["appeal_profit_spent"] == 0
    assert settled["appeal_bonds_payout_total"] == 1_400
    assert settled["appeal_bond_settlements"][0]["payout"] == 1_400
    assert settled["appeal_bond_settlements"][0]["profitFunded"] is False
    assert refund == 1_800


def test_settle_fee_accounting_explicitly_forfeits_failed_appeal_bond():
    fees_distribution = _fees_distribution(appeals=1, rotations=[0, 0])
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5),
        user_value=0,
    )
    recorded = record_appeal_bond(
        accounting,
        amount=1400,
        appealer="0x1111111111111111111111111111111111111111",
        current_round=0,
        status="ACCEPTED",
    )
    consensus_history = {
        "consensus_results": [
            {"consensus_round": "Accepted"},
            {"consensus_round": "Validator Appeal Failed"},
        ]
    }

    settled, refund = settle_fee_accounting(
        recorded,
        actual_final_round=1,
        num_of_validators=5,
        consensus_history=consensus_history,
    )

    assert refund == 5850
    assert settled["appeal_bonds_payout_total"] == 0
    assert settled["appeal_bond_settlements"][0]["status"] == "forfeited"
    assert settled["appeal_bond_settlements"][0]["bond_forfeited"] == 1400


def test_cancel_fee_accounting_returns_unadjudicated_appeal_bond():
    fees_distribution = _fees_distribution(appeals=1, rotations=[0, 0])
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5),
        user_value=0,
    )
    recorded = record_appeal_bond(
        accounting,
        amount=1400,
        appealer="0x1111111111111111111111111111111111111111",
        current_round=0,
        status="ACCEPTED",
    )

    canceled, refund = cancel_fee_accounting(recorded)

    assert refund == accounting["primary_fee_budget"]
    assert canceled["appeal_bonds_payout_total"] == 1400
    assert canceled["appeal_bond_settlements"][0]["status"] == "returned"
    assert canceled["appeal_bond_settlements"][0]["payout"] == 1400


def test_cancel_fee_accounting_invalidates_committed_message_allocations():
    fee_params = _encode_internal_fee_params()
    allocation = _allocation(budget=55, fee_params=fee_params)
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=55),
        message_allocations=[allocation],
        num_of_validators=5,
        submitted_value=1_155,
        user_value=0,
    )
    consumed = consume_message_fees(
        accounting,
        [
            {
                "messageType": 1,
                "recipient": allocation["recipient"],
                "onAcceptance": allocation["onAcceptance"],
                "feeParams": fee_params,
                "declaredBudget": 55,
                "callKey": allocation["callKey"],
            }
        ],
    )

    canceled, _ = cancel_fee_accounting(consumed)

    assert canceled["canceled_message_allocations"] == accounting["message_allocations"]
    assert canceled["message_allocations"] == []
    assert canceled["allocation_consumed"] == {}
    assert canceled["message_allocations_invalidated"] is True


def test_abort_unstarted_appeal_restores_accounting_and_returns_typed_charge():
    fees_distribution = _fees_distribution(appeals=0, rotations=[0])
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5),
        user_value=0,
        sender="0x2222222222222222222222222222222222222222",
    )
    appealer = "0x1111111111111111111111111111111111111111"
    charge = calculate_appeal_charge(
        accounting["fees_distribution"],
        current_round=0,
        status="ACCEPTED",
    )
    recorded = record_appeal_bond(
        accounting,
        amount=charge["bond"] + charge["funding"],
        appealer=appealer,
        current_round=0,
        status="ACCEPTED",
    )

    restored, recipient, refund = abort_latest_appeal_admission(
        recorded,
        reason="appeal_committee_unavailable",
    )

    assert recipient == appealer
    assert refund == charge["bond"] + charge["funding"]
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
    ):
        assert restored[key] == accounting[key]
    assert restored["appeal_bonds"] == []
    assert restored["aborted_appeals"][0]["refund"] == refund


def test_top_up_and_submit_appeal_records_only_typed_funding_in_fee_budget():
    fees_distribution = _fees_distribution(
        max_price_gen_per_time_unit=100,
        storage_fee_max_gas_price=80,
        receipt_fee_max_gas_price=60,
    )
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5),
        user_value=0,
    )

    charge = calculate_appeal_charge(
        accounting["fees_distribution"],
        current_round=0,
        status="ACCEPTED",
    )
    updated = record_appeal_bond(
        accounting,
        amount=charge["bond"] + charge["funding"],
        appealer="0x1111111111111111111111111111111111111111",
        current_round=0,
        status="ACCEPTED",
        fees_distribution=_fees_distribution(
            total_message_fees=55,
            max_price_gen_per_time_unit=200,
            storage_fee_max_gas_price=90,
            receipt_fee_max_gas_price=70,
        ),
        top_up_and_submit=True,
    )

    assert updated["paid_fee_value"] == accounting["paid_fee_value"] + charge["funding"]
    assert updated["primary_fee_budget"] == (
        accounting["primary_fee_budget"] + charge["funding"]
    )
    assert updated["top_ups"] == accounting["top_ups"]
    assert updated["message_fee_budget"] == accounting["message_fee_budget"]
    assert updated["fees_distribution"]["appealRounds"] == 1
    assert updated["fees_distribution"]["totalMessageFees"] == 0
    assert updated["fees_distribution"]["maxPriceGenPerTimeUnit"] == 100
    assert updated["fees_distribution"]["storageFeeMaxGasPrice"] == 80
    assert updated["fees_distribution"]["receiptFeeMaxGasPrice"] == 60
    assert updated["appeal_bonds"][0]["topUpAndSubmit"] is True
    assert updated["appeal_bonds"][0]["feesDistributionIgnored"] is True
    assert updated["appeal_bonds"][0]["extendsSchedule"] is True


def test_accepted_appeal_bond_uses_active_timeunit_price():
    bond = calculate_min_appeal_bond(
        _fees_distribution(),
        current_round=0,
        status="ACCEPTED",
        policy=StudioFeePolicy(gen_per_time_unit=10**15),
    )

    assert bond == 1400 * 10**15


def test_leader_timeout_bond_matches_fee_simulator_configured_round_vector():
    bond = calculate_min_appeal_bond(
        _fees_distribution(),
        current_round=0,
        status="LEADER_TIMEOUT",
    )
    bond_with_two_rotations_left = calculate_min_appeal_bond(
        _fees_distribution(),
        current_round=0,
        status="LEADER_TIMEOUT",
        leader_timeout_rotations_left=2,
    )

    assert bond == 100 + 5 * 200
    assert bond_with_two_rotations_left == 3 * (100 + 5 * 200)


def test_all_shared_simulator_consensus_appeal_quote_signatures():
    """Run Studio against the exact oracle campaign used by Consensus #1388."""

    fixture = json.loads(
        (
            Path(__file__).parents[1] / "fixtures" / "fee_simulator_appeal_quotes.json"
        ).read_text()
    )
    assert fixture["schema_version"] == 4
    assert len(fixture["quotes"]) == 27
    status_names = {
        "Accepted": "ACCEPTED",
        "LeaderTimeout": "LEADER_TIMEOUT",
        "Undetermined": "UNDETERMINED",
        "ValidatorsTimeout": "VALIDATORS_TIMEOUT",
    }

    for quote in fixture["quotes"]:
        current_round = int(quote["quote_round_index"])
        rotation_index = current_round // 2 + 1
        rotations = [0] * (rotation_index + 1)
        rotations[rotation_index] = int(quote["rotations_value"])
        distribution = _fees_distribution(
            leader_timeunits=int(quote["leader_unit"]),
            validator_timeunits=int(quote["validator_unit"]),
            appeals=len(rotations) - 1,
            rotations=rotations,
        )

        actual = calculate_min_appeal_bond(
            distribution,
            current_round=current_round,
            status=status_names[quote["source_status"]],
        )

        assert actual == int(quote["expected_bond"]), quote


def test_leader_timeout_bond_uses_the_next_normal_round_schedule():
    fees_distribution = _fees_distribution(appeals=1, rotations=[0, 2])

    assert calculate_min_appeal_bond(
        fees_distribution,
        current_round=0,
        status="LEADER_TIMEOUT",
    ) == 3 * (100 + 5 * 200)


def test_leader_timeout_live_seats_gate_eligibility_not_configured_pricing():
    fees_distribution = _fees_distribution()

    assert (
        calculate_min_appeal_bond(
            fees_distribution,
            current_round=0,
            status="LEADER_TIMEOUT",
            leader_timeout_live_seats=1,
        )
        == 0
    )
    assert (
        calculate_min_appeal_bond(
            fees_distribution,
            current_round=0,
            status="LEADER_TIMEOUT",
            leader_timeout_live_seats=4,
        )
        == 100 + 5 * 200
    )


def test_leader_appeal_bonds_count_the_mandatory_attempt():
    fees_distribution = _fees_distribution(
        appeals=1,
        rotations=[0, 0],
    )

    assert (
        calculate_min_appeal_bond(
            fees_distribution,
            current_round=0,
            status="UNDETERMINED",
        )
        == 100 + 11 * 200
    )
    assert (
        calculate_min_appeal_bond(
            fees_distribution,
            current_round=0,
            status="LEADER_TIMEOUT",
        )
        == 100 + 5 * 200
    )


def test_later_undetermined_appeal_bond_uses_next_normal_rotation_ordinal():
    fees_distribution = _fees_distribution(
        appeals=3,
        rotations=[0, 1, 2, 3],
    )

    # A leader appeal from raw round 2 induces raw round 4, which is normal
    # ordinal 2 and therefore uses rotations[2] (three attempts).
    assert calculate_min_appeal_bond(
        fees_distribution,
        current_round=2,
        status="UNDETERMINED",
    ) == 3 * (100 + 23 * 200)


def test_successful_appeal_return_matches_fee_simulator_exact_integer_math():
    bond = 10**30 + 1

    assert successful_appeal_reward(bond) == bond * 5 // 2
    assert successful_appeal_profit(bond) == bond * 5 // 2 - bond


def test_timeout_appeal_bond_bypasses_stale_max_price_cap():
    policy = StudioFeePolicy(gen_per_time_unit=100)
    fees_distribution = _fees_distribution(
        appeals=2,
        rotations=[1, 1, 1],
        max_price_gen_per_time_unit=10,
    )

    # Submission quoting stays at the user's ceiling even when the live price
    # has already risen; the transaction is canceled only at activation.
    assert calculate_round_fees(fees_distribution, 5, policy=policy) > 0

    bond = calculate_min_appeal_bond(
        fees_distribution,
        current_round=0,
        status="LEADER_TIMEOUT",
        policy=policy,
    )

    assert bond == 220_000


def test_top_up_and_submit_appeal_bypasses_stale_cap_without_rewriting_policy():
    submission_policy = StudioFeePolicy(gen_per_time_unit=1)
    fees_distribution = _fees_distribution(
        appeals=2,
        rotations=[1, 1, 1],
        max_price_gen_per_time_unit=10,
    )
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5, submission_policy),
        user_value=0,
        policy=submission_policy,
    )

    updated = record_appeal_bond(
        accounting,
        amount=2_300,
        appealer="0x1111111111111111111111111111111111111111",
        current_round=0,
        status="LEADER_TIMEOUT",
        fees_distribution=_fees_distribution(max_price_gen_per_time_unit=200),
        top_up_and_submit=True,
    )

    assert updated["primary_fee_budget"] == accounting["primary_fee_budget"]
    assert updated["fees_distribution"]["appealRounds"] == 2
    assert updated["fees_distribution"]["maxPriceGenPerTimeUnit"] == 10
    assert updated["appeal_bonds"][0]["minimumRequired"] == 2_200
    assert updated["appeal_bonds"][0]["surplusRefund"] == 100
    assert updated["appeal_bonds"][0]["feesDistributionIgnored"] is True


def test_top_up_and_submit_appeal_only_bumps_appeal_capacity_and_fee_pot():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(),
        num_of_validators=5,
        submitted_value=1100,
        user_value=0,
    )

    charge = calculate_appeal_charge(
        accounting["fees_distribution"],
        current_round=0,
        status="ACCEPTED",
    )
    updated = record_appeal_bond(
        accounting,
        amount=charge["bond"] + charge["funding"],
        appealer="0x1111111111111111111111111111111111111111",
        current_round=0,
        status="ACCEPTED",
        fees_distribution=_fees_distribution(total_message_fees=55),
        top_up_and_submit=True,
    )

    assert updated["primary_fee_budget"] == 1100 + charge["funding"]
    assert updated["fees_distribution"]["appealRounds"] == 1
    assert updated["fees_distribution"]["totalMessageFees"] == 0
    assert updated["message_fee_budget"] == 0
    assert updated["appeal_bonds"][0]["feesDistributionIgnored"] is True


def test_appeal_admission_keeps_funding_time_overlay_with_locked_gen_price():
    submission_policy = StudioFeePolicy(
        gen_per_time_unit=3,
        time_unit_overlay_bps=500,
    )
    current_overlay_bps = 2_000
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(max_price_gen_per_time_unit=3),
        num_of_validators=5,
        submitted_value=required_fee_deposit(
            _fees_distribution(max_price_gen_per_time_unit=3),
            5,
            submission_policy,
        ),
        user_value=0,
        policy=submission_policy,
    )
    charge = calculate_appeal_charge(
        accounting["fees_distribution"],
        current_round=0,
        status="ACCEPTED",
        policy=submission_policy,
    )

    updated = record_appeal_bond(
        accounting,
        amount=charge["bond"] + charge["funding"],
        appealer="0x1111111111111111111111111111111111111111",
        current_round=0,
        status="ACCEPTED",
        time_unit_overlay_bps=current_overlay_bps,
    )

    breakdown = updated["appeal_bonds"][0]["fundingBreakdown"]
    assert breakdown["taxableWork"] == 3_700 * 3
    assert breakdown["overlay"] == breakdown["taxableWork"] * 500 // 9_500
    assert updated["time_unit_overlay_budget"] == (
        accounting["time_unit_overlay_budget"] + breakdown["overlay"]
    )


def test_top_up_and_submit_appeal_refreshes_recommended_fee_preset():
    policy = StudioFeePolicy(gen_per_time_unit=1, receipt_gas_price=0)
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(execution_budget_per_round=100),
        num_of_validators=5,
        submitted_value=1_200,
        user_value=0,
        policy=policy,
    )
    recorded = record_execution_fee_consumption(
        accounting,
        {"genvm_result": {"data_fees_consumed": [80]}},
        policy,
    )

    assert recorded["recommended_fee_preset"]["distribution"]["appealRounds"] == 0

    charge = calculate_appeal_charge(
        recorded["fees_distribution"],
        current_round=0,
        status="ACCEPTED",
        policy=policy,
    )
    updated = record_appeal_bond(
        recorded,
        amount=charge["bond"] + charge["funding"],
        appealer="0x1111111111111111111111111111111111111111",
        current_round=0,
        status="ACCEPTED",
        top_up_and_submit=True,
        policy=policy,
    )
    preset = updated["recommended_fee_preset"]

    assert updated["fees_distribution"]["appealRounds"] == 1
    assert preset["distribution"]["appealRounds"] == 1
    assert preset["distribution"]["rotations"] == [0, 0]
    assert preset["observed"]["executionFee"] == 80
    assert preset["feeValue"] == required_fee_deposit(
        preset["distribution"],
        5,
        policy,
    )


def test_create_child_fee_accounting_seeds_mode1_bucket_without_child_allocations():
    child_fees, child_accounting = create_child_fee_accounting(
        message={
            "messageType": 1,
            "recipient": "0x3333333333333333333333333333333333333333",
            "value": 7,
            "onAcceptance": True,
            "feeParams": _encode_internal_fee_params(),
            "declaredBudget": 70,
            "callKey": "0x" + "0" * 64,
        },
        parent_fees_distribution=_fees_distribution(
            max_price_gen_per_time_unit=999,
            storage_fee_max_gas_price=888,
            receipt_fee_max_gas_price=777,
        ),
        sender="0x1111111111111111111111111111111111111111",
    )

    assert child_fees["totalMessageFees"] == 15
    assert child_fees["maxPriceGenPerTimeUnit"] == 1
    assert child_fees["storageFeeMaxGasPrice"] == 2**200
    assert child_fees["receiptFeeMaxGasPrice"] == 2**200
    assert child_accounting["paid_fee_value"] == 70
    assert child_accounting["primary_fee_budget"] == 55
    assert child_accounting["message_fee_budget"] == 15
    assert child_accounting["message_allocations"] == []
    assert child_accounting["message_allocations_restricted"] is False
    assert child_accounting["user_value"] == 7

    sealed_child = dict(child_accounting)
    sealed_child["message_allocations_restricted"] = True
    with pytest.raises(MessageAllocationsRestricted):
        apply_fee_top_up(
            sealed_child,
            fees_distribution=_top_up_distribution(total_message_fees=1),
            amount=1,
            perform_fee_checks=False,
        )


def test_committed_child_uses_parent_policy_then_checks_own_activation_floor():
    parent_policy = StudioFeePolicy(
        receipt_gas_price=1,
        intrinsic_gas=0,
        bootloader_overhead=0,
        gas_per_changed_slot=0,
        calldata_gas_per_byte=0,
        fixed_propose_receipt_gas=10,
    )
    fee_params = _encode_internal_fee_params(
        execution_budget_per_round=10,
        receipt_fee_max_gas_price=2,
    )

    _, child_accounting = create_child_fee_accounting(
        message={
            "messageType": 1,
            "recipient": "0x3333333333333333333333333333333333333333",
            "value": 0,
            "onAcceptance": False,
            "feeParams": fee_params,
            "declaredBudget": 65,
            "callKey": EMPTY_CALL_KEY,
        },
        parent_fees_distribution=_fees_distribution(),
        sender="0x1111111111111111111111111111111111111111",
        policy=parent_policy,
    )

    canceled, should_cancel = activate_fee_accounting(
        child_accounting,
        replace(parent_policy, receipt_gas_price=2),
    )

    assert should_cancel is True
    assert canceled["activation_budget_floor_not_met"] == {
        "actual": 10,
        "minimum": 20,
    }


def test_create_child_fee_accounting_prices_primary_at_child_owned_caps():
    policy = StudioFeePolicy(
        gen_per_time_unit=2,
        storage_unit_price=3,
        receipt_gas_price=4,
    )
    fee_params = _encode_internal_fee_params(
        leader_timeunits=5,
        validator_timeunits=10,
        max_price_gen_per_time_unit=2,
        storage_fee_max_gas_price=3,
        receipt_fee_max_gas_price=4,
    )
    child_primary = calculate_round_fees(
        _fees_distribution(
            leader_timeunits=5,
            validator_timeunits=10,
            max_price_gen_per_time_unit=2,
        ),
        5,
        policy=policy,
    )

    child_fees, child_accounting = create_child_fee_accounting(
        message={
            "messageType": 1,
            "recipient": "0x3333333333333333333333333333333333333333",
            "value": 7,
            "onAcceptance": True,
            "feeParams": fee_params,
            "declaredBudget": child_primary,
            "callKey": "0x" + "0" * 64,
        },
        parent_fees_distribution=_fees_distribution(
            max_price_gen_per_time_unit=1,
            storage_fee_max_gas_price=2,
            receipt_fee_max_gas_price=3,
        ),
        sender="0x1111111111111111111111111111111111111111",
        policy=policy,
    )

    assert child_fees["maxPriceGenPerTimeUnit"] == 2
    assert child_fees["storageFeeMaxGasPrice"] == 3
    assert child_fees["receiptFeeMaxGasPrice"] == 4
    assert child_fees["totalMessageFees"] == 0
    assert child_accounting["primary_fee_required"] == child_primary
    assert child_accounting["primary_fee_budget"] == child_primary
    assert child_accounting["paid_fee_value"] == child_primary
    assert child_accounting["policy_snapshot"]["gen_per_time_unit"] == 2
    assert child_accounting["user_value"] == 7


def test_zero_work_internal_child_and_zero_principal_appeal_match_consensus():
    fee_params = _encode_internal_fee_params(
        leader_timeunits=0,
        validator_timeunits=0,
        appeals=1,
        rotations=[0, 0],
        execution_budget_per_round=0,
    )
    child_fees, child_accounting = create_child_fee_accounting(
        message={
            "messageType": 1,
            "recipient": "0x3333333333333333333333333333333333333333",
            "value": 0,
            "onAcceptance": True,
            "feeParams": fee_params,
            "declaredBudget": 0,
            "callKey": "0x" + "0" * 64,
        },
        parent_fees_distribution=_fees_distribution(),
        sender="0x1111111111111111111111111111111111111111",
    )

    assert child_fees["leaderTimeunitsAllocation"] == 0
    assert child_fees["validatorTimeunitsAllocation"] == 0
    assert child_accounting["paid_fee_value"] == 0
    assert child_accounting["primary_fee_required"] == 0

    appealed = record_appeal_bond(
        child_accounting,
        amount=0,
        appealer="0x1111111111111111111111111111111111111111",
        current_round=0,
        status="ACCEPTED",
        available_appeal_validators=7,
    )
    assert appealed["appeal_bonds"][0]["amount"] == 0
    assert appealed["appeal_bonds"][0]["requiredCharge"] == 0


def test_zero_bond_does_not_make_one_seat_leader_timeout_appealable():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(),
        num_of_validators=5,
        submitted_value=1100,
        user_value=0,
    )
    accounting["fees_distribution"]["leaderTimeunitsAllocation"] = 0
    accounting["fees_distribution"]["validatorTimeunitsAllocation"] = 0

    with pytest.raises(InvalidAppealBond):
        record_appeal_bond(
            accounting,
            amount=0,
            appealer="0x1111111111111111111111111111111111111111",
            current_round=0,
            status="LEADER_TIMEOUT",
            leader_timeout_live_seats=1,
        )


def test_create_child_fee_accounting_rejects_budget_below_child_primary_fee():
    with pytest.raises(MessageDeclaredBudgetInsufficient):
        create_child_fee_accounting(
            message={
                "messageType": 1,
                "recipient": "0x3333333333333333333333333333333333333333",
                "value": 0,
                "onAcceptance": True,
                "feeParams": _encode_internal_fee_params(),
                "declaredBudget": 54,
                "callKey": "0x" + "0" * 64,
            },
            parent_fees_distribution=_fees_distribution(),
            sender="0x1111111111111111111111111111111111111111",
        )


def test_create_child_fee_accounting_installs_child_allocation_subtree():
    grandchild_recipient = "0x4444444444444444444444444444444444444444"
    grandchild_call_key = "0x" + "44" * 32
    grandchild_fee_params = _encode_internal_fee_params()
    child_fees, child_accounting = create_child_fee_accounting(
        message={
            "messageType": 1,
            "recipient": "0x3333333333333333333333333333333333333333",
            "value": 0,
            "onAcceptance": True,
            "feeParams": _encode_internal_fee_params(),
            "declaredBudget": 110,
            "callKey": "0x" + "0" * 64,
        },
        parent_fees_distribution=_fees_distribution(),
        message_allocations=[
            _allocation(
                recipient="0x3333333333333333333333333333333333333333",
                call_key=EMPTY_CALL_KEY,
                budget=110,
                fee_params=_encode_internal_fee_params(),
            ),
            _allocation(
                parent_index=0,
                recipient=grandchild_recipient,
                call_key=grandchild_call_key,
                budget=55,
                fee_params=grandchild_fee_params,
            ),
        ],
        sender="0x1111111111111111111111111111111111111111",
    )

    assert child_fees["totalMessageFees"] == 55
    assert child_accounting["message_fee_budget"] == 55
    assert len(child_accounting["message_allocations"]) == 1

    updated = consume_message_fees(
        child_accounting,
        [
            {
                "messageType": 1,
                "recipient": grandchild_recipient,
                "onAcceptance": True,
                "feeParams": grandchild_fee_params,
                "declaredBudget": 55,
                "callKey": grandchild_call_key,
            }
        ],
    )

    assert updated["message_fee_consumed"] == 55
    assert updated["allocation_consumed"] == {"0": 55}


def test_create_child_fee_accounting_strips_leaf_matched_root_subtree():
    fee_params = _encode_internal_fee_params()
    child_fees, child_accounting = create_child_fee_accounting(
        message={
            "messageType": 1,
            "recipient": "0x3333333333333333333333333333333333333333",
            "value": 0,
            "onAcceptance": True,
            "feeParams": fee_params,
            "declaredBudget": 55,
            "callKey": "0x" + "0" * 64,
        },
        parent_fees_distribution=_fees_distribution(),
        message_allocations=[
            _allocation(
                recipient="0x3333333333333333333333333333333333333333",
                call_key=EMPTY_CALL_KEY,
                budget=55,
                fee_params=fee_params,
            )
        ],
        sender="0x1111111111111111111111111111111111111111",
    )

    assert child_fees["totalMessageFees"] == 0
    assert child_accounting["message_fee_budget"] == 0
    assert child_accounting["message_allocations"] == []


def test_create_child_fee_accounting_rejects_phase_mismatched_root_subtree():
    fee_params = _encode_internal_fee_params()
    child_fee_params = _encode_internal_fee_params()

    with pytest.raises(MessageAllocationsNotEqualBudget):
        create_child_fee_accounting(
            message={
                "messageType": 1,
                "recipient": "0x3333333333333333333333333333333333333333",
                "value": 0,
                "onAcceptance": True,
                "feeParams": fee_params,
                "declaredBudget": 110,
                "callKey": "0x" + "0" * 64,
            },
            parent_fees_distribution=_fees_distribution(),
            message_allocations=[
                _allocation(
                    on_acceptance=False,
                    recipient="0x3333333333333333333333333333333333333333",
                    budget=110,
                    fee_params=fee_params,
                ),
                _allocation(
                    parent_index=0,
                    recipient="0x4444444444444444444444444444444444444444",
                    budget=55,
                    fee_params=child_fee_params,
                ),
            ],
            sender="0x1111111111111111111111111111111111111111",
        )


class _MessageDispatchTxProcessor:
    def __init__(self, fee_accounting):
        self.updated_fee_accounting = None
        self.updated_hash = None
        self.fee_accounting = fee_accounting

    def get_genlayer_transaction_count(self, address):
        return 3

    def update_transaction_fee_accounting(self, tx_hash, accounting):
        self.updated_hash = tx_hash
        self.updated_fee_accounting = accounting
        self.fee_accounting = accounting

    def mutate_transaction_fee_accounting(self, tx_hash, mutator, *, commit=True):
        assert commit is False
        updated = mutator(self.fee_accounting)
        self.update_transaction_fee_accounting(tx_hash, updated)
        return updated


def _message_dispatch_context(accounting):
    processor = _MessageDispatchTxProcessor(accounting)
    executor = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    tx = SimpleNamespace(
        hash="0x" + "ab" * 32,
        to_address="0x9999999999999999999999999999999999999999",
        from_address="0x1111111111111111111111111111111111111111",
        origin_address="0x1111111111111111111111111111111111111111",
        data={FEE_ACCOUNTING_KEY: accounting},
        status=None,
    )
    consensus_data = SimpleNamespace(
        leader_receipt=[SimpleNamespace(node_config={"address": executor})]
    )
    return (
        SimpleNamespace(
            transaction=tx,
            transactions_processor=processor,
            consensus_data=consensus_data,
        ),
        processor,
    )


class _MessageValueAccountsManager:
    def __init__(self, balance):
        self.balance = balance
        self.debits = []

    def get_account_balance(self, address):
        return self.balance

    def debit_account_balance(self, address, amount):
        self.debits.append((address, amount))
        if self.balance < amount:
            return False
        self.balance -= amount
        return True


class _MessageValueReservationProcessor:
    def __init__(self, fee_accounting):
        self.fee_accounting = fee_accounting

    def mutate_transaction_fee_accounting(self, tx_hash, mutator, *, commit=True):
        assert commit is False
        self.fee_accounting = mutator(self.fee_accounting)
        return self.fee_accounting


class _ExternalFreezeQuery:
    def __init__(self, *, one_value=None, rows=None):
        self.one_value = one_value
        self.rows = rows or []

    def filter(self, *args):
        return self

    def scalar(self):
        return self.one_value

    def all(self):
        return self.rows


class _ExternalFreezeSession:
    def __init__(self, *, current_order=None, accepted_rows=None):
        self.current_order = current_order
        self.accepted_rows = accepted_rows or []
        self.query_count = 0

    def query(self, *args):
        self.query_count += 1
        if self.query_count == 1:
            return _ExternalFreezeQuery(one_value=self.current_order)
        return _ExternalFreezeQuery(rows=self.accepted_rows)


def _message_value_context(balance, *, session=None):
    address = "0x9999999999999999999999999999999999999999"
    processor = SimpleNamespace()
    if session is not None:
        processor.session = session
    return SimpleNamespace(
        transaction=SimpleNamespace(
            hash="0x" + "cd" * 32,
            to_address=address,
        ),
        transactions_processor=processor,
        accounts_manager=_MessageValueAccountsManager(balance),
    )


def _recorded_message_value_context(balance):
    address = "0x9999999999999999999999999999999999999999"
    accounting = {"paid_fee_value": 0}
    processor = _MessageValueReservationProcessor(accounting)
    return SimpleNamespace(
        transaction=SimpleNamespace(
            hash="0x" + "cd" * 32,
            to_address=address,
            data={FEE_ACCOUNTING_KEY: accounting},
        ),
        transactions_processor=processor,
        accounts_manager=_MessageValueAccountsManager(balance),
    )


def _pending_external_value(value, *, on="accepted", recipient=None):
    return PendingTransaction(
        address=recipient or "0x4444444444444444444444444444444444444444",
        calldata=b"",
        code=None,
        salt_nonce=0,
        on=on,
        value=value,
        is_eth_send=True,
    )


def _pending_internal_value(
    value,
    *,
    on="accepted",
    recipient=None,
    use_balance=False,
    declared_budget=0,
):
    return PendingTransaction(
        address=recipient or "0x5555555555555555555555555555555555555555",
        calldata=b"\x12\x34",
        code=None,
        salt_nonce=0,
        on=on,
        value=value,
        use_balance=use_balance,
        declared_budget=declared_budget,
    )


def _leader_receipt_with_messages(pending_transactions):
    return Receipt(
        result=b"\x00",
        calldata=b"",
        gas_used=0,
        mode=ExecutionMode.LEADER,
        contract_state={"balance": "kept"},
        node_config={"address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
        execution_result=ExecutionResultStatus.SUCCESS,
        pending_transactions=pending_transactions,
        genvm_result={"stdout": ""},
    )


def test_external_message_freeze_rejects_value_above_available_balance():
    context = _message_value_context(balance=4)
    receipt = _leader_receipt_with_messages(
        [
            _pending_external_value(3, on="accepted"),
            _pending_external_value(2, on="finalized"),
            _pending_internal_value(100, on="accepted"),
        ]
    )

    _apply_external_message_freeze_check(context, receipt)

    assert receipt.execution_result == ExecutionResultStatus.ERROR
    assert receipt.pending_transactions == []
    assert receipt.contract_state == {}
    assert receipt.contract_state_hash is None
    assert b"ExternalMessageFreezeExceeded" in receipt.result
    assert receipt.genvm_result["error_code"] == "EXTERNAL_MESSAGE_FREEZE_EXCEEDED"
    assert receipt.genvm_result["external_message_freeze"] == {
        "declaredValue": 5,
        "availableLimit": 4,
        "balance": 4,
        "reservedExternal": 0,
    }


def test_external_message_freeze_counts_prior_finalization_reservations():
    prior_finalization_freeze = 6
    prior_row = SimpleNamespace(
        consensus_data={
            "leader_receipt": [
                {
                    "execution_result": ExecutionResultStatus.SUCCESS.value,
                    "pending_transactions": [
                        {
                            "is_eth_send": True,
                            "on": "finalized",
                            "value": prior_finalization_freeze,
                        },
                        {
                            "is_eth_send": True,
                            "on": "accepted",
                            "value": 4,
                        },
                        {
                            "messageType": "1",
                            "onAcceptance": False,
                            "value": 100,
                        },
                    ],
                }
            ]
        }
    )
    context = _message_value_context(
        balance=10,
        session=_ExternalFreezeSession(accepted_rows=[prior_row]),
    )
    receipt = _leader_receipt_with_messages([_pending_external_value(5, on="accepted")])

    _apply_external_message_freeze_check(context, receipt)

    assert receipt.execution_result == ExecutionResultStatus.ERROR
    assert receipt.genvm_result["external_message_freeze"] == {
        "declaredValue": 5,
        "availableLimit": 4,
        "balance": 10,
        "reservedExternal": prior_finalization_freeze,
    }


def test_message_freeze_counts_external_and_use_balance_obligations_together():
    context = _message_value_context(balance=10)
    receipt = _leader_receipt_with_messages(
        [
            _pending_external_value(2, on="accepted"),
            _pending_internal_value(
                2,
                on="finalized",
                use_balance=True,
                declared_budget=7,
            ),
        ]
    )

    _apply_external_message_freeze_check(context, receipt)

    assert receipt.execution_result == ExecutionResultStatus.ERROR
    assert receipt.pending_transactions == []
    assert b"ContractMessageFreezeExceeded" in receipt.result
    assert receipt.genvm_result["error_code"] == "CONTRACT_MESSAGE_FREEZE_EXCEEDED"
    assert receipt.genvm_result["external_message_freeze"] == {
        "declaredValue": 11,
        "availableLimit": 10,
        "balance": 10,
        "reservedExternal": 0,
        "externalValue": 2,
        "useBalanceFee": 7,
        "useBalanceValue": 2,
    }


def test_message_freeze_counts_prior_use_balance_finalization_reservations():
    prior_row = SimpleNamespace(
        consensus_data={
            "leader_receipt": [
                {
                    "execution_result": ExecutionResultStatus.SUCCESS.value,
                    "pending_transactions": [
                        {
                            "messageType": 1,
                            "onAcceptance": False,
                            "value": 2,
                            "declaredBudget": 5,
                            "useBalance": True,
                        }
                    ],
                }
            ]
        }
    )
    context = _message_value_context(
        balance=10,
        session=_ExternalFreezeSession(accepted_rows=[prior_row]),
    )
    receipt = _leader_receipt_with_messages(
        [
            _pending_internal_value(
                0,
                use_balance=True,
                declared_budget=4,
            )
        ]
    )

    _apply_external_message_freeze_check(context, receipt)

    assert receipt.execution_result == ExecutionResultStatus.ERROR
    assert receipt.genvm_result["external_message_freeze"]["reservedExternal"] == 7
    assert receipt.genvm_result["external_message_freeze"]["availableLimit"] == 3


def test_message_value_withdrawal_reserves_finalized_external_value_before_internal():
    context = _message_value_context(balance=20)
    accepted_external = _pending_external_value(7, on="accepted")
    accepted_internal = _pending_internal_value(5, on="accepted")
    finalized_external = _pending_external_value(14, on="finalized")

    adjusted = _apply_message_value_withdrawals_for_phase(
        context,
        [accepted_external, accepted_internal, finalized_external],
        "accepted",
    )

    assert adjusted[0] is accepted_external
    assert adjusted[0].value == 7
    assert adjusted[1].address == accepted_internal.address
    assert adjusted[1].value == 0
    assert adjusted[2] is finalized_external
    assert context.accounts_manager.balance == 13
    assert context.accounts_manager.debits == [
        (context.transaction.to_address, 7),
    ]


def test_message_value_withdrawal_drops_unbacked_external_value():
    context = _message_value_context(balance=3)
    accepted_external = _pending_external_value(5, on="accepted")
    accepted_internal = _pending_internal_value(2, on="accepted")

    adjusted = _apply_message_value_withdrawals_for_phase(
        context,
        [accepted_external, accepted_internal],
        "accepted",
    )

    assert adjusted == [accepted_internal]
    assert context.accounts_manager.balance == 1
    assert context.accounts_manager.debits == [
        (context.transaction.to_address, 5),
        (context.transaction.to_address, 2),
    ]


def test_message_value_reservation_is_replayed_without_a_second_debit():
    context = _recorded_message_value_context(balance=9)
    message = _pending_external_value(5)

    first = _apply_message_value_withdrawals_for_phase(
        context,
        [message],
        "accepted",
    )
    second = _apply_message_value_withdrawals_for_phase(
        context,
        [message],
        "accepted",
    )

    assert first == [message]
    assert second == [message]
    assert context.accounts_manager.balance == 4
    assert context.accounts_manager.debits == [
        (context.transaction.to_address, 5),
    ]
    assert (
        len(context.transactions_processor.fee_accounting["message_value_effects"]) == 1
    )


def test_unbacked_message_value_outcome_is_stable_across_worker_retry():
    context = _recorded_message_value_context(balance=3)
    message = _pending_external_value(5)

    first = _apply_message_value_withdrawals_for_phase(
        context,
        [message],
        "accepted",
    )
    second = _apply_message_value_withdrawals_for_phase(
        context,
        [message],
        "accepted",
    )

    assert first == []
    assert second == []
    assert context.accounts_manager.balance == 3
    assert context.accounts_manager.debits == [
        (context.transaction.to_address, 5),
    ]


def test_message_value_withdrawal_debits_use_balance_fee_and_value_from_ghost():
    context = _message_value_context(balance=20)
    use_balance_child = _pending_internal_value(
        3,
        use_balance=True,
        declared_budget=7,
    )
    ordinary_child = _pending_internal_value(4)

    adjusted = _apply_message_value_withdrawals_for_phase(
        context,
        [use_balance_child, ordinary_child],
        "accepted",
    )

    assert adjusted == [use_balance_child, ordinary_child]
    assert context.accounts_manager.balance == 6
    assert context.accounts_manager.debits == [
        (context.transaction.to_address, 10),
        (context.transaction.to_address, 4),
    ]


def test_message_value_withdrawal_drops_short_use_balance_child():
    context = _message_value_context(balance=9)
    use_balance_child = _pending_internal_value(
        3,
        use_balance=True,
        declared_budget=7,
    )

    adjusted = _apply_message_value_withdrawals_for_phase(
        context,
        [use_balance_child],
        "accepted",
    )

    assert adjusted == []
    assert context.accounts_manager.balance == 9
    assert context.accounts_manager.debits == []


def test_message_dispatch_creates_mode1_child_fee_accounting_from_pending_metadata(
    monkeypatch,
):
    monkeypatch.setenv("GENLAYER_STUDIO_GEN_PER_TIME_UNIT", "1")
    monkeypatch.setenv("GENLAYER_STUDIO_STORAGE_UNIT_PRICE", "0")
    monkeypatch.setenv("GENLAYER_STUDIO_RECEIPT_GAS_PRICE", "0")
    monkeypatch.setenv("GENLAYER_STUDIO_MIN_PROPOSE_TIMEUNITS", "1")
    monkeypatch.setenv("GENLAYER_STUDIO_MIN_COMMIT_TIMEUNITS", "1")
    policy = StudioFeePolicy.from_env()
    fee_params = _encode_internal_fee_params()
    child_budget = 64
    fees_distribution = _env_fees_distribution(total_message_fees=child_budget)
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5, policy),
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
        policy=policy,
    )
    context, processor = _message_dispatch_context(accounting)
    pending = PendingTransaction(
        address="0x2222222222222222222222222222222222222222",
        calldata=b"\x12\x34",
        code=None,
        salt_nonce=0,
        on="accepted",
        value=7,
        fee_params=fee_params,
        declared_budget=child_budget,
        call_key="0x" + "12" * 32,
    )

    internal_messages, inserts = _get_messages_data(context, [pending], "accepted")

    assert len(inserts) == 1
    recipient, data, tx_type, nonce, value, effect_identity, effect_payload = inserts[0]
    assert recipient == pending.address
    assert tx_type == TransactionType.RUN_CONTRACT.value
    assert nonce == 3
    assert value == 7
    assert effect_identity.startswith("0x")
    assert effect_payload["recipient"] == pending.address
    assert data["calldata"] == b"\x12\x34"
    assert data["fee_value"] == child_budget
    assert data["user_value"] == 7
    assert data["fees_distribution"]["totalMessageFees"] == 0
    assert data[FEE_ACCOUNTING_KEY]["source"] == "internal_message"
    assert data[FEE_ACCOUNTING_KEY]["paid_fee_value"] == child_budget
    assert data[FEE_ACCOUNTING_KEY]["message_fee_budget"] == 0
    assert data["message_allocations_count"] == 0

    assert len(internal_messages) == 1
    serialized_child = json.loads(internal_messages[0]["data"])
    assert serialized_child["fee_value"] == child_budget
    assert serialized_child["user_value"] == 7
    assert serialized_child["calldata"] == base64.b64encode(b"\x12\x34").decode("utf-8")
    assert processor.updated_hash == context.transaction.hash
    assert processor.updated_fee_accounting["message_fee_consumed"] == child_budget
    assert processor.updated_fee_accounting["allocation_consumed"] == {}
    assert processor.updated_fee_accounting["message_consumption_events"][-1] == {
        "consumed": child_budget,
        "internalConsumed": child_budget,
        "externalReimbursed": 0,
        "remaining": 0,
    }


def test_internal_deployment_descriptor_stays_zero_address_and_carries_salt():
    context, _ = _message_dispatch_context({})
    pending = PendingTransaction(
        address="0x",
        calldata=b"\x12\x34",
        code=b"contract source",
        salt_nonce=42,
        on="accepted",
        value=0,
    )

    internal_messages, inserts = _get_messages_data(context, [pending], "accepted")

    zero = "0x0000000000000000000000000000000000000000"
    assert inserts[0][0] == zero
    assert inserts[0][1]["contract_address"] == zero
    assert internal_messages[0]["recipient"] == zero
    assert internal_messages[0]["saltNonce"] == 42
    assert pending.address == "0x"


def test_flat_array_message_dispatch_ignores_bad_receipt_subtree_and_inherits_parent(
    monkeypatch,
):
    monkeypatch.setenv("GENLAYER_STUDIO_GEN_PER_TIME_UNIT", "1")
    monkeypatch.setenv("GENLAYER_STUDIO_STORAGE_UNIT_PRICE", "0")
    monkeypatch.setenv("GENLAYER_STUDIO_RECEIPT_GAS_PRICE", "0")
    monkeypatch.setenv("GENLAYER_STUDIO_MIN_PROPOSE_TIMEUNITS", "1")
    monkeypatch.setenv("GENLAYER_STUDIO_MIN_COMMIT_TIMEUNITS", "1")
    policy = StudioFeePolicy.from_env()
    root_fee_params = _encode_internal_fee_params(leader_timeunits=6)
    child_fee_params = _encode_internal_fee_params(leader_timeunits=7)
    recipient = "0x2222222222222222222222222222222222222222"
    child_recipient = "0x3333333333333333333333333333333333333333"
    root_call_key = "0x" + "12" * 32
    child_call_key = "0x" + "34" * 32
    child_budget = 67
    root_budget = 132
    fees_distribution = _env_fees_distribution(total_message_fees=root_budget)
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        message_allocations=[
            _allocation(
                recipient=recipient,
                call_key=root_call_key,
                budget=root_budget,
                fee_params=root_fee_params,
            ),
            _allocation(
                parent_index=0,
                recipient=child_recipient,
                call_key=child_call_key,
                budget=child_budget,
                fee_params=child_fee_params,
            ),
        ],
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5, policy),
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
        policy=policy,
    )
    context, processor = _message_dispatch_context(accounting)
    pending = PendingTransaction(
        address=recipient,
        calldata=b"\xaa\xbb",
        code=None,
        salt_nonce=0,
        on="accepted",
        value=0,
        fee_params=b"",
        declared_budget=0,
        call_key=root_call_key,
        allocation_subtree=[
            _allocation(
                recipient=recipient,
                call_key=root_call_key,
                budget=root_budget,
                fee_params=root_fee_params,
            )
        ],
    )

    _, inserts = _get_messages_data(context, [pending], "accepted")

    child_data = inserts[0][1]
    child_accounting = child_data[FEE_ACCOUNTING_KEY]
    assert child_data["fee_value"] == root_budget
    assert child_data["fees_distribution"]["leaderTimeunitsAllocation"] == 6
    assert child_data["fees_distribution"]["totalMessageFees"] == child_budget
    assert child_data["message_allocations_count"] == 1
    assert child_accounting["message_fee_budget"] == child_budget
    assert child_accounting["message_allocations"] == [
        {
            "messageType": 1,
            "onAcceptance": True,
            "parentIndex": NODE_ROOT_SENTINEL,
            "recipient": child_recipient,
            "callKey": child_call_key,
            "budget": child_budget,
            "feeParams": "0x" + child_fee_params.hex(),
        }
    ]
    assert processor.updated_fee_accounting["message_fee_consumed"] == root_budget
    assert processor.updated_fee_accounting["allocation_consumed"] == {"0": root_budget}


def test_message_dispatch_records_revealed_external_message_execution_fees(
    monkeypatch,
):
    monkeypatch.setenv("GENLAYER_STUDIO_GEN_PER_TIME_UNIT", "1")
    monkeypatch.setenv("GENLAYER_STUDIO_STORAGE_UNIT_PRICE", "0")
    monkeypatch.setenv("GENLAYER_STUDIO_RECEIPT_GAS_PRICE", "7")
    policy = StudioFeePolicy.from_env()
    recipient = "0x4444444444444444444444444444444444444444"
    calldata = b"\xaa\xbb\xcc\xdd\x01\x02"
    call_key = _external_selector_call_key(bytes.fromhex("aabbccdd"))
    fee_params = _encode_external_fee_params(gas_limit=100, max_gas_price=10)
    allocation = _allocation(
        message_type=0,
        on_acceptance=False,
        recipient=recipient,
        call_key=call_key,
        budget=1_000,
        fee_params=fee_params,
    )
    fees_distribution = _env_fees_distribution(total_message_fees=1_000)
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        message_allocations=[allocation],
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5, policy),
        user_value=0,
        sender="0x1111111111111111111111111111111111111111",
        policy=policy,
    )
    revealed = record_reveal_message_fees(
        accounting,
        [
            {
                "messageType": 0,
                "recipient": recipient,
                "onAcceptance": False,
                "declaredBudget": 0,
                "callKey": call_key,
                "gasUsed": 0,
            }
        ],
        policy=policy,
    )
    context, processor = _message_dispatch_context(revealed)
    pending = PendingTransaction(
        address=recipient,
        calldata=calldata,
        code=None,
        salt_nonce=0,
        on="finalized",
        value=0,
        is_eth_send=True,
        call_key=call_key,
        gas_used=60,
    )

    internal_messages, inserts = _get_messages_data(context, [pending], "finalized")

    assert len(inserts) == 1
    assert inserts[0][:5] == [recipient, {}, TransactionType.SEND.value, 3, 0]
    assert inserts[0][5].startswith("0x")
    assert internal_messages == []
    updated = processor.updated_fee_accounting
    assert updated["message_fees_recorded_at_reveal"] is True
    assert updated["allocation_consumed"] == {"0": 700}
    assert updated["external_message_fee_reserved"] == 700
    assert updated["external_message_fee_reimbursed"] == 420
    assert updated["external_message_fee_remainder"] == 280
    assert updated["message_fee_consumed"] == 700
    assert updated["external_message_events"][0]["executionRecorded"] is True
    assert updated["external_message_events"][0]["gasUsed"] == 60
    assert updated["external_message_fee_payouts"] == [
        {
            "recipient": "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
            "amount": 420,
            "source": "external-executor-reimbursement",
        },
        {
            "recipient": "0x1111111111111111111111111111111111111111",
            "amount": 280,
            "source": "external-execution-remainder",
        },
    ]


def test_apply_fee_top_up_extends_budgets():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(),
        num_of_validators=5,
        submitted_value=1100,
        user_value=0,
    )

    updated = apply_fee_top_up(
        accounting,
        fees_distribution=_top_up_distribution(total_message_fees=55),
        amount=1155,
        sender="0x1111111111111111111111111111111111111111",
        perform_fee_checks=False,
    )

    assert updated["paid_fee_value"] == 2255
    assert updated["primary_fee_budget"] == 2200
    assert updated["message_fee_budget"] == 55


def test_apply_fee_top_up_rejects_zero_value_like_consensus():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(),
        num_of_validators=5,
        submitted_value=1_100,
        user_value=0,
    )

    with pytest.raises(InsufficientFees):
        apply_fee_top_up(
            accounting,
            fees_distribution=_top_up_distribution(),
            amount=0,
        )


def test_apply_fee_top_up_carves_cumulative_overlay_reserve():
    policy = StudioFeePolicy(gen_per_time_unit=1, time_unit_overlay_bps=1_000)
    fees_distribution = _fees_distribution(max_price_gen_per_time_unit=1)
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5, policy),
        user_value=0,
        policy=policy,
    )

    updated = apply_fee_top_up(
        accounting,
        fees_distribution=_top_up_distribution(),
        amount=1_000,
        perform_fee_checks=False,
        policy=policy,
    )

    assert accounting["time_unit_overlay_budget"] == 122
    assert updated["time_unit_overlay_budget"] == 222


def test_settlement_refunds_top_up_contributors_fifo():
    original = "0x1111111111111111111111111111111111111111"
    rescuer = "0x2222222222222222222222222222222222222222"
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(),
        num_of_validators=5,
        submitted_value=1_100,
        user_value=0,
        sender=original,
    )
    topped_up = apply_fee_top_up(
        accounting,
        fees_distribution=_top_up_distribution(),
        amount=500,
        sender=rescuer,
        perform_fee_checks=False,
    )

    settled, refund = settle_fee_accounting(
        topped_up,
        actual_final_round=0,
        num_of_validators=5,
        policy=StudioFeePolicy(),
    )

    assert refund == 500
    assert settled["contributions"] == [
        {
            "depositor": original,
            "amount": 1_100,
            "primary": 1_100,
            "overlay": 0,
            "message": 0,
        },
        {
            "depositor": rescuer,
            "amount": 500,
            "primary": 500,
            "overlay": 0,
            "message": 0,
        },
    ]
    assert settled["fee_refund_settlements"] == [
        {"recipient": rescuer, "amount": 500, "source": "primary-fifo"}
    ]


def test_legacy_contribution_segment_is_not_partially_retyped_by_top_up():
    sender = "0x1111111111111111111111111111111111111111"
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(),
        num_of_validators=5,
        submitted_value=1_100,
        user_value=0,
        sender=sender,
    )
    accounting["version"] = 1
    accounting["contributions"] = [{"depositor": sender, "amount": 1_100}]

    topped_up = apply_fee_top_up(
        accounting,
        fees_distribution=_top_up_distribution(),
        amount=500,
        sender=sender,
        perform_fee_checks=False,
    )
    settled, refund = settle_fee_accounting(
        topped_up,
        actual_final_round=0,
        num_of_validators=5,
        policy=StudioFeePolicy(),
    )

    assert len(topped_up["contributions"]) == 2
    assert "primary" not in topped_up["contributions"][0]
    assert refund == 500
    assert settled["fee_refund_settlements"] == [
        {"recipient": sender, "amount": 500, "source": "fifo"}
    ]


def test_settlement_keeps_primary_and_message_contributor_pools_separate():
    primary_funder = "0x1111111111111111111111111111111111111111"
    message_funder = "0x2222222222222222222222222222222222222222"
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(),
        num_of_validators=5,
        submitted_value=1_500,
        user_value=0,
        sender=primary_funder,
    )
    topped_up = apply_fee_top_up(
        accounting,
        fees_distribution=_top_up_distribution(total_message_fees=500),
        amount=500,
        sender=message_funder,
        perform_fee_checks=False,
    )
    consumed = consume_message_fees(
        topped_up,
        [
            {
                "messageType": 1,
                "recipient": "0x3333333333333333333333333333333333333333",
                "onAcceptance": False,
                "feeParams": _encode_internal_fee_params(),
                "declaredBudget": 500,
                "callKey": EMPTY_CALL_KEY,
            }
        ],
    )

    settled, refund = settle_fee_accounting(
        consumed,
        actual_final_round=0,
        num_of_validators=5,
        policy=StudioFeePolicy(),
    )

    assert refund == 400
    assert settled["fee_refund_settlements"] == [
        {
            "recipient": primary_funder,
            "amount": 400,
            "source": "primary-fifo",
        }
    ]


def test_cancellation_returns_overlay_and_primary_to_each_contributor():
    policy = StudioFeePolicy(gen_per_time_unit=1, time_unit_overlay_bps=1_000)
    original = "0x1111111111111111111111111111111111111111"
    rescuer = "0x2222222222222222222222222222222222222222"
    fees_distribution = _fees_distribution(max_price_gen_per_time_unit=1)
    initial = required_fee_deposit(fees_distribution, 5, policy)
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=initial,
        user_value=0,
        sender=original,
        policy=policy,
    )
    topped_up = apply_fee_top_up(
        accounting,
        fees_distribution=_top_up_distribution(),
        amount=1_000,
        sender=rescuer,
        perform_fee_checks=False,
        policy=policy,
    )

    canceled, refund = cancel_fee_accounting(topped_up)
    by_recipient: dict[str, int] = {}
    for settlement in canceled["fee_refund_settlements"]:
        by_recipient[settlement["recipient"]] = (
            by_recipient.get(settlement["recipient"], 0) + settlement["amount"]
        )

    assert refund == initial + 1_000
    assert by_recipient == {original: initial, rescuer: 1_000}


def test_contribution_segment_cap_rejects_unattributed_top_up_atomically():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(),
        num_of_validators=5,
        submitted_value=1_100,
        user_value=0,
        sender="0x0000000000000000000000000000000000000001",
    )
    for index in range(2, MAX_CONTRIBUTION_SEGMENTS + 1):
        accounting = apply_fee_top_up(
            accounting,
            fees_distribution=_top_up_distribution(),
            amount=1,
            sender=f"0x{index:040x}",
            perform_fee_checks=False,
        )

    before = accounting["paid_fee_value"]
    with pytest.raises(ContributionSegmentsFull):
        apply_fee_top_up(
            accounting,
            fees_distribution=_top_up_distribution(),
            amount=1,
            sender="0xffffffffffffffffffffffffffffffffffffffff",
            perform_fee_checks=False,
        )

    assert len(accounting["contributions"]) == MAX_CONTRIBUTION_SEGMENTS
    assert accounting["paid_fee_value"] == before


def test_third_party_top_up_cannot_raise_original_depositor_price_caps():
    owner = "0x1111111111111111111111111111111111111111"
    third_party = "0x2222222222222222222222222222222222222222"
    fees_distribution = _fees_distribution(
        max_price_gen_per_time_unit=10,
        storage_fee_max_gas_price=10,
        receipt_fee_max_gas_price=10,
    )
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5),
        user_value=0,
        sender=owner,
    )
    raised_caps = _top_up_distribution(
        max_price_gen_per_time_unit=20,
        storage_fee_max_gas_price=20,
        receipt_fee_max_gas_price=20,
    )

    third_party_update = apply_fee_top_up(
        accounting,
        fees_distribution=raised_caps,
        amount=1,
        sender=third_party,
    )
    owner_update = apply_fee_top_up(
        accounting,
        fees_distribution=raised_caps,
        amount=11_000,
        sender=owner,
    )

    assert third_party_update["fees_distribution"]["maxPriceGenPerTimeUnit"] == 10
    assert third_party_update["fees_distribution"]["storageFeeMaxGasPrice"] == 10
    assert third_party_update["fees_distribution"]["receiptFeeMaxGasPrice"] == 10
    assert owner_update["fees_distribution"]["maxPriceGenPerTimeUnit"] == 20
    assert owner_update["fees_distribution"]["storageFeeMaxGasPrice"] == 20
    assert owner_update["fees_distribution"]["receiptFeeMaxGasPrice"] == 20


def test_submission_ignores_caller_supplied_execution_consumed():
    fees_distribution = _fees_distribution(execution_consumed=999)

    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5),
        user_value=0,
    )

    assert accounting["fees_distribution"]["executionConsumed"] == 0
    assert accounting["execution_fee_consumed"] == 0
    assert accounting["top_ups"][0]["feesDistribution"]["executionConsumed"] == 0


def test_apply_fee_top_up_rejects_schedule_extension_after_activation():
    initial_distribution = _fees_distribution(appeals=1, rotations=[0, 0])
    incoming_distribution = _fees_distribution(
        leader_timeunits=999,
        validator_timeunits=888,
        appeals=3,
        rotations=[2, 2, 2, 2],
    )
    accounting = create_fee_accounting(
        fees_distribution=initial_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(initial_distribution, 5),
        user_value=0,
    )

    with pytest.raises(TopUpCannotExtendSchedule):
        apply_fee_top_up(
            accounting,
            fees_distribution=incoming_distribution,
            amount=calculate_round_fees(incoming_distribution, 5),
        )


def test_apply_fee_top_up_rejects_underfunded_message_bucket():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(),
        num_of_validators=5,
        submitted_value=1100,
        user_value=0,
    )

    with pytest.raises(InsufficientFees):
        apply_fee_top_up(
            accounting,
            fees_distribution=_top_up_distribution(
                total_message_fees=55,
            ),
            amount=54,
        )


def test_apply_fee_top_up_rejects_underfunded_primary_delta():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(),
        num_of_validators=5,
        submitted_value=1100,
        user_value=0,
    )

    with pytest.raises(InsufficientFees):
        apply_fee_top_up(
            accounting,
            fees_distribution=_top_up_distribution(execution_budget_per_round=1100),
            amount=1099,
        )


def test_apply_fee_top_up_adds_message_bucket_without_mutating_allocations():
    fee_params = _encode_internal_fee_params()
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=55),
        message_allocations=[_allocation(budget=55, fee_params=fee_params)],
        num_of_validators=5,
        submitted_value=1155,
        user_value=0,
    )

    updated = apply_fee_top_up(
        accounting,
        fees_distribution=_top_up_distribution(
            total_message_fees=25,
        ),
        amount=25,
    )

    assert updated["paid_fee_value"] == 1180
    assert updated["primary_fee_budget"] == 1100
    assert updated["message_fee_budget"] == 80
    assert updated["fees_distribution"]["totalMessageFees"] == 80
    assert updated["message_allocations"] == accounting["message_allocations"]
    assert updated["top_ups"][-1]["primaryAmount"] == 0
    assert updated["top_ups"][-1]["messageFees"] == 25


def test_apply_fee_top_up_checks_execution_budget_floor_after_merge():
    policy = StudioFeePolicy(receipt_gas_price=1)
    budget_floor = policy.message_fee_params_budget_floor()
    fees_distribution = _fees_distribution(
        execution_budget_per_round=budget_floor,
    )
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5, policy),
        user_value=0,
        policy=policy,
    )

    updated = apply_fee_top_up(
        accounting,
        fees_distribution=_top_up_distribution(execution_budget_per_round=1),
        amount=1101,
        policy=policy,
    )

    assert updated["fees_distribution"]["executionBudgetPerRound"] == budget_floor + 1
    assert updated["execution_budget_total"] == updated["fees_distribution"][
        "executionBudgetPerRound"
    ] * get_leader_rounds(updated["fees_distribution"])

    underfunded = create_fee_accounting(
        fees_distribution=_fees_distribution(),
        num_of_validators=5,
        submitted_value=1100,
        user_value=0,
        policy=policy,
    )

    with pytest.raises(BudgetTooLow):
        apply_fee_top_up(
            underfunded,
            fees_distribution=_top_up_distribution(execution_budget_per_round=1),
            amount=1101,
            policy=policy,
        )


def test_post_activation_top_up_uses_locked_formula_and_floor():
    activation_policy = StudioFeePolicy(
        receipt_gas_price=1,
        intrinsic_gas=0,
        bootloader_overhead=0,
        gas_per_changed_slot=0,
        calldata_gas_per_byte=0,
        fixed_propose_receipt_gas=10,
    )
    fees_distribution = _fees_distribution(
        execution_budget_per_round=10,
        receipt_fee_max_gas_price=2,
    )
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(
            fees_distribution,
            5,
            activation_policy,
        ),
        user_value=0,
        policy=activation_policy,
    )
    activated, should_cancel = activate_fee_accounting(
        accounting,
        activation_policy,
    )
    assert should_cancel is False

    topped_up = apply_fee_top_up(
        activated,
        fees_distribution=_top_up_distribution(execution_budget_per_round=1),
        amount=1,
        policy=replace(
            activation_policy,
            receipt_gas_price=2,
            fixed_propose_receipt_gas=100,
        ),
    )

    assert topped_up["fees_distribution"]["executionBudgetPerRound"] == 11


def test_apply_fee_top_up_only_raises_existing_price_caps():
    fees_distribution = _fees_distribution(
        max_price_gen_per_time_unit=100,
        storage_fee_max_gas_price=80,
        receipt_fee_max_gas_price=60,
    )
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5),
        user_value=0,
    )

    unchanged = apply_fee_top_up(
        accounting,
        fees_distribution=_top_up_distribution(
            max_price_gen_per_time_unit=90,
            storage_fee_max_gas_price=0,
            receipt_fee_max_gas_price=60,
        ),
        amount=1,
    )

    assert unchanged["fees_distribution"]["maxPriceGenPerTimeUnit"] == 100
    assert unchanged["fees_distribution"]["storageFeeMaxGasPrice"] == 80
    assert unchanged["fees_distribution"]["receiptFeeMaxGasPrice"] == 60

    raised = apply_fee_top_up(
        unchanged,
        fees_distribution=_top_up_distribution(
            max_price_gen_per_time_unit=120,
            storage_fee_max_gas_price=85,
            receipt_fee_max_gas_price=70,
        ),
        amount=22_000,
    )

    assert raised["fees_distribution"]["maxPriceGenPerTimeUnit"] == 120
    assert raised["fees_distribution"]["storageFeeMaxGasPrice"] == 85
    assert raised["fees_distribution"]["receiptFeeMaxGasPrice"] == 70

    uncapped = create_fee_accounting(
        fees_distribution=_fees_distribution(),
        num_of_validators=5,
        submitted_value=1100,
        user_value=0,
    )

    still_uncapped = apply_fee_top_up(
        uncapped,
        fees_distribution=_top_up_distribution(
            max_price_gen_per_time_unit=120,
            storage_fee_max_gas_price=85,
            receipt_fee_max_gas_price=70,
        ),
        amount=1,
    )

    assert still_uncapped["fees_distribution"]["maxPriceGenPerTimeUnit"] == 0
    assert still_uncapped["fees_distribution"]["storageFeeMaxGasPrice"] == 0
    assert still_uncapped["fees_distribution"]["receiptFeeMaxGasPrice"] == 0


def test_apply_fee_top_up_uses_locked_cap_after_live_gen_price_rises():
    submission_policy = StudioFeePolicy(gen_per_time_unit=1)
    fees_distribution = _fees_distribution(max_price_gen_per_time_unit=10)
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(
            fees_distribution,
            5,
            submission_policy,
        ),
        user_value=0,
        policy=submission_policy,
    )

    updated = apply_fee_top_up(
        accounting,
        fees_distribution=_top_up_distribution(execution_budget_per_round=1),
        amount=1,
        policy=StudioFeePolicy(gen_per_time_unit=20),
    )

    assert updated["fees_distribution"]["maxPriceGenPerTimeUnit"] == 10
    assert updated["fees_distribution"]["executionBudgetPerRound"] == 1
    assert updated["primary_fee_budget"] == accounting["primary_fee_budget"] + 1


class _FakeAccountsManager:
    def __init__(self, balance=0):
        self.balance = balance
        self.credits = []
        self.debits = []

    def get_account_balance(self, address):
        return self.balance

    def credit_account_balance(self, address, amount):
        self.credits.append((address, amount))
        self.balance += amount

    def debit_account_balance(self, address, amount):
        self.debits.append((address, amount))
        self.balance -= amount
        return True


class _FakeTransactionsProcessor:
    def __init__(self, transaction):
        self.transaction = transaction
        self.updated_fee_accounting = None
        self.appeal_updates = []
        self.finalization_head = True

    def get_transaction_by_hash(self, tx_hash):
        if tx_hash != self.transaction["hash"]:
            return None
        return self.transaction

    def update_transaction_fee_accounting(self, tx_hash, fee_accounting):
        assert tx_hash == self.transaction["hash"]
        self.updated_fee_accounting = fee_accounting
        self.transaction["data"]["fee_accounting"] = fee_accounting

    def apply_transaction_fee_top_up(self, tx_hash, **kwargs):
        assert tx_hash == self.transaction["hash"]
        if self.transaction["status"] in {
            "ACCEPTED",
            "UNDETERMINED",
            "LEADER_TIMEOUT",
            "VALIDATORS_TIMEOUT",
            "FINALIZED",
            "CANCELED",
        }:
            raise ValueError("InvalidTransactionStatus")
        current = self.transaction["data"].get("fee_accounting")
        if current is None:
            raise ValueError("FeeAccountingMissing")
        updated = apply_fee_top_up(
            current,
            num_of_validators=int(
                self.transaction.get("num_of_initial_validators") or 5
            ),
            **kwargs,
        )
        self.update_transaction_fee_accounting(tx_hash, updated)
        return updated

    def set_transaction_appeal(self, tx_hash, appeal):
        assert tx_hash == self.transaction["hash"]
        self.appeal_updates.append((tx_hash, appeal))
        self.transaction["appealed"] = appeal

    def admit_transaction_appeal(self, tx_hash, *, prepare_fee_accounting, **kwargs):
        assert tx_hash == self.transaction["hash"]
        if self.transaction.get("appealed"):
            raise ValueError("CanNotAppeal")
        kwargs.setdefault(
            "appeal_context",
            (
                "leaderAppealReplay"
                if self.transaction.get("status") in {"UNDETERMINED", "LEADER_TIMEOUT"}
                else "validatorAppeal"
            ),
        )
        self.transaction["consensus_history"] = prepare_appeal_decision_basis(
            self.transaction.get("consensus_history"),
            **kwargs,
        )
        fee_accounting, surplus_refund = prepare_fee_accounting(
            self.transaction["data"].get("fee_accounting")
        )
        if fee_accounting is not None:
            self.update_transaction_fee_accounting(tx_hash, fee_accounting)
        self.set_transaction_appeal(tx_hash, True)
        return surplus_refund

    def is_transaction_finalization_head(self, tx_hash):
        assert tx_hash == self.transaction["hash"]
        return self.finalization_head


def _decoded_top_up(tx_id, *, amount, fees_distribution=None):
    return DecodedRollupTransaction(
        from_address="0x1111111111111111111111111111111111111111",
        to_address="0x0000000000000000000000000000000000000000",
        data=DecodedTopUpFeesDataArgs(
            tx_id=tx_id,
            fees_distribution=fees_distribution or _top_up_distribution(),
        ),
        type="2",
        nonce=0,
        value=amount,
    )


def _decoded_appeal(
    tx_id,
    *,
    amount,
    expected_decision_id=1,
    fees_distribution=None,
    top_up_and_submit=False,
):
    return DecodedRollupTransaction(
        from_address="0x1111111111111111111111111111111111111111",
        to_address="0x0000000000000000000000000000000000000000",
        data=DecodedsubmitAppealDataArgs(
            tx_id=tx_id,
            expected_decision_id=expected_decision_id,
            fees_distribution=fees_distribution,
            top_up_and_submit=top_up_and_submit,
        ),
        type="2",
        nonce=0,
        value=amount,
    )


def _decoded_finalize(tx_id, *, expected_decision_id=1, amount=0):
    return DecodedRollupTransaction(
        from_address="0x1111111111111111111111111111111111111111",
        to_address="0x0000000000000000000000000000000000000000",
        data=DecodedFinalizeTransactionDataArgs(
            tx_id=tx_id,
            expected_decision_id=expected_decision_id,
        ),
        type="2",
        nonce=0,
        value=amount,
    )


def _fee_accounted_tx(*, status="PENDING", accounting=None):
    tx_hash = "0x" + "12" * 32
    return {
        "hash": tx_hash,
        "status": status,
        "num_of_initial_validators": 5,
        "consensus_history": {},
        "data": {
            "fee_accounting": accounting
            or create_fee_accounting(
                fees_distribution=_fees_distribution(),
                num_of_validators=5,
                submitted_value=1100,
                user_value=0,
            )
        },
    }


def _env_fee_accounting(fees_distribution=None):
    policy = StudioFeePolicy.from_env()
    fees_distribution = fees_distribution or _env_fees_distribution()
    return create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5, policy),
        user_value=0,
        policy=policy,
    )


def _env_appeal_charge(accounting, *, status="ACCEPTED", current_round=0):
    return calculate_appeal_charge(
        accounting["fees_distribution"],
        current_round=current_round,
        status=status,
        terminal_committee_upper_bound=5,
        policy=StudioFeePolicy.from_env(),
    )


def test_top_up_fees_endpoint_updates_accounting_and_debits_sender():
    tx = _fee_accounted_tx()
    accounts = _FakeAccountsManager(balance=0)
    transactions = _FakeTransactionsProcessor(tx)
    amount = _required_env_fee_deposit(_fees_distribution())

    tx_id = _handle_top_up_fees(
        accounts_manager=accounts,
        transactions_processor=transactions,
        decoded_rollup_transaction=_decoded_top_up(tx["hash"], amount=amount),
    )

    updated = transactions.updated_fee_accounting
    assert tx_id == tx["hash"]
    assert updated["paid_fee_value"] == 1100 + amount
    assert updated["primary_fee_budget"] == 1100 + amount
    assert updated["top_ups"][-1]["amount"] == amount
    assert accounts.credits == [("0x1111111111111111111111111111111111111111", amount)]
    assert accounts.debits == [("0x1111111111111111111111111111111111111111", amount)]


def test_terminal_committee_excludes_compacted_leader_appeal_replay_leader():
    history = {
        "consensus_results": [
            {"consensus_round": "Undetermined"},
            {"consensus_round": "Leader Appeal Successful"},
        ]
    }

    assert _current_fee_round(history) == 2
    assert _normal_leader_count(history) == 2


def test_terminal_committee_excludes_every_rotated_normal_leader():
    history = {
        "consensus_results": [
            {"consensus_round": "Leader Rotation"},
            {"consensus_round": "Leader Rotation"},
            {"consensus_round": "Accepted"},
            {"consensus_round": "Leader Rotation Appeal"},
            {"consensus_round": "Leader Appeal Failed"},
        ]
    }

    # Consensus records the initial leader and every forked normal-round
    # generation. Appeal-jury leaders are excluded, but a leader-appeal replay
    # and its rotations are normal-round leaders too.
    assert _normal_leader_count(history) == 5


@pytest.mark.parametrize(
    "status",
    [
        "ACCEPTED",
        "UNDETERMINED",
        "LEADER_TIMEOUT",
        "VALIDATORS_TIMEOUT",
        "FINALIZED",
        "CANCELED",
    ],
)
def test_top_up_fees_endpoint_rejects_final_decided_transaction_status(status):
    tx = _fee_accounted_tx(status=status)
    transactions = _FakeTransactionsProcessor(tx)

    with pytest.raises(InvalidTransactionError, match="InvalidTransactionStatus"):
        _handle_top_up_fees(
            accounts_manager=_FakeAccountsManager(balance=5000),
            transactions_processor=transactions,
            decoded_rollup_transaction=_decoded_top_up(tx["hash"], amount=1100),
        )

    assert transactions.updated_fee_accounting is None


def test_submit_appeal_endpoint_records_separate_bond_and_typed_funding(monkeypatch):
    monkeypatch.setenv("VITE_FINALITY_WINDOW", "30")
    monkeypatch.setenv("VITE_FINALITY_WINDOW_APPEAL_FAILED_REDUCTION", "0.2")
    monkeypatch.setattr("backend.protocol_rpc.endpoints.time.time", lambda: 1_000)
    accounting = _env_fee_accounting()
    tx = _fee_accounted_tx(status="ACCEPTED", accounting=accounting)
    accounts = _FakeAccountsManager(balance=0)
    transactions = _FakeTransactionsProcessor(tx)
    charge = _env_appeal_charge(accounting)
    appeal_bond = charge["bond"]
    submitted = appeal_bond + charge["funding"]

    class _MsgHandler:
        def __init__(self):
            self.events = []

        def send_message(self, log_event, log_to_terminal=True):
            self.events.append((log_event, log_to_terminal))

    handler = _MsgHandler()

    tx_id = _handle_appeal_or_top_up_and_submit(
        accounts_manager=accounts,
        transactions_processor=transactions,
        msg_handler=handler,
        decoded_rollup_transaction=_decoded_appeal(tx["hash"], amount=submitted),
    )

    updated = transactions.updated_fee_accounting
    assert tx_id == tx["hash"]
    assert updated["appeal_bonds_total"] == appeal_bond
    assert updated["primary_fee_budget"] == (
        accounting["primary_fee_budget"] + charge["funding"]
    )
    assert updated["appeal_bonds"][0]["minimumRequired"] == appeal_bond
    assert updated["appeal_bonds"][0]["topUpAndSubmit"] is False
    assert transactions.appeal_updates == [(tx["hash"], True)]
    assert tx["consensus_history"]["activeAppealBasis"] == {
        "decisionId": 1,
        "context": "validatorAppeal",
        "submittedAt": 1_000,
        "nextAppealWindow": 24,
    }
    assert len(handler.events) == 1
    assert accounts.credits == [
        ("0x1111111111111111111111111111111111111111", submitted)
    ]
    assert accounts.debits == [
        ("0x1111111111111111111111111111111111111111", submitted)
    ]


def test_submit_appeal_rejects_until_acceptance_effects_are_acknowledged(
    monkeypatch,
):
    monkeypatch.setattr("backend.protocol_rpc.endpoints.time.time", lambda: 1_000)
    accounting = _env_fee_accounting()
    accounting["active_message_generation"] = {
        "acceptanceDispatchRequired": True,
        "acceptanceDispatched": False,
    }
    tx = _fee_accounted_tx(status="ACCEPTED", accounting=accounting)
    accounts = _FakeAccountsManager(balance=10_000)

    with pytest.raises(InvalidTransactionError, match="CanNotAppeal"):
        _handle_appeal_or_top_up_and_submit(
            accounts_manager=accounts,
            transactions_processor=_FakeTransactionsProcessor(tx),
            msg_handler=SimpleNamespace(),
            decoded_rollup_transaction=_decoded_appeal(
                tx["hash"],
                amount=10_000,
            ),
        )

    assert accounts.debits == []


def test_concurrent_appeal_loser_is_not_charged_or_recorded(monkeypatch):
    monkeypatch.setenv("VITE_FINALITY_WINDOW", "30")
    monkeypatch.setattr("backend.protocol_rpc.endpoints.time.time", lambda: 1_000)
    accounting = _env_fee_accounting()
    tx = _fee_accounted_tx(status="ACCEPTED", accounting=accounting)
    accounts = _FakeAccountsManager(balance=10_000)

    class _RacingTransactionsProcessor(_FakeTransactionsProcessor):
        def admit_transaction_appeal(self, tx_hash, **kwargs):
            # The competing request won after this request's initial read but
            # before its row-locked compare-and-set.
            raise ValueError("CanNotAppeal")

    class _MsgHandler:
        def send_message(self, log_event, log_to_terminal=True):
            raise AssertionError("a losing admission must not emit success")

    transactions = _RacingTransactionsProcessor(tx)
    charge = _env_appeal_charge(accounting)

    with pytest.raises(InvalidTransactionError, match="CanNotAppeal"):
        _handle_appeal_or_top_up_and_submit(
            accounts_manager=accounts,
            transactions_processor=transactions,
            msg_handler=_MsgHandler(),
            decoded_rollup_transaction=_decoded_appeal(
                tx["hash"], amount=charge["bond"] + charge["funding"]
            ),
        )

    assert transactions.updated_fee_accounting is None
    assert transactions.appeal_updates == []
    assert accounts.debits == []
    assert accounts.credits == []


def test_leader_timeout_appeal_uses_next_normal_fee_schedule():
    fees = _env_fees_distribution(appeals=1, rotations=[3, 2])
    accounting = _env_fee_accounting(fees)
    tx = _fee_accounted_tx(status="LEADER_TIMEOUT", accounting=accounting)
    tx["config_rotation_rounds"] = 3
    tx["leader_timeout_validators"] = [
        "0x1111111111111111111111111111111111111111",
        "0x2222222222222222222222222222222222222222",
        "0x3333333333333333333333333333333333333333",
        "0x4444444444444444444444444444444444444444",
    ]
    charge = calculate_appeal_charge(
        fees,
        current_round=0,
        status="LEADER_TIMEOUT",
        replacement_rotations=2,
        leader_timeout_live_seats=5,
        policy=StudioFeePolicy.from_env(),
    )
    transactions = _FakeTransactionsProcessor(tx)

    class _MsgHandler:
        def send_message(self, log_event, log_to_terminal=True):
            pass

    _handle_appeal_or_top_up_and_submit(
        accounts_manager=_FakeAccountsManager(balance=0),
        transactions_processor=transactions,
        msg_handler=_MsgHandler(),
        decoded_rollup_transaction=_decoded_appeal(
            tx["hash"],
            amount=charge["bond"] + charge["funding"],
        ),
    )

    recorded = transactions.updated_fee_accounting["appeal_bonds"][0]
    assert recorded["minimumRequired"] == charge["bond"]
    assert recorded["minimumRequired"] == 3 * (100 + 5 * 200)
    assert recorded["sourceRound"] == 0
    assert recorded["appealRound"] == 2
    assert recorded["fundingBreakdown"] == charge["fundingBreakdown"]
    assert recorded["fundingBreakdown"]["executionBacking"] == 0


def test_submit_appeal_rejects_terminal_validator_recomputation():
    tx = _fee_accounted_tx(status="ACCEPTED")
    tx["consensus_history"] = {
        "consensus_results": [
            {"consensus_round": "Accepted"},
            {"consensus_round": "Validator Appeal Successful"},
            {"consensus_round": "Accepted"},
        ]
    }
    transactions = _FakeTransactionsProcessor(tx)

    class _MsgHandler:
        def send_message(self, log_event, log_to_terminal=True):
            pass

    with pytest.raises(InvalidTransactionError, match="CanNotAppeal"):
        _handle_appeal_or_top_up_and_submit(
            accounts_manager=_FakeAccountsManager(balance=0),
            transactions_processor=transactions,
            msg_handler=_MsgHandler(),
            decoded_rollup_transaction=_decoded_appeal(tx["hash"], amount=10**30),
        )


def test_submit_appeal_prices_against_frozen_activation_pool():
    accounting = _env_fee_accounting()
    accounting, should_cancel = activate_fee_accounting(
        accounting,
        StudioFeePolicy.from_env(),
        selection_pool_count=9,
    )
    assert should_cancel is False
    tx = _fee_accounted_tx(status="ACCEPTED", accounting=accounting)
    tx["consensus_history"] = {"consensus_results": [{"consensus_round": "Accepted"}]}
    transactions = _FakeTransactionsProcessor(tx)
    charge = calculate_appeal_charge(
        accounting["fees_distribution"],
        current_round=0,
        status="ACCEPTED",
        # One normal leader is durably excluded from the frozen pool.
        terminal_committee_upper_bound=8,
        policy=StudioFeePolicy.from_env(),
    )

    class _MsgHandler:
        def send_message(self, log_event, log_to_terminal=True):
            pass

    _handle_appeal_or_top_up_and_submit(
        accounts_manager=_FakeAccountsManager(balance=0),
        transactions_processor=transactions,
        msg_handler=_MsgHandler(),
        decoded_rollup_transaction=_decoded_appeal(
            tx["hash"],
            amount=charge["bond"] + charge["funding"],
        ),
    )

    assert (
        transactions.updated_fee_accounting["appeal_bonds"][0]["fundingBreakdown"]
        == charge["fundingBreakdown"]
    )


def test_top_up_and_submit_appeal_endpoint_expands_capacity_only():
    accounting = _env_fee_accounting()
    tx = _fee_accounted_tx(status="ACCEPTED", accounting=accounting)
    transactions = _FakeTransactionsProcessor(tx)
    charge = _env_appeal_charge(accounting)
    submitted = charge["bond"] + charge["funding"]

    class _MsgHandler:
        def send_message(self, log_event, log_to_terminal=True):
            pass

    tx_id = _handle_appeal_or_top_up_and_submit(
        accounts_manager=_FakeAccountsManager(balance=0),
        transactions_processor=transactions,
        msg_handler=_MsgHandler(),
        decoded_rollup_transaction=_decoded_appeal(
            tx["hash"],
            amount=submitted,
            fees_distribution=_fees_distribution(total_message_fees=55),
            top_up_and_submit=True,
        ),
    )

    updated = transactions.updated_fee_accounting
    assert tx_id == tx["hash"]
    assert updated["paid_fee_value"] == (
        accounting["paid_fee_value"] + charge["funding"]
    )
    assert updated["primary_fee_budget"] == (
        accounting["primary_fee_budget"] + charge["funding"]
    )
    assert updated["message_fee_budget"] == 0
    assert updated["fees_distribution"]["appealRounds"] == 1
    assert updated["fees_distribution"]["totalMessageFees"] == 0
    assert updated["appeal_bonds"][0]["topUpAndSubmit"] is True
    assert updated["appeal_bonds"][0]["feesDistributionIgnored"] is True
    assert transactions.appeal_updates == [(tx["hash"], True)]


def test_submit_appeal_endpoint_rejects_underfunded_induced_work():
    accounting = _env_fee_accounting()
    tx = _fee_accounted_tx(status="ACCEPTED", accounting=accounting)
    transactions = _FakeTransactionsProcessor(tx)
    charge = _env_appeal_charge(accounting)

    class _MsgHandler:
        def send_message(self, log_event, log_to_terminal=True):
            pass

    with pytest.raises(InvalidTransactionError, match="InsufficientFees"):
        _handle_appeal_or_top_up_and_submit(
            accounts_manager=_FakeAccountsManager(balance=5000),
            transactions_processor=transactions,
            msg_handler=_MsgHandler(),
            decoded_rollup_transaction=_decoded_appeal(
                tx["hash"],
                amount=charge["bond"] + charge["funding"] - 1,
            ),
        )

    assert transactions.updated_fee_accounting is None
    assert transactions.appeal_updates == []


def test_submit_appeal_endpoint_rejects_stale_decision_id_before_charging():
    accounting = _env_fee_accounting()
    tx = _fee_accounted_tx(status="ACCEPTED", accounting=accounting)
    transactions = _FakeTransactionsProcessor(tx)
    charge = _env_appeal_charge(accounting)

    class _MsgHandler:
        def send_message(self, log_event, log_to_terminal=True):
            pass

    with pytest.raises(InvalidTransactionError, match="CanNotAppeal"):
        _handle_appeal_or_top_up_and_submit(
            accounts_manager=_FakeAccountsManager(balance=5000),
            transactions_processor=transactions,
            msg_handler=_MsgHandler(),
            decoded_rollup_transaction=_decoded_appeal(
                tx["hash"],
                amount=charge["bond"] + charge["funding"],
                expected_decision_id=2,
            ),
        )

    assert transactions.updated_fee_accounting is None
    assert transactions.appeal_updates == []


def test_submit_appeal_endpoint_rejects_missing_decision_id_before_charging():
    accounting = _env_fee_accounting()
    tx = _fee_accounted_tx(status="ACCEPTED", accounting=accounting)
    transactions = _FakeTransactionsProcessor(tx)
    charge = _env_appeal_charge(accounting)

    class _MsgHandler:
        def send_message(self, log_event, log_to_terminal=True):
            pass

    accounts = _FakeAccountsManager(balance=5000)
    with pytest.raises(InvalidTransactionError, match="CanNotAppeal"):
        _handle_appeal_or_top_up_and_submit(
            accounts_manager=accounts,
            transactions_processor=transactions,
            msg_handler=_MsgHandler(),
            decoded_rollup_transaction=_decoded_appeal(
                tx["hash"],
                amount=charge["bond"] + charge["funding"],
                expected_decision_id=None,
            ),
        )

    assert accounts.debits == []
    assert transactions.updated_fee_accounting is None
    assert transactions.appeal_updates == []


def test_submit_appeal_endpoint_rejects_elapsed_appeal_window_before_charging(
    monkeypatch,
):
    accounting = _env_fee_accounting()
    tx = _fee_accounted_tx(status="ACCEPTED", accounting=accounting)
    tx["timestamp_awaiting_finalization"] = 1_000
    tx["appeal_processing_time"] = 5
    tx["appeal_failed"] = 1
    transactions = _FakeTransactionsProcessor(tx)
    charge = _env_appeal_charge(accounting)
    monkeypatch.setenv("VITE_FINALITY_WINDOW", "30")
    monkeypatch.setenv("VITE_FINALITY_WINDOW_APPEAL_FAILED_REDUCTION", "0.2")
    # Deadline = 1000 + 5 paused seconds + 30 * 0.8 = 1029. Consensus
    # rejects at the boundary, before accepting or escrowing any appeal value.
    monkeypatch.setattr("backend.protocol_rpc.endpoints.time.time", lambda: 1_029)

    class _MsgHandler:
        def send_message(self, log_event, log_to_terminal=True):
            pass

    accounts = _FakeAccountsManager(balance=5000)
    with pytest.raises(InvalidTransactionError, match="CanNotAppeal"):
        _handle_appeal_or_top_up_and_submit(
            accounts_manager=accounts,
            transactions_processor=transactions,
            msg_handler=_MsgHandler(),
            decoded_rollup_transaction=_decoded_appeal(
                tx["hash"],
                amount=charge["bond"] + charge["funding"],
            ),
        )

    assert transactions.updated_fee_accounting is None
    assert transactions.appeal_updates == []
    assert accounts.debits == []


def test_submit_appeal_endpoint_rejects_zero_bond_when_fee_accounting_enabled():
    tx = _fee_accounted_tx(status="ACCEPTED", accounting=_env_fee_accounting())
    transactions = _FakeTransactionsProcessor(tx)

    class _MsgHandler:
        def send_message(self, log_event, log_to_terminal=True):
            pass

    with pytest.raises(InvalidTransactionError, match="InvalidAppealBond"):
        _handle_appeal_or_top_up_and_submit(
            accounts_manager=_FakeAccountsManager(balance=5000),
            transactions_processor=transactions,
            msg_handler=_MsgHandler(),
            decoded_rollup_transaction=_decoded_appeal(tx["hash"], amount=0),
        )

    assert transactions.updated_fee_accounting is None
    assert transactions.appeal_updates == []


@pytest.mark.parametrize("status", ["PENDING", "FINALIZED", "CANCELED"])
def test_gasless_submit_appeal_still_enforces_appealable_status(status):
    tx = _fee_accounted_tx(status=status)
    tx["data"] = {}
    transactions = _FakeTransactionsProcessor(tx)

    class _MsgHandler:
        def send_message(self, log_event, log_to_terminal=True):
            pass

    with pytest.raises(InvalidTransactionError, match="CanNotAppeal"):
        _handle_appeal_or_top_up_and_submit(
            accounts_manager=_FakeAccountsManager(balance=0),
            transactions_processor=transactions,
            msg_handler=_MsgHandler(),
            decoded_rollup_transaction=_decoded_appeal(tx["hash"], amount=0),
        )

    assert transactions.updated_fee_accounting is None
    assert transactions.appeal_updates == []


def test_gasless_submit_appeal_still_rejects_stale_decision_and_duplicate():
    tx = _fee_accounted_tx(status="ACCEPTED")
    tx["data"] = {}

    class _MsgHandler:
        def send_message(self, log_event, log_to_terminal=True):
            pass

    stale_transactions = _FakeTransactionsProcessor(tx)
    with pytest.raises(InvalidTransactionError, match="CanNotAppeal"):
        _handle_appeal_or_top_up_and_submit(
            accounts_manager=_FakeAccountsManager(balance=0),
            transactions_processor=stale_transactions,
            msg_handler=_MsgHandler(),
            decoded_rollup_transaction=_decoded_appeal(
                tx["hash"],
                amount=0,
                expected_decision_id=2,
            ),
        )
    assert stale_transactions.appeal_updates == []

    tx["appealed"] = True
    duplicate_transactions = _FakeTransactionsProcessor(tx)
    with pytest.raises(InvalidTransactionError, match="CanNotAppeal"):
        _handle_appeal_or_top_up_and_submit(
            accounts_manager=_FakeAccountsManager(balance=0),
            transactions_processor=duplicate_transactions,
            msg_handler=_MsgHandler(),
            decoded_rollup_transaction=_decoded_appeal(
                tx["hash"],
                amount=0,
                expected_decision_id=1,
            ),
        )
    assert duplicate_transactions.appeal_updates == []


def test_current_fee_round_ignores_rotation_events_and_expands_leader_appeal():
    consensus_history = {
        "consensus_results": [
            {"consensus_round": "Accepted"},
            {"consensus_round": "Leader Rotation"},
            {"consensus_round": "Leader Rotation Appeal"},
            {"consensus_round": "Leader Appeal Failed"},
        ]
    }

    assert _current_fee_round(consensus_history) == 2
    assert _infer_final_round(consensus_history) == 2


def test_current_fee_round_skips_even_gaps_between_failed_validator_appeals():
    consensus_history = {
        "consensus_results": [
            {"consensus_round": "Accepted"},
            {"consensus_round": "Validator Appeal Failed"},
            {"consensus_round": "Validator Appeal Failed"},
        ]
    }

    assert _current_fee_round(consensus_history) == 3


def _processor_transaction(*, accounting=None, execution_result=None):
    consensus_data = None
    if execution_result is not None:
        consensus_data = {
            "leader_receipt": [
                {
                    "execution_result": execution_result,
                    "result": {"raw": base64.b64encode(b"ok").decode("ascii")},
                }
            ]
        }
    return SimpleNamespace(
        hash="0x" + "34" * 32,
        from_address="0x1111111111111111111111111111111111111111",
        to_address="0x2222222222222222222222222222222222222222",
        data={FEE_ACCOUNTING_KEY: accounting} if accounting is not None else {},
        value=0,
        type=2,
        status=SimpleNamespace(value="ACCEPTED"),
        consensus_data=consensus_data,
        nonce=1,
        r=0,
        s=0,
        v=0,
        created_at=datetime.fromtimestamp(0),
        leader_only=False,
        execution_mode="NORMAL",
        origin_address=None,
        triggered_by_hash=None,
        triggered_on=None,
        triggered_transactions=[],
        appealed=False,
        timestamp_awaiting_finalization=None,
        appeal_failed=0,
        appeal_undetermined=False,
        consensus_history=None,
        timestamp_appeal=None,
        appeal_processing_time=None,
        contract_snapshot=None,
        config_rotation_rounds=0,
        num_of_initial_validators=5,
        last_vote_timestamp=0,
        rotation_count=0,
        appeal_leader_timeout=False,
        leader_timeout_validators=None,
        appeal_validators_timeout=False,
        sim_config=None,
        value_credited=False,
    )


def test_transaction_status_rpc_returns_legacy_status_string():
    class _Processor:
        def get_transaction_status(self, transaction_hash):
            return TransactionsProcessor._status_payload("ACCEPTED")

    assert get_transaction_status(_Processor(), "0x1234") == "ACCEPTED"


def test_transaction_status_rpc_returns_node_v06_payload_for_object_request():
    class _Processor:
        def get_transaction_status(self, transaction_hash):
            assert transaction_hash == "0x1234"
            return TransactionsProcessor._status_payload("ACCEPTED")

    assert get_transaction_status(_Processor(), {"txId": "0x1234"}) == {
        "status": "ACCEPTED",
        "statusCode": 5,
    }


@pytest.mark.parametrize("params", [{}, {"txId": ""}, 123])
def test_transaction_status_rpc_rejects_invalid_node_v06_request(params):
    with pytest.raises(JSONRPCError, match="txId|transaction hash"):
        get_transaction_status(SimpleNamespace(), params)


def test_transaction_status_details_rpc_shape_includes_canonical_status_code():
    class _Processor:
        def get_transaction_status(self, transaction_hash):
            return TransactionsProcessor._status_payload("ACCEPTED")

    assert get_transaction_status_details(_Processor(), "0x1234") == {
        "status": "ACCEPTED",
        "statusCode": 5,
    }


def test_transaction_lifecycle_exposes_active_v06_decision_before_deadline(
    monkeypatch,
):
    monkeypatch.setenv("VITE_FINALITY_WINDOW", "30")
    tx = _fee_accounted_tx(status="ACCEPTED")
    tx.update(
        timestamp_awaiting_finalization=1_000,
        appeal_processing_time=5,
        appeal_failed=0,
        appealed=False,
        execution_mode="NORMAL",
    )
    processor = _FakeTransactionsProcessor(tx)

    assert get_transaction_lifecycle(
        processor, {"txId": tx["hash"], "timestamp": 1_034}
    ) == {
        "storedStatus": "Accepted",
        "storedStatusCode": 5,
        "projectedStatus": "Accepted",
        "projectedStatusCode": 5,
        "resolutionAction": "NoOp",
        "resolutionActionCode": 0,
        "resolutionSource": "FullReveal",
        "resolutionSourceCode": 6,
        "decisionId": "1",
        "decisionActive": True,
        "evaluatedAt": 1_034,
    }


def test_transaction_lifecycle_blocks_actions_while_acceptance_effects_are_pending(
    monkeypatch,
):
    monkeypatch.setenv("VITE_FINALITY_WINDOW", "30")
    accounting = _env_fee_accounting()
    accounting["active_message_generation"] = {
        "acceptanceDispatchRequired": True,
        "acceptanceDispatched": False,
    }
    tx = _fee_accounted_tx(status="ACCEPTED", accounting=accounting)
    tx.update(
        timestamp_awaiting_finalization=1_000,
        appeal_processing_time=0,
        appeal_failed=0,
        appealed=False,
        execution_mode="NORMAL",
    )
    processor = _FakeTransactionsProcessor(tx)

    lifecycle = get_transaction_lifecycle(
        processor, {"txId": tx["hash"], "timestamp": 2_000}
    )
    assert lifecycle["decisionActive"] is True
    assert "effectsPending" not in lifecycle
    assert lifecycle["resolutionAction"] == "NoOp"

    with pytest.raises(InvalidTransactionError, match="CanNotAppeal"):
        estimate_latest_appeal_charge(processor, {"txId": tx["hash"]})
    with pytest.raises(InvalidTransactionError, match="FinalizationNotAllowed"):
        _handle_finalize_transaction(
            transactions_processor=processor,
            decoded_rollup_transaction=_decoded_finalize(tx["hash"]),
        )


def test_transaction_lifecycle_projects_finalize_at_exact_deadline(monkeypatch):
    monkeypatch.setenv("VITE_FINALITY_WINDOW", "30")
    tx = _fee_accounted_tx(status="VALIDATORS_TIMEOUT")
    tx.update(
        timestamp_awaiting_finalization=1_000,
        appeal_processing_time=5,
        appeal_failed=0,
        appealed=False,
        execution_mode="NORMAL",
    )
    processor = _FakeTransactionsProcessor(tx)

    lifecycle = get_transaction_lifecycle(
        processor, {"txId": tx["hash"], "timestamp": 1_035}
    )

    assert lifecycle["storedStatusCode"] == 11
    assert lifecycle["storedStatus"] == "ValidatorsTimeout"
    assert lifecycle["projectedStatusCode"] == 11
    assert lifecycle["projectedStatus"] == "ValidatorsTimeout"
    assert lifecycle["resolutionActionCode"] == 6
    assert lifecycle["resolutionAction"] == "Finalize"
    assert lifecycle["resolutionSourceCode"] == 6
    assert lifecycle["resolutionSource"] == "FullReveal"
    assert lifecycle["decisionId"] == "1"
    assert lifecycle["decisionActive"] is True


def test_transaction_lifecycle_reports_activation_capacity_shortfall_source():
    tx = _fee_accounted_tx(status="UNDETERMINED")
    tx.update(
        consensus_history={
            "consensus_results": [
                {
                    "consensus_round": "Undetermined",
                    "leader_result": None,
                    "validator_results": [],
                }
            ]
        },
        timestamp_awaiting_finalization=1_000,
        appealed=False,
        execution_mode="NORMAL",
    )

    lifecycle = get_transaction_lifecycle(
        _FakeTransactionsProcessor(tx),
        {"txId": tx["hash"], "timestamp": 1_000},
    )

    assert lifecycle["resolutionSourceCode"] == 1
    assert lifecycle["resolutionSource"] == "ActivationInsufficientValidators"


def test_transaction_lifecycle_does_not_finalize_behind_older_transaction(
    monkeypatch,
):
    monkeypatch.setenv("VITE_FINALITY_WINDOW", "30")
    tx = _fee_accounted_tx(status="ACCEPTED")
    tx.update(
        timestamp_awaiting_finalization=1_000,
        appeal_processing_time=0,
        appeal_failed=0,
        appealed=False,
        execution_mode="NORMAL",
    )
    processor = _FakeTransactionsProcessor(tx)
    processor.finalization_head = False

    lifecycle = get_transaction_lifecycle(
        processor, {"txId": tx["hash"], "timestamp": 1_030}
    )

    assert lifecycle["resolutionActionCode"] == 0


def test_transaction_lifecycle_uses_appeal_decision_ordinal_and_source():
    tx = _fee_accounted_tx(status="ACCEPTED")
    tx.update(
        consensus_history={
            "consensus_results": [
                {"consensus_round": "Accepted"},
                {"consensus_round": "Validator Appeal Failed"},
            ]
        },
        timestamp_awaiting_finalization=1_000,
        appealed=False,
        execution_mode="NORMAL",
    )
    processor = _FakeTransactionsProcessor(tx)

    lifecycle = get_transaction_lifecycle(
        processor, {"txId": tx["hash"], "timestamp": 1_000}
    )

    assert lifecycle["decisionId"] == "2"
    assert lifecycle["resolutionSourceCode"] == 9
    assert lifecycle["resolutionSource"] == "AppealFullReveal"


def test_transaction_lifecycle_exposes_terminal_replacement_for_finalization():
    tx = _fee_accounted_tx(status="ACCEPTED")
    tx.update(
        consensus_history={
            "consensus_results": [
                {"consensus_round": "Accepted"},
                {"consensus_round": "Validator Appeal Successful"},
                {"consensus_round": "Accepted"},
            ]
        },
        timestamp_awaiting_finalization=1_000,
        appealed=False,
        execution_mode="NORMAL",
    )

    lifecycle = get_transaction_lifecycle(
        _FakeTransactionsProcessor(tx),
        {"txId": tx["hash"], "timestamp": 10_000},
    )

    # Consensus keeps the terminal replacement's DecisionRecord active so it
    # can finalize; terminality suppresses only a further appeal.
    assert lifecycle["decisionId"] == "2"
    assert lifecycle["decisionActive"] is True
    assert lifecycle["resolutionAction"] == "Finalize"
    assert lifecycle["resolutionSource"] == "FullReveal"


def test_terminal_replacement_can_finalize_but_cannot_be_appealed(monkeypatch):
    monkeypatch.setenv("VITE_FINALITY_WINDOW", "30")
    monkeypatch.setattr("backend.protocol_rpc.endpoints.time.time", lambda: 1_030)
    tx = _fee_accounted_tx(status="ACCEPTED", accounting=_env_fee_accounting())
    tx.update(
        consensus_history={
            "consensus_results": [
                {"consensus_round": "Accepted"},
                {"consensus_round": "Validator Appeal Successful"},
                {"consensus_round": "Accepted"},
            ]
        },
        timestamp_awaiting_finalization=1_000,
        appeal_processing_time=0,
        appealed=False,
        execution_mode="NORMAL",
    )
    processor = _FakeTransactionsProcessor(tx)

    assert (
        _handle_finalize_transaction(
            transactions_processor=processor,
            decoded_rollup_transaction=_decoded_finalize(
                tx["hash"], expected_decision_id=2
            ),
        )
        == tx["hash"]
    )
    with pytest.raises(InvalidTransactionError, match="CanNotAppeal"):
        estimate_latest_appeal_charge(processor, {"txId": tx["hash"]})


def test_latest_appeal_charge_rpc_uses_admission_quote_and_decision_id(
    monkeypatch,
):
    monkeypatch.setenv("VITE_FINALITY_WINDOW", "30")
    monkeypatch.setattr("backend.protocol_rpc.endpoints.time.time", lambda: 1_001)
    accounting = _env_fee_accounting()
    tx = _fee_accounted_tx(status="ACCEPTED", accounting=accounting)
    tx.update(
        timestamp_awaiting_finalization=1_000,
        appeal_processing_time=0,
        appeal_failed=0,
        appealed=False,
        execution_mode="NORMAL",
    )
    expected = _env_appeal_charge(accounting)

    assert estimate_latest_appeal_charge(
        _FakeTransactionsProcessor(tx), {"txId": tx["hash"]}
    ) == {
        "decisionId": "1",
        "bond": str(expected["bond"]),
        "funding": str(expected["funding"]),
        "appealDeadline": "1030",
    }


def test_latest_appeal_charge_rpc_prices_capacity_limited_jury_exactly(monkeypatch):
    monkeypatch.setenv("VITE_FINALITY_WINDOW", "30")
    monkeypatch.setattr("backend.protocol_rpc.endpoints.time.time", lambda: 1_001)
    accounting = _env_fee_accounting()
    accounting["selection_pool_count"] = 10
    tx = _fee_accounted_tx(status="ACCEPTED", accounting=accounting)
    tx.update(
        timestamp_awaiting_finalization=1_000,
        appeal_processing_time=0,
        appeal_failed=0,
        appealed=False,
        execution_mode="NORMAL",
        consensus_data={
            "validators": [
                _history_receipt(
                    mode="validator",
                    address=f"0x{index:040x}",
                )
                for index in range(2, 6)
            ]
        },
        consensus_history={
            "consensus_results": [
                {
                    "consensus_round": "Accepted",
                    "leader_result": [
                        _history_receipt(
                            mode="leader",
                            address="0x0000000000000000000000000000000000000001",
                        )
                    ],
                    "validator_results": [],
                }
            ]
        },
    )
    expected = calculate_appeal_charge(
        accounting["fees_distribution"],
        current_round=0,
        status="ACCEPTED",
        terminal_committee_upper_bound=9,
        available_appeal_validators=5,
        policy=StudioFeePolicy.from_env(),
    )
    scheduled = calculate_appeal_charge(
        accounting["fees_distribution"],
        current_round=0,
        status="ACCEPTED",
        terminal_committee_upper_bound=9,
        policy=StudioFeePolicy.from_env(),
    )

    quote = estimate_latest_appeal_charge(
        _FakeTransactionsProcessor(tx), {"txId": tx["hash"]}
    )

    assert expected["juryCount"] == 5
    assert scheduled["juryCount"] == 7
    assert int(quote["funding"]) == expected["funding"]
    assert expected["funding"] < scheduled["funding"]

    class _MsgHandler:
        def send_message(self, log_event, log_to_terminal=True):
            pass

    processor = _FakeTransactionsProcessor(tx)
    total = int(quote["bond"]) + int(quote["funding"])
    with pytest.raises(InvalidTransactionError, match="InsufficientFees"):
        _handle_appeal_or_top_up_and_submit(
            accounts_manager=_FakeAccountsManager(balance=total),
            transactions_processor=processor,
            msg_handler=_MsgHandler(),
            decoded_rollup_transaction=_decoded_appeal(
                tx["hash"],
                amount=total - 1,
            ),
        )

    _handle_appeal_or_top_up_and_submit(
        accounts_manager=_FakeAccountsManager(balance=total),
        transactions_processor=processor,
        msg_handler=_MsgHandler(),
        decoded_rollup_transaction=_decoded_appeal(tx["hash"], amount=total),
    )
    assert processor.updated_fee_accounting["appeal_bonds"][0]["juryCount"] == 5


def test_latest_appeal_charge_excludes_historical_jurors_from_fresh_capacity(
    monkeypatch,
):
    monkeypatch.setenv("VITE_FINALITY_WINDOW", "30")
    monkeypatch.setattr("backend.protocol_rpc.endpoints.time.time", lambda: 1_001)
    accounting = _env_fee_accounting()
    accounting["selection_pool_count"] = 10
    tx = _fee_accounted_tx(status="ACCEPTED", accounting=accounting)
    tx.update(
        timestamp_awaiting_finalization=1_000,
        appealed=False,
        consensus_data={
            "leader_receipt": _history_receipt(
                mode="leader",
                address="0x0000000000000000000000000000000000000001",
            ),
            "validators": [
                _history_receipt(mode="validator", address=f"0x{index:040x}")
                for index in range(2, 5)
            ],
        },
        consensus_history={
            "consensus_results": [
                {
                    "consensus_round": "Accepted",
                    "leader_result": [
                        _history_receipt(
                            mode="leader",
                            address="0x0000000000000000000000000000000000000001",
                        )
                    ],
                    "validator_results": [],
                },
                {
                    "consensus_round": "Validator Appeal Failed",
                    "leader_result": [],
                    "validator_results": [
                        _history_receipt(
                            mode="validator",
                            address=f"0x{index:040x}",
                        )
                        for index in range(5, 9)
                    ],
                },
            ]
        },
    )

    quote = estimate_latest_appeal_charge(
        _FakeTransactionsProcessor(tx), {"txId": tx["hash"]}
    )
    expected = calculate_appeal_charge(
        accounting["fees_distribution"],
        current_round=1,
        status="ACCEPTED",
        terminal_committee_upper_bound=9,
        available_appeal_validators=2,
        policy=StudioFeePolicy.from_env(),
    )

    assert expected["juryCount"] == 2
    assert int(quote["funding"]) == expected["funding"]


def test_appeal_capacity_uses_frozen_pool_identities_and_live_availability():
    tx = {
        "consensus_data": {
            "leader_receipt": _history_receipt(
                mode="leader",
                address="0x0000000000000000000000000000000000000001",
            ),
            "validators": [
                _history_receipt(
                    mode="validator",
                    address="0x0000000000000000000000000000000000000002",
                )
            ],
        },
        "consensus_history": None,
    }

    available = _available_appeal_validator_count(
        tx,
        5,
        frozen_pool_addresses=[f"0x{index:040x}" for index in range(1, 6)],
        # Frozen validator 5 was removed; new validator 6 is ineligible.
        live_pool_addresses=[f"0x{index:040x}" for index in (1, 2, 3, 4, 6)],
    )

    assert available == 2


def test_execution_selection_rejects_post_activation_validator_additions():
    validators = [
        {"address": f"0x{index:040x}", "stake": 1} for index in (1, 2, 3, 4, 6)
    ]
    accounting = {
        "selection_pool_addresses": [f"0x{index:040x}" for index in (1, 2, 3, 4, 5)]
    }

    eligible = _validators_in_frozen_selection_pool(validators, accounting)

    # Removed frozen validator 5 is unavailable; newly-added validator 6 is
    # not part of this transaction's activation authority.
    assert {validator["address"] for validator in eligible} == {
        f"0x{index:040x}" for index in (1, 2, 3, 4)
    }


def test_latest_appeal_charge_rejects_capacity_limited_undetermined_replay(
    monkeypatch,
):
    monkeypatch.setenv("VITE_FINALITY_WINDOW", "30")
    monkeypatch.setattr("backend.protocol_rpc.endpoints.time.time", lambda: 1_001)
    accounting = _env_fee_accounting()
    accounting["selection_pool_count"] = 10
    tx = _fee_accounted_tx(status="UNDETERMINED", accounting=accounting)
    tx.update(
        timestamp_awaiting_finalization=1_000,
        appealed=False,
        consensus_data={
            "validators": [
                _history_receipt(
                    mode="validator",
                    address=f"0x{index:040x}",
                )
                for index in range(2, 6)
            ]
        },
        consensus_history={
            "consensus_results": [
                {
                    "consensus_round": "Undetermined",
                    "leader_result": [
                        _history_receipt(
                            mode="leader",
                            address="0x0000000000000000000000000000000000000001",
                        )
                    ],
                }
            ]
        },
    )

    with pytest.raises(InvalidTransactionError, match="CanNotAppeal"):
        estimate_latest_appeal_charge(
            _FakeTransactionsProcessor(tx), {"txId": tx["hash"]}
        )


def test_leader_appeal_requires_the_full_fresh_scheduled_set():
    all_validators = [
        {"address": f"0x{index:040x}", "stake": 1} for index in range(1, 11)
    ]
    consensus_data = SimpleNamespace(
        leader_receipt=[
            SimpleNamespace(
                node_config={"address": "0x0000000000000000000000000000000000000001"}
            )
        ],
        validators=[
            SimpleNamespace(node_config={"address": f"0x{index:040x}"})
            for index in range(2, 6)
        ],
    )
    history = {
        "consensus_results": [
            {
                "consensus_round": "Undetermined",
                "leader_result": [
                    {
                        "node_config": {
                            "address": "0x0000000000000000000000000000000000000001"
                        }
                    }
                ],
            }
        ]
    }

    with pytest.raises(ValueError, match="required 7, available 5"):
        ConsensusAlgorithm.get_extra_validators(
            all_validators,
            history,
            consensus_data,
            0,
            required_extra_validators=7,
        )


def test_leader_replay_matches_fee_plan_after_seven_validator_initial_round():
    all_validators = [
        {"address": f"0x{index:040x}", "stake": 1} for index in range(1, 21)
    ]
    consensus_data = SimpleNamespace(
        leader_receipt=[
            SimpleNamespace(
                node_config={"address": "0x0000000000000000000000000000000000000001"}
            )
        ],
        validators=[
            SimpleNamespace(node_config={"address": f"0x{index:040x}"})
            for index in range(2, 8)
        ],
    )
    history = {
        "consensus_results": [
            {
                "consensus_round": "Undetermined",
                "leader_result": [
                    {
                        "node_config": {
                            "address": "0x0000000000000000000000000000000000000001"
                        }
                    }
                ],
                "validator_results": [
                    _history_receipt(
                        mode="validator",
                        address=f"0x{index:040x}",
                    )
                    for index in range(2, 8)
                ],
            }
        ]
    }

    replay = ConsensusAlgorithm.get_leader_replay_validators(
        all_validators,
        history,
        consensus_data,
        target_committee_size=11,
    )

    assert len(replay) == 11
    assert {item["address"] for item in replay[:6]} == {
        f"0x{index:040x}" for index in range(2, 8)
    }
    assert {item["address"] for item in replay[6:]}.isdisjoint(
        {f"0x{index:040x}" for index in range(1, 8)}
    )


def test_leader_replay_downselects_survivors_when_initial_round_exceeds_target():
    all_validators = [
        {"address": f"0x{index:040x}", "stake": 1} for index in range(1, 31)
    ]
    consensus_data = SimpleNamespace(
        leader_receipt=[
            SimpleNamespace(
                node_config={"address": "0x0000000000000000000000000000000000000001"}
            )
        ],
        validators=[
            SimpleNamespace(node_config={"address": f"0x{index:040x}"})
            for index in range(2, 25)
        ],
    )

    replay = ConsensusAlgorithm.get_leader_replay_validators(
        all_validators,
        {"consensus_results": []},
        consensus_data,
        target_committee_size=11,
    )

    assert [item["address"] for item in replay] == [
        f"0x{index:040x}" for index in range(2, 13)
    ]


def test_appeal_selection_excludes_jurors_retained_only_in_history():
    all_validators = [
        {"address": f"0x{index:040x}", "stake": 1} for index in range(1, 11)
    ]
    consensus_data = SimpleNamespace(
        leader_receipt=[
            SimpleNamespace(
                node_config={"address": "0x0000000000000000000000000000000000000001"}
            )
        ],
        validators=[
            SimpleNamespace(node_config={"address": f"0x{index:040x}"})
            for index in range(2, 5)
        ],
    )
    history = {
        "consensus_results": [
            {
                "consensus_round": "Validator Appeal Failed",
                "leader_result": [],
                "validator_results": [
                    _history_receipt(
                        mode="validator",
                        address=f"0x{index:040x}",
                    )
                    for index in range(5, 9)
                ],
            }
        ]
    }

    _current, selected = ConsensusAlgorithm.get_extra_validators(
        all_validators,
        history,
        consensus_data,
        0,
        required_extra_validators=2,
        allow_short=True,
    )

    assert {item["address"] for item in selected} == {
        "0x0000000000000000000000000000000000000009",
        "0x000000000000000000000000000000000000000a",
    }


def test_repeated_validator_appeal_selects_fresh_scheduled_jury():
    all_validators = [
        {"address": f"0x{index:040x}", "stake": 1} for index in range(1, 31)
    ]
    consumed = [
        SimpleNamespace(node_config={"address": f"0x{index:040x}"})
        for index in range(2, 13)
    ]
    consensus_data = SimpleNamespace(validators=consumed)
    history = {
        "consensus_results": [
            {
                "consensus_round": "Accepted",
                "leader_result": [
                    {
                        "node_config": {
                            "address": "0x0000000000000000000000000000000000000001"
                        }
                    }
                ],
            },
            {"consensus_round": "Validator Appeal Failed", "leader_result": None},
        ]
    }

    current, jury = ConsensusAlgorithm.get_extra_validators(
        all_validators,
        history,
        consensus_data,
        appeal_failed=1,
        required_extra_validators=11,
        allow_short=True,
    )

    consumed_addresses = {item["address"] for item in current}
    consumed_addresses.add("0x0000000000000000000000000000000000000001")
    assert len(jury) == 11
    assert consumed_addresses.isdisjoint({item["address"] for item in jury})


def test_repeated_validator_appeal_uses_every_remaining_fresh_validator():
    all_validators = [
        {"address": f"0x{index:040x}", "stake": 1} for index in range(1, 18)
    ]
    consensus_data = SimpleNamespace(
        validators=[
            SimpleNamespace(node_config={"address": f"0x{index:040x}"})
            for index in range(2, 13)
        ]
    )
    history = {
        "consensus_results": [
            {
                "consensus_round": "Accepted",
                "leader_result": [
                    {
                        "node_config": {
                            "address": "0x0000000000000000000000000000000000000001"
                        }
                    }
                ],
            },
            {"consensus_round": "Validator Appeal Failed", "leader_result": None},
        ]
    }

    _, jury = ConsensusAlgorithm.get_extra_validators(
        all_validators,
        history,
        consensus_data,
        appeal_failed=1,
        required_extra_validators=11,
        allow_short=True,
    )

    assert len(jury) == 5


def test_terminal_replacement_readmits_prior_participants_but_excludes_leaders():
    all_validators = [
        {"address": f"0x{index:040x}", "stake": 1} for index in range(1, 24)
    ]
    history = {
        "consensus_results": [
            {
                "consensus_round": "Leader Rotation",
                "leader_result": [
                    {
                        "node_config": {
                            "address": "0x0000000000000000000000000000000000000001"
                        }
                    }
                ],
            },
            {
                "consensus_round": "Accepted",
                "leader_result": [
                    {
                        "node_config": {
                            "address": "0x0000000000000000000000000000000000000002"
                        }
                    }
                ],
            },
            {"consensus_round": "Validator Appeal Successful", "leader_result": None},
        ]
    }

    terminal = ConsensusAlgorithm.get_terminal_replacement_validators(
        all_validators, history
    )

    assert len(terminal) == 21
    assert {item["address"] for item in terminal} == {
        item["address"] for item in all_validators[2:]
    }


def test_terminal_replacement_excludes_prior_leader_case_insensitively():
    leader = "0xAbCdEf0000000000000000000000000000000001"
    validator = "0x0000000000000000000000000000000000000002"
    history = {
        "consensus_results": [
            {
                "consensus_round": "Accepted",
                "leader_result": [{"node_config": {"address": leader.lower()}}],
            }
        ]
    }

    terminal = ConsensusAlgorithm.get_terminal_replacement_validators(
        [{"address": leader}, {"address": validator}], history
    )

    assert terminal == [{"address": validator}]


def test_consensus_data_validator_lookup_is_case_insensitive():
    leader = "0xAbCdEf0000000000000000000000000000000001"
    validator = "0xFeDcBa0000000000000000000000000000000002"
    consensus_data = SimpleNamespace(
        leader_receipt=[SimpleNamespace(node_config={"address": leader.lower()})],
        validators=[SimpleNamespace(node_config={"address": validator.lower()})],
    )

    selected, remaining = ConsensusAlgorithm.get_validators_from_consensus_data(
        [{"address": leader}, {"address": validator}],
        consensus_data,
        include_leader=True,
    )

    assert selected == [{"address": leader}, {"address": validator}]
    assert remaining == {}


def test_terminal_replacement_keeps_full_frozen_electorate_threshold():
    from backend.consensus.types import ConsensusResult
    from backend.consensus.utils import determine_consensus_from_votes
    from backend.node.types import Vote

    votes = [Vote.AGREE.value] * 11 + [Vote.DISAGREE.value] * 10

    assert determine_consensus_from_votes(votes) == ConsensusResult.MAJORITY_AGREE
    assert (
        determine_consensus_from_votes(votes, electorate_size=23)
        == ConsensusResult.NO_MAJORITY
    )


def test_finalize_request_accepts_exact_active_decision_at_deadline(monkeypatch):
    monkeypatch.setenv("VITE_FINALITY_WINDOW", "30")
    monkeypatch.setattr("backend.protocol_rpc.endpoints.time.time", lambda: 1_030)
    tx = _fee_accounted_tx(status="ACCEPTED")
    tx.update(
        timestamp_awaiting_finalization=1_000,
        appeal_processing_time=0,
        appealed=False,
        execution_mode="NORMAL",
    )
    processor = _FakeTransactionsProcessor(tx)

    assert (
        _handle_finalize_transaction(
            transactions_processor=processor,
            decoded_rollup_transaction=_decoded_finalize(
                tx["hash"], expected_decision_id=1
            ),
        )
        == tx["hash"]
    )


def test_worker_finalization_is_ready_at_the_exact_consensus_deadline(monkeypatch):
    algorithm = ConsensusAlgorithm.__new__(ConsensusAlgorithm)
    algorithm.finality_window_time = 30
    algorithm.finality_window_appeal_failed_reduction = 0
    transaction = SimpleNamespace(
        execution_mode=TransactionExecutionMode.NORMAL,
        timestamp_awaiting_finalization=1_000,
        appeal_processing_time=0,
        appeal_failed=0,
    )
    monkeypatch.setattr("backend.consensus.base.time.time", lambda: 1_030)

    assert algorithm.can_finalize_transaction(None, transaction, 0, []) is True


def test_failed_appeal_window_uses_remaining_time_at_submission_like_consensus():
    initial_history = {
        "consensus_results": [{"consensus_round": "Accepted"}],
    }
    initial_history = materialize_decision_metadata(
        initial_history,
        status="ACCEPTED",
        materialized_at=1_000,
        default_appeal_window=30,
    )
    assert initial_history["latestDecision"] == {
        "decisionId": 1,
        "status": "ACCEPTED",
        "materializedAt": 1_000,
        "appealDeadline": 1_030,
    }

    # Ten seconds of the original window have already elapsed. Consensus
    # freezes 80% of the remaining 20 seconds, then starts that exact 16-second
    # window when the failed appeal materializes its replacement decision.
    appealed_history = prepare_appeal_decision_basis(
        initial_history,
        expected_decision_id=1,
        submitted_at=1_010,
        appeal_deadline=1_030,
        retention_bps=8_000,
    )
    appealed_history["consensus_results"].append(
        {"consensus_round": "Validator Appeal Failed"}
    )
    failed_history = materialize_decision_metadata(
        appealed_history,
        status="ACCEPTED",
        materialized_at=1_020,
        default_appeal_window=30,
    )

    assert "activeAppealBasis" not in failed_history
    assert failed_history["latestDecision"] == {
        "decisionId": 2,
        "status": "ACCEPTED",
        "materializedAt": 1_020,
        "appealDeadline": 1_036,
    }


def test_successful_validator_appeal_starts_fresh_full_window():
    initial_history = materialize_decision_metadata(
        {"consensus_results": [{"consensus_round": "Accepted"}]},
        status="ACCEPTED",
        materialized_at=1_000,
        default_appeal_window=30,
    )
    appealed_history = prepare_appeal_decision_basis(
        initial_history,
        expected_decision_id=1,
        submitted_at=1_010,
        appeal_deadline=1_030,
        retention_bps=8_000,
    )
    appealed_history["consensus_results"].append(
        {"consensus_round": "Validator Appeal Successful"}
    )

    # A successful validator appeal first opens the terminal normal round. It
    # does not materialize a decision or consume the frozen basis by itself.
    assert appealed_history["activeAppealBasis"]["nextAppealWindow"] == 16
    appealed_history["consensus_results"].append({"consensus_round": "Undetermined"})
    terminal_history = materialize_decision_metadata(
        appealed_history,
        status="UNDETERMINED",
        materialized_at=1_025,
        default_appeal_window=30,
    )

    assert "activeAppealBasis" not in terminal_history
    assert terminal_history["latestDecision"] == {
        "decisionId": 2,
        "status": "UNDETERMINED",
        "materializedAt": 1_025,
        "appealDeadline": 1_055,
    }


@pytest.mark.parametrize(
    "replay_round",
    ["Leader Appeal Successful", "Leader Appeal Failed"],
)
def test_leader_appeal_replay_always_starts_fresh_full_window(replay_round):
    initial_history = materialize_decision_metadata(
        {"consensus_results": [{"consensus_round": "Undetermined"}]},
        status="UNDETERMINED",
        materialized_at=1_000,
        default_appeal_window=30,
    )
    appealed_history = prepare_appeal_decision_basis(
        initial_history,
        expected_decision_id=1,
        submitted_at=1_010,
        appeal_deadline=1_030,
        retention_bps=8_000,
    )
    assert appealed_history["activeAppealBasis"] == {
        "decisionId": 1,
        "context": "leaderAppealReplay",
        "submittedAt": 1_010,
        "nextAppealWindow": 16,
    }
    appealed_history["consensus_results"].append({"consensus_round": replay_round})
    replayed_history = materialize_decision_metadata(
        appealed_history,
        status="UNDETERMINED",
        materialized_at=1_025,
        default_appeal_window=30,
    )

    assert replayed_history["latestDecision"]["appealDeadline"] == 1_055


def test_worker_prefers_persisted_remaining_time_deadline(monkeypatch):
    algorithm = ConsensusAlgorithm.__new__(ConsensusAlgorithm)
    algorithm.finality_window_time = 30
    algorithm.finality_window_appeal_failed_reduction = 0.2
    transaction = SimpleNamespace(
        execution_mode=TransactionExecutionMode.NORMAL,
        timestamp_awaiting_finalization=1_000,
        appeal_processing_time=10,
        appeal_failed=1,
        consensus_history={
            "latestDecision": {
                "decisionId": 2,
                "appealDeadline": 1_036,
            }
        },
    )

    # The retired shortcut would finalize at 1000 + 10 + 30*0.8 = 1034.
    monkeypatch.setattr("backend.consensus.base.time.time", lambda: 1_035)
    assert algorithm.can_finalize_transaction(None, transaction, 0, []) is False
    monkeypatch.setattr("backend.consensus.base.time.time", lambda: 1_036)
    assert algorithm.can_finalize_transaction(None, transaction, 0, []) is True


def test_lifecycle_and_finalize_use_persisted_decision_deadline(monkeypatch):
    tx = _fee_accounted_tx(status="ACCEPTED")
    tx.update(
        timestamp_awaiting_finalization=1_000,
        appeal_processing_time=10,
        appeal_failed=1,
        appealed=False,
        execution_mode="NORMAL",
    )
    tx["consensus_history"] = {
        "consensus_results": [
            {"consensus_round": "Accepted"},
            {"consensus_round": "Validator Appeal Failed"},
        ],
        "latestDecision": {
            "decisionId": 2,
            "status": "ACCEPTED",
            "materializedAt": 1_020,
            "appealDeadline": 1_036,
        },
    }
    processor = _FakeTransactionsProcessor(tx)
    monkeypatch.setenv("VITE_FINALITY_WINDOW", "30")
    monkeypatch.setenv("VITE_FINALITY_WINDOW_APPEAL_FAILED_REDUCTION", "0.2")

    before = get_transaction_lifecycle(
        processor,
        {"txId": tx["hash"], "timestamp": 1_035},
    )
    at_deadline = get_transaction_lifecycle(
        processor,
        {"txId": tx["hash"], "timestamp": 1_036},
    )
    assert before["decisionId"] == "2"
    assert before["resolutionAction"] == "NoOp"
    assert at_deadline["resolutionAction"] == "Finalize"

    monkeypatch.setattr("backend.protocol_rpc.endpoints.time.time", lambda: 1_036)
    assert (
        _handle_finalize_transaction(
            transactions_processor=processor,
            decoded_rollup_transaction=_decoded_finalize(
                tx["hash"], expected_decision_id=2
            ),
        )
        == tx["hash"]
    )


@pytest.mark.parametrize("expected_decision_id", [None, 2])
def test_finalize_request_rejects_missing_or_stale_decision_before_mutation(
    monkeypatch, expected_decision_id
):
    monkeypatch.setenv("VITE_FINALITY_WINDOW", "30")
    monkeypatch.setattr("backend.protocol_rpc.endpoints.time.time", lambda: 1_030)
    tx = _fee_accounted_tx(status="ACCEPTED")
    tx.update(
        timestamp_awaiting_finalization=1_000,
        appeal_processing_time=0,
        appealed=False,
        execution_mode="NORMAL",
    )
    processor = _FakeTransactionsProcessor(tx)

    with pytest.raises(InvalidTransactionError, match="FinalizationNotAllowed"):
        _handle_finalize_transaction(
            transactions_processor=processor,
            decoded_rollup_transaction=_decoded_finalize(
                tx["hash"], expected_decision_id=expected_decision_id
            ),
        )

    assert processor.updated_fee_accounting is None
    assert processor.appeal_updates == []


def test_finalize_request_rejects_value_like_nonpayable_consensus_call(monkeypatch):
    monkeypatch.setenv("VITE_FINALITY_WINDOW", "30")
    monkeypatch.setattr("backend.protocol_rpc.endpoints.time.time", lambda: 1_030)
    tx = _fee_accounted_tx(status="ACCEPTED")
    tx.update(
        timestamp_awaiting_finalization=1_000,
        appeal_processing_time=0,
        appealed=False,
        execution_mode="NORMAL",
    )

    with pytest.raises(InvalidTransactionError, match="NonPayableCall"):
        _handle_finalize_transaction(
            transactions_processor=_FakeTransactionsProcessor(tx),
            decoded_rollup_transaction=_decoded_finalize(tx["hash"], amount=1),
        )


def test_finalize_request_rejects_before_deadline_or_behind_older_transaction(
    monkeypatch,
):
    monkeypatch.setenv("VITE_FINALITY_WINDOW", "30")
    tx = _fee_accounted_tx(status="ACCEPTED")
    tx.update(
        timestamp_awaiting_finalization=1_000,
        appeal_processing_time=0,
        appealed=False,
        execution_mode="NORMAL",
    )
    processor = _FakeTransactionsProcessor(tx)

    monkeypatch.setattr("backend.protocol_rpc.endpoints.time.time", lambda: 1_029)
    with pytest.raises(InvalidTransactionError, match="FinalizationNotAllowed"):
        _handle_finalize_transaction(
            transactions_processor=processor,
            decoded_rollup_transaction=_decoded_finalize(tx["hash"]),
        )

    monkeypatch.setattr("backend.protocol_rpc.endpoints.time.time", lambda: 1_030)
    processor.finalization_head = False
    with pytest.raises(InvalidTransactionError, match="FinalizationNotAllowed"):
        _handle_finalize_transaction(
            transactions_processor=processor,
            decoded_rollup_transaction=_decoded_finalize(tx["hash"]),
        )


@pytest.mark.parametrize(
    ("status", "code"),
    [
        ("VALIDATORS_TIMEOUT", 11),
        ("LEADER_TIMEOUT", 12),
        ("LEADER_REVEALING", 13),
    ],
)
def test_transaction_status_details_rpc_uses_consensus_v06_timeout_ordinals(
    status, code
):
    class _Processor:
        def get_transaction_status(self, transaction_hash):
            return TransactionsProcessor._status_payload(status)

    assert get_transaction_status_details(_Processor(), "0x1234") == {
        "status": status,
        "statusCode": code,
    }


@pytest.mark.parametrize(
    ("status", "queue_type"),
    [
        ("PENDING", "1"),
        ("PROPOSING", "1"),
        ("COMMITTING", "1"),
        ("REVEALING", "1"),
        ("APPEAL_COMMITTING", "1"),
        ("APPEAL_REVEALING", "1"),
        ("LEADER_REVEALING", "1"),
        ("ACCEPTED", "2"),
        ("LEADER_TIMEOUT", "2"),
        ("VALIDATORS_TIMEOUT", "2"),
        ("UNDETERMINED", "3"),
        ("FINALIZED", "0"),
        ("CANCELED", "0"),
    ],
)
def test_transaction_queue_type_matches_consensus_v06_status_family(status, queue_type):
    processor = TransactionsProcessor.__new__(TransactionsProcessor)

    result = processor._process_queue({"status": status})

    assert result["queue_type"] == queue_type


def test_transaction_status_details_rpc_raises_not_found():
    class _Processor:
        def get_transaction_status(self, transaction_hash):
            return None

    with pytest.raises(NotFoundError) as exc_info:
        get_transaction_status_details(_Processor(), "0xmissing")

    assert exc_info.value.data == {"hash": "0xmissing"}


def test_transaction_payload_maps_execution_result_name():
    parsed = TransactionsProcessor._parse_transaction_data(
        _processor_transaction(execution_result="SUCCESS")
    )
    failed = TransactionsProcessor._parse_transaction_data(
        _processor_transaction(execution_result="ERROR")
    )

    assert parsed["txExecutionResult"] == 1
    assert parsed["txExecutionResultName"] == "FINISHED_WITH_RETURN"
    assert failed["txExecutionResult"] == 2
    assert failed["txExecutionResultName"] == "FINISHED_WITH_ERROR"


def test_transaction_payload_maps_execution_timeout_to_vote_type_timeout():
    assert TransactionsProcessor._execution_result_fields(
        {
            "leader_receipt": [
                {
                    "execution_result": "ERROR",
                    "genvm_result": {"error_code": "CONSENSUS_LEADER_EXEC_TIMEOUT"},
                }
            ]
        }
    ) == (3, "TIMEOUT")


@pytest.mark.parametrize(
    ("result", "code"),
    [
        ("MAJORITY_AGREE", 1),
        ("MAJORITY_DISAGREE", 2),
        ("TIMEOUT", 3),
        ("DETERMINISTIC_VIOLATION", 4),
        ("NO_MAJORITY", 5),
    ],
)
def test_transaction_round_result_uses_consensus_v06_result_type(result, code):
    from backend.consensus.types import ConsensusResult

    assert TransactionsProcessor._result_type_code(ConsensusResult(result)) == code


def test_transaction_round_payload_uses_raw_round_index_and_ignores_rotation_events():
    processor = TransactionsProcessor.__new__(TransactionsProcessor)
    leader = {
        "vote": "agree",
        "execution_result": "SUCCESS",
        "node_config": {"address": "0x1111111111111111111111111111111111111111"},
    }
    transaction = {
        "consensus_history": {
            "consensus_results": [
                {
                    "consensus_round": "Accepted",
                    "leader_result": [leader, leader],
                    "validator_results": [],
                },
                {
                    "consensus_round": "Leader Rotation",
                    "leader_result": [],
                    "validator_results": [],
                },
            ]
        },
        "data": {},
        "nonce": 7,
        "config_rotation_rounds": 0,
        "rotation_count": 0,
        "type": TransactionType.RUN_CONTRACT,
        "status": "ACCEPTED",
        "execution_mode": "NORMAL",
    }

    result = processor._process_round_data(transaction)

    # ConsensusData.numOfRounds is the current raw index despite its name.
    assert result["num_of_rounds"] == "0"
    assert result["last_round"]["round"] == "0"
    assert result["last_round"]["validator_votes"] == [1]


def test_transaction_round_payload_handles_empty_history_and_raw_appeal_gaps():
    processor = TransactionsProcessor.__new__(TransactionsProcessor)
    base = {
        "data": {},
        "nonce": 7,
        "config_rotation_rounds": 0,
        "rotation_count": 0,
        "type": TransactionType.RUN_CONTRACT,
        "status": "PENDING",
        "execution_mode": "NORMAL",
    }

    empty = processor._process_round_data(
        {**base, "consensus_history": {"consensus_results": []}}
    )
    assert empty["num_of_rounds"] == "0"
    assert empty["last_round"]["round"] == "0"

    rounds = [
        {
            "consensus_round": outcome,
            "leader_result": [],
            "validator_results": [],
        }
        for outcome in [
            "Accepted",
            "Validator Appeal Failed",
            "Validator Appeal Failed",
        ]
    ]
    appealed = processor._process_round_data(
        {**base, "consensus_history": {"consensus_results": rounds}}
    )
    assert appealed["num_of_rounds"] == "3"
    assert appealed["last_round"]["round"] == "3"


@pytest.mark.parametrize(
    (
        "status",
        "active_flags",
        "history_outcomes",
        "bond_status",
        "appeal_round",
        "expected_round",
        "expected_bond",
    ),
    [
        ("REVEALING", {"appealed": True}, ["Accepted"], "ACCEPTED", 1, 1, 1400),
        (
            "PROPOSING",
            {"appeal_undetermined": True},
            ["Undetermined"],
            "UNDETERMINED",
            2,
            2,
            2300,
        ),
        (
            "PROPOSING",
            {"appeal_leader_timeout": True},
            ["Leader Timeout"],
            "LEADER_TIMEOUT",
            2,
            2,
            0,
        ),
        (
            "PROPOSING",
            {},
            ["Accepted", "Validator Appeal Successful"],
            "ACCEPTED",
            1,
            2,
            0,
        ),
    ],
)
def test_transaction_round_payload_projects_active_v06_round_and_bond(
    status,
    active_flags,
    history_outcomes,
    bond_status,
    appeal_round,
    expected_round,
    expected_bond,
):
    processor = TransactionsProcessor.__new__(TransactionsProcessor)
    rounds = [
        {
            "consensus_round": outcome,
            "leader_result": [],
            "validator_results": [],
        }
        for outcome in history_outcomes
    ]
    transaction = {
        "consensus_history": {"consensus_results": rounds},
        "data": {
            FEE_ACCOUNTING_KEY: {
                "appeal_bonds": [
                    {
                        "sourceRound": 0,
                        "appealRound": appeal_round,
                        "status": bond_status,
                        "amount": 1400 if bond_status == "ACCEPTED" else 2300,
                    }
                ]
            }
        },
        "nonce": 7,
        "config_rotation_rounds": 0,
        "rotation_count": 0,
        "type": TransactionType.RUN_CONTRACT,
        "status": status,
        "execution_mode": "NORMAL",
        **active_flags,
    }

    result = processor._process_round_data(transaction)

    assert result["num_of_rounds"] == str(expected_round)
    assert result["last_round"]["round"] == str(expected_round)
    assert result["last_round"]["appeal_bond"] == str(expected_bond)
    assert result["last_round"]["validator_votes"] == []


def test_execution_hash_uses_the_transaction_nonce_in_the_v06_preimage():
    leader = "0x1111111111111111111111111111111111111111"

    expected = Web3.to_hex(
        Web3.solidity_keccak(
            ["address", "uint8", "bytes32", "uint256"],
            [leader, 1, bytes(32), 7],
        )
    )

    assert get_tx_execution_hash(leader, 1, 7) == expected
    assert get_tx_execution_hash(leader, 1, 7) != get_tx_execution_hash(leader, 1, 8)


@pytest.mark.parametrize(
    ("vote", "execution_result", "code"),
    [
        ("agree", "SUCCESS", 1),
        ("agree", "ERROR", 2),
        ("timeout", None, 3),
        ("idle", None, 3),
        ("disagree", None, 4),
        ("deterministic_violation", None, 5),
    ],
)
def test_studio_votes_map_to_consensus_v06_vote_type(vote, execution_result, code):
    from backend.consensus.types import consensus_vote_type_code

    assert consensus_vote_type_code(vote, execution_result) == code


@pytest.mark.parametrize(
    ("votes", "code"),
    [
        ({"a": "agree", "b": "agree", "c": "disagree"}, 1),
        ({"a": "disagree", "b": "disagree", "c": "agree"}, 2),
        ({"a": "timeout", "b": "idle", "c": "agree"}, 3),
        (
            {
                "a": "deterministic_violation",
                "b": "deterministic_violation",
                "c": "agree",
            },
            4,
        ),
        ({"a": "agree", "b": "disagree"}, 5),
    ],
)
def test_transaction_top_level_result_uses_consensus_v06_result_type(votes, code):
    processor = TransactionsProcessor.__new__(TransactionsProcessor)
    transaction = {
        "type": TransactionType.RUN_CONTRACT,
        "status": "ACCEPTED",
        "execution_mode": "NORMAL",
        "consensus_data": {"votes": votes},
    }

    assert processor._process_result(transaction)["result"] == code


@pytest.mark.parametrize(
    "outcome",
    [
        "Leader Timeout",
        "Validators Timeout",
        "Leader Timeout Appeal Failed",
        "Validators Timeout Appeal Failed",
    ],
)
def test_timeout_history_sets_top_level_and_round_result_without_validator_votes(
    outcome,
):
    processor = TransactionsProcessor.__new__(TransactionsProcessor)
    transaction = {
        "consensus_history": {
            "consensus_results": [
                {
                    "consensus_round": outcome,
                    "leader_result": [],
                    "validator_results": [],
                }
            ]
        },
        "consensus_data": {"votes": {}},
        "data": {},
        "nonce": 7,
        "config_rotation_rounds": 0,
        "rotation_count": 0,
        "type": TransactionType.RUN_CONTRACT,
        "status": "FINALIZED",
        "execution_mode": "NORMAL",
    }

    assert processor._process_result(dict(transaction))["result"] == 3
    assert processor._process_round_data(dict(transaction))["last_round"]["result"] == 3


def test_transaction_payload_includes_canonical_fee_object_with_decimal_strings():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(
            execution_budget_per_round=100,
            total_message_fees=55,
            storage_fee_max_gas_price=1,
            receipt_fee_max_gas_price=1,
        ),
        num_of_validators=5,
        submitted_value=1355,
        user_value=100,
        policy=StudioFeePolicy(storage_unit_price=1, receipt_gas_price=1),
        allow_low_execution_budget=True,
    )
    recorded = record_execution_fee_consumption(
        accounting,
        {"genvm_result": {"data_fees_consumed": [20, 3, 0]}},
        StudioFeePolicy(storage_unit_price=1, receipt_gas_price=1),
    )

    parsed = TransactionsProcessor._parse_transaction_data(
        _processor_transaction(accounting=recorded, execution_result="SUCCESS")
    )

    assert parsed["fees"]["deposit"] == "1255"
    assert parsed["fees"]["userValue"] == "100"
    assert parsed["fees"]["distribution"]["leaderTimeunitsAllocation"] == "100"
    assert parsed["fees"]["distribution"]["rotations"] == ["0"]
    assert parsed["fees"]["locked"] == {
        "genPerTimeUnit": "0",
        "storageUnitPrice": "1",
        "receiptGasPrice": "1",
    }
    assert parsed["fees"]["consumed"] == {
        "executionConsumed": str(recorded["execution_fee_consumed"]),
        "storageFeeUsed": "3",
        "messageFeesConsumed": "0",
        "messageFeesBudgetTotal": "55",
        "leaderTimeunitsUsed": "0",
        "validatorTimeunitsUsed": "0",
        "perRound": [],
    }


def test_transaction_payload_fees_null_when_fee_accounting_disabled():
    parsed = TransactionsProcessor._parse_transaction_data(_processor_transaction())

    assert parsed["fees"] is None
    assert parsed["txExecutionResult"] == 0
    assert parsed["txExecutionResultName"] == "NOT_VOTED"


def _acceptance_dispatch_context(calls):
    """A parent owing an acceptance-phase emission that carries no children."""

    accounting = {
        "active_message_generation": {
            "acceptanceDispatchRequired": True,
            "acceptanceDispatched": False,
        }
    }

    class _Processor:
        def insert_transaction(self, *args, **kwargs):
            calls.append((args, kwargs))

        def get_genlayer_transaction_count(self, _address):
            return 0

        def lock_ghost_factory(self):
            return None

        def get_successful_ghost_creation_count(self):
            return 0

        def lock_pending_recipients(self, _recipients):
            return None

        def mutate_transaction_fee_accounting(self, _hash, mutate, *, commit=True):
            transaction.data[FEE_ACCOUNTING_KEY] = mutate(
                transaction.data[FEE_ACCOUNTING_KEY]
            )
            return transaction.data[FEE_ACCOUNTING_KEY]

    transaction = SimpleNamespace(
        to_address="0x1111111111111111111111111111111111111111",
        execution_mode="NORMAL",
        hash="0x" + "12" * 32,
        config_rotation_rounds=0,
        sim_config=None,
        origin_address=None,
        data={FEE_ACCOUNTING_KEY: accounting},
    )
    return SimpleNamespace(
        transaction=transaction,
        transactions_processor=_Processor(),
    )


def _empty_success_receipt():
    return SimpleNamespace(
        execution_result=ExecutionResultStatus.SUCCESS,
        pending_transactions=[],
        node_config={"address": "0x" + "01" * 20, "private_key": "0x" + "02" * 32},
    )


def test_acceptance_dispatch_completes_without_a_helper_rollup():
    """An empty acceptance phase commits through Studio's local authority."""
    calls = []
    context = _acceptance_dispatch_context(calls)

    assert (
        _dispatch_messages_for_phase(context, _empty_success_receipt(), "accepted")
        is True
    )
    assert calls == []
    generation = context.transaction.data[FEE_ACCOUNTING_KEY][
        "active_message_generation"
    ]
    assert generation["acceptanceDispatched"] is True


def test_child_rotation_clamp_tolerates_a_null_parent_schedule():
    """transactions.config_rotation_rounds is nullable.

    int(None) raised TypeError for every triggered child, which the worker
    retried until it cancelled the parent.
    """
    assert _child_config_rotation_rounds(None, {}) >= 0
    assert (
        _child_config_rotation_rounds(
            None, {"fees_distribution": _fees_distribution(rotations=[2])}
        )
        == 2
    )
    # A known parent schedule still clamps the funded one.
    assert (
        _child_config_rotation_rounds(
            1, {"fees_distribution": _fees_distribution(rotations=[5])}
        )
        == 1
    )


def test_triggered_child_insert_survives_a_parent_without_a_rotation_schedule():
    """End-to-end over _emit_messages, the frame that actually crashed."""
    calls = []
    context = _rollup_free_context(calls)
    context.transaction.config_rotation_rounds = None
    child = (
        "0x2222222222222222222222222222222222222222",
        {},
        TransactionType.RUN_CONTRACT.value,
        0,
        0,
    )

    _emit_messages(context, [child], None, "finalized", rollup_skipped=True)

    assert len(calls) == 1
    assert isinstance(calls[0][1]["config_rotation_rounds"], int)


def test_transaction_payload_exposes_only_consensus_child_transactions():
    transaction = _processor_transaction()
    transaction.triggered_transactions = [
        SimpleNamespace(hash="0x" + "01" * 32, type=TransactionType.SEND.value),
        SimpleNamespace(
            hash="0x" + "02" * 32,
            type=TransactionType.RUN_CONTRACT.value,
        ),
        SimpleNamespace(
            hash="0x" + "03" * 32,
            type=TransactionType.DEPLOY_CONTRACT.value,
        ),
    ]

    parsed = TransactionsProcessor._parse_transaction_data(transaction)

    assert parsed["triggered_transactions"] == [
        "0x" + "02" * 32,
        "0x" + "03" * 32,
    ]
