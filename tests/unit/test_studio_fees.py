import base64
from datetime import datetime
import json
from types import SimpleNamespace

import pytest
import rlp
from eth_abi import encode

from backend.consensus.base import (
    _apply_external_message_freeze_check,
    _apply_message_value_withdrawals_for_phase,
    _get_messages_data,
)
from backend.domain.types import TransactionType
from backend.errors.errors import InvalidTransactionError
from backend.database_handler.accounts_manager import _infer_final_round
from backend.database_handler.transactions_processor import TransactionsProcessor
from backend.protocol_rpc.exceptions import JSONRPCError
from backend.protocol_rpc.endpoints import (
    _current_fee_round,
    _handle_appeal_or_top_up_and_submit,
    _handle_top_up_fees,
    get_transaction_status,
    _stage_simulated_call_value,
    _simulation_fee_accounting,
    _validate_fee_envelope,
    _with_default_simulation_fees,
    sim_estimate_transaction_fees,
)
from backend.protocol_rpc.fees import (
    AllocationDuplicateKey,
    AllocationLifecycleBudgetInsufficient,
    AllocationSubtreeMismatch,
    AllocationTreeBudgetInconsistent,
    AllocationTreeMalformed,
    AllocationTreeTooDeep,
    BudgetTooLow,
    ExternalAllocationInvalid,
    InsufficientFees,
    InvalidAppealBond,
    InvalidAppealRounds,
    InvalidFeeParams,
    InvalidNumOfValidators,
    MaxPriceExceeded,
    MessageBudgetExceeded,
    MessageDeclaredBudgetInsufficient,
    MessageEmissionPhaseMismatch,
    MessageFeeParamsMismatch,
    MessageAllocationsNotEqualBudget,
    MessageFeesReportMismatch,
    MessageNoMatchingAllocation,
    Mode1MessageFeesRequireGenVMPerEmissionSupport,
    CALL_KEY_WILDCARD,
    MESSAGE_ALLOCATION_NODE_ABI_TYPE,
    MIN_RECEIPT_BYTES,
    MESSAGE_REVEAL_LENGTH_SLOTS,
    NODE_ROOT_SENTINEL,
    NONDET_OUTPUT_LENGTH_BYTES,
    FEE_ACCOUNTING_KEY,
    PROPOSE_RECEIPT_SLOTS,
    SUBMITTED_MESSAGE_ABI_TYPE,
    DEFAULT_GEN_PER_TIME_UNIT,
    DEFAULT_PRICE_CAP_HEADROOM_BPS,
    DEFAULT_RECEIPT_GAS_PRICE,
    DEFAULT_STORAGE_UNIT_PRICE,
    DEFAULT_TRANSACTION_EXECUTION_BUDGET_PER_ROUND,
    GENVM_UNMETERED_DATA_FEE_BUCKET,
    StudioFeePolicy,
    TooManyMessages,
    apply_fee_top_up,
    calculate_min_appeal_bond,
    calculate_round_fees,
    calculate_time_unit_fees_through_round,
    consume_message_fees,
    create_child_fee_accounting,
    create_fee_accounting,
    cancel_fee_accounting,
    default_transaction_fees_for_policy,
    derive_external_message_call_key,
    fill_message_fee_payload_from_allocation,
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
    studio_fee_config,
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


def _required_env_fee_deposit(fees_distribution, num_of_validators=5):
    return required_fee_deposit(
        fees_distribution,
        num_of_validators,
        StudioFeePolicy.from_env(),
    )


def _encode_internal_fee_params(
    *,
    leader_timeunits=5,
    validator_timeunits=10,
    appeals=0,
    execution_budget_per_round=0,
    rotations=None,
):
    if rotations is None:
        rotations = [0] * (appeals + 1)
    return encode(
        ["(uint256,uint256,uint256,uint256,uint256[])"],
        [
            (
                leader_timeunits,
                validator_timeunits,
                appeals,
                execution_budget_per_round,
                rotations,
            )
        ],
    )


def _encode_external_fee_params(*, gas_limit=21_000, max_gas_price=10):
    return encode(["(uint256,uint256)"], [(gas_limit, max_gas_price)])


def _external_selector_call_key(selector: bytes) -> str:
    return "0x" + selector.hex().ljust(64, "0")


def _root_parent_index() -> int:
    return NODE_ROOT_SENTINEL


def test_derive_external_message_call_key_preserves_explicit_value():
    explicit = "0x" + "99" * 32
    assert derive_external_message_call_key(explicit, b"\x12\x34\x56\x78") == explicit


def test_derive_external_message_call_key_from_calldata_selector_when_omitted():
    selector = b"\x12\x34\x56\x78"
    assert derive_external_message_call_key(
        CALL_KEY_WILDCARD,
        selector + b"payload",
    ) == _external_selector_call_key(selector)


def test_derive_external_message_call_key_keeps_wildcard_without_selector():
    assert derive_external_message_call_key(CALL_KEY_WILDCARD, b"\x12\x34") == (
        CALL_KEY_WILDCARD
    )


def _allocation(
    *,
    message_type=1,
    on_acceptance=True,
    parent_index=NODE_ROOT_SENTINEL,
    recipient="0x2222222222222222222222222222222222222222",
    call_key="0x0000000000000000000000000000000000000000000000000000000000000000",
    budget=55,
    fee_params=None,
):
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
        (23, 1, [0, 0], 19300),
        (23, 2, [0, 2, 4], 143700),
        (1537, 0, [0], 307500),
    ],
)
def test_calculate_round_fees_matches_consensus_budget_cases(
    validators, appeals, rotations, expected
):
    fees_distribution = _fees_distribution(appeals=appeals, rotations=rotations)

    assert calculate_round_fees(fees_distribution, validators) == expected


def test_calculate_round_fees_rejects_invalid_validator_count():
    fees_distribution = _fees_distribution()

    with pytest.raises(InvalidNumOfValidators):
        calculate_round_fees(fees_distribution, 6)


def test_calculate_round_fees_rejects_invalid_appeal_rounds():
    fees_distribution = _fees_distribution(appeals=2, rotations=[1])

    with pytest.raises(InvalidAppealRounds):
        calculate_round_fees(fees_distribution, 5)


def test_calculate_round_fees_applies_gen_per_time_unit_multiplier():
    fees_distribution = _fees_distribution()
    policy = StudioFeePolicy(gen_per_time_unit=10)

    assert calculate_round_fees(fees_distribution, 5, policy=policy) == 11000
    assert (
        calculate_round_fees(
            _fees_distribution(execution_budget_per_round=50),
            5,
            policy=policy,
        )
        == 11050
    )


def test_calculate_round_fees_rejects_price_caps():
    with pytest.raises(MaxPriceExceeded):
        calculate_round_fees(
            _fees_distribution(max_price_gen_per_time_unit=5),
            5,
            policy=StudioFeePolicy(gen_per_time_unit=10),
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
    assert calculate_round_fees(fees_distribution, 5) == 10900


def test_time_unit_fees_through_round_refunds_unused_appeal_budget():
    fees_distribution = _fees_distribution(appeals=2, rotations=[0, 0, 0])

    assert get_leader_rounds_through_round(fees_distribution, 0) == 1
    assert calculate_time_unit_fees_through_round(fees_distribution, 5, 0) == 1100
    assert calculate_round_fees(fees_distribution, 5) == 12300


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

    policy = StudioFeePolicy.from_env()

    assert policy.gen_per_time_unit == DEFAULT_GEN_PER_TIME_UNIT
    assert policy.storage_unit_price == DEFAULT_STORAGE_UNIT_PRICE
    assert policy.receipt_gas_price == DEFAULT_RECEIPT_GAS_PRICE
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
        policy.intrinsic_gas
        + policy.bootloader_overhead
        + (MIN_RECEIPT_BYTES * policy.calldata_gas_per_byte)
        + (PROPOSE_RECEIPT_SLOTS * policy.gas_per_changed_slot)
    )
    expected_genvm_bucket_floor_gas = (
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
        policy.intrinsic_gas
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
        == expected_receipt_floor_gas
    )
    assert policy.estimate_message_reveal_gas(320, 2) == expected_message_reveal_gas
    assert (
        policy.estimate_consensus_message_reveal_gas(320, 2)
        == expected_consensus_message_reveal_gas
    )
    assert policy.estimate_nondet_output_start_gas() == expected_nondet_output_start_gas
    assert (
        policy.message_fee_params_budget_floor() == expected_genvm_bucket_floor_gas * 3
    )


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
    assert distribution["maxPriceGenPerTimeUnit"] == (
        DEFAULT_GEN_PER_TIME_UNIT * DEFAULT_PRICE_CAP_HEADROOM_BPS // 10_000
    )
    assert distribution["storageFeeMaxGasPrice"] == 2
    assert distribution["receiptFeeMaxGasPrice"] == 2
    assert (
        fee_value
        == (1100 * DEFAULT_GEN_PER_TIME_UNIT) + distribution["executionBudgetPerRound"]
    )

    config = studio_fee_config(policy)
    assert config["enabled"] is True
    assert config["policy"]["fixedProposeReceiptGas"] == "210000"
    assert config["policy"]["fixedMessageRevealGas"] == "100000"
    assert config["policy"]["receiptWrapperBytes"] == "1024"
    assert config["policy"]["messageFeeParamsBudgetFloor"] == str(
        policy.message_fee_params_budget_floor()
    )
    assert config["capabilities"]["messageFees"]["mode1"] == {
        "accounting": True,
        "genvmExecution": False,
    }
    assert config["capabilities"]["messageFees"]["mode2"]["genvmExecution"] is True
    assert config["defaultFees"]["distribution"]["maxPriceGenPerTimeUnit"] == str(
        DEFAULT_GEN_PER_TIME_UNIT * DEFAULT_PRICE_CAP_HEADROOM_BPS // 10_000
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


def test_endpoint_fee_envelope_rejects_insufficient_fee_deposit():
    fees_distribution = _fees_distribution(total_message_fees=55)
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
    fees_distribution = _fees_distribution(total_message_fees=55)
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


def test_simulation_fee_accounting_accepts_sdk_style_fee_options():
    policy = StudioFeePolicy.from_env()
    fees_distribution = _fees_distribution(
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
    fee_params = _encode_internal_fee_params()
    policy = StudioFeePolicy.from_env()
    budget = calculate_round_fees(
        _fees_distribution(leader_timeunits=5, validator_timeunits=10),
        5,
        policy=policy,
    )
    allocation = _allocation(budget=budget, fee_params=fee_params)
    fees_distribution = _fees_distribution(total_message_fees=budget)

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
            "callKey": "0x0000000000000000000000000000000000000000000000000000000000000000",
            "budget": budget,
            "feeParams": "0x" + fee_params.hex(),
        }
    ]


def test_simulation_fee_accounting_defaults_to_required_deposit():
    fees_distribution = _fees_distribution(total_message_fees=55)
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


def test_simulation_fee_accounting_rejects_insufficient_sdk_fee_value():
    with pytest.raises(JSONRPCError, match="InsufficientFees"):
        _simulation_fee_accounting(
            {
                "fees": {
                    "distribution": _fees_distribution(total_message_fees=55),
                    "feeValue": 1154,
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
    assert "_allow_low_execution_budget_for_estimate" not in params
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
        leader_timeunits=5,
        validator_timeunits=10,
        execution_budget_per_round=execution_budget,
    )
    message_budget = calculate_round_fees(
        _fees_distribution(
            leader_timeunits=5,
            validator_timeunits=10,
            execution_budget_per_round=execution_budget,
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
    fees_distribution = _fees_distribution(
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
        leader_timeunits=6,
        validator_timeunits=10,
    )
    message_budget = calculate_round_fees(
        _fees_distribution(
            leader_timeunits=6,
            validator_timeunits=10,
        ),
        5,
        policy=policy,
    )
    expected_padded_message_budget = (
        message_budget * DEFAULT_PRICE_CAP_HEADROOM_BPS + 9_999
    ) // 10_000
    recipient = "0x2222222222222222222222222222222222222222"
    fees_distribution = _fees_distribution(
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
        "leaderTimeunitsAllocation": 6,
        "validatorTimeunitsAllocation": 10,
        "appealRounds": 0,
        "executionBudgetPerRound": 0,
        "rotations": [0],
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
    fees_distribution = _fees_distribution(total_message_fees=message_budget)
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
    expected_reservation = gas_limit * policy.receipt_gas_price
    expected_reimbursement = 11 * policy.receipt_gas_price

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
        _allocation(budget=55, call_key=CALL_KEY_WILDCARD),
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


def test_message_allocations_accept_valid_external_allocation():
    allocation = _allocation(
        message_type=0,
        budget=210_000,
        fee_params=_encode_external_fee_params(gas_limit=21_000, max_gas_price=10),
    )

    validate_message_allocations([allocation], total_message_fees=210_000)


def test_message_allocations_reject_invalid_external_allocation():
    allocation = _allocation(
        message_type=0,
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
                    call_key=CALL_KEY_WILDCARD,
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

    assert bucket_totals == [123, 123, 0]
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
        GENVM_UNMETERED_DATA_FEE_BUCKET,
        55,
    ]


def test_genvm_message_fee_allocation_maps_studio_nodes():
    fee_params = _encode_internal_fee_params(leader_timeunits=6)
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=60),
        message_allocations=[
            _allocation(
                budget=60,
                fee_params=fee_params,
                recipient="0x2222222222222222222222222222222222222222",
                call_key="0x" + "12" * 32,
            )
        ],
        num_of_validators=5,
        submitted_value=1160,
        user_value=0,
    )

    allocations = genvm_message_fee_allocation(accounting)

    assert allocations[0] == {
        "message_type": "InternalAccepted",
        "parent_index": None,
        "recipient": "0x2222222222222222222222222222222222222222",
        "call_key": bytes.fromhex("12" * 32),
        "budget": 60,
        "fee_params": {
            "leader_timeunits_allocation": 6,
            "validator_timeunits_allocation": 10,
            "execution_budget_per_round": 0,
            "rotations": [0],
        },
    }
    assert allocations[1] == {
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


def test_genvm_message_fee_allocation_exposes_only_current_roots():
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

    assert len(allocations) == 2
    assert allocations[0]["recipient"] == root["recipient"]
    assert allocations[0]["call_key"] == bytes.fromhex("12" * 32)
    assert allocations[0]["budget"] == 120
    assert allocations[1]["message_type"] == "External"
    assert allocations[1]["recipient"] is None
    assert allocations[1]["call_key"] is None


def test_genvm_message_fee_allocation_adds_external_legacy_fallback_after_roots():
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

    assert allocations[0]["message_type"] == "External"
    assert allocations[0]["recipient"] == external["recipient"]
    assert allocations[0]["call_key"] == bytes.fromhex(
        _external_selector_call_key(b"\x12\x34\x56\x78").removeprefix("0x")
    )
    assert allocations[0]["budget"] == 210_000
    assert allocations[1]["message_type"] == "External"
    assert allocations[1]["recipient"] is None
    assert allocations[1]["call_key"] is None
    assert allocations[1]["budget"] == 2**200


def test_genvm_message_fee_allocation_keeps_legacy_gasless_messages_unmetered():
    allocations = genvm_message_fee_allocation(None)

    assert [node["message_type"] for node in allocations] == [
        "External",
        "InternalFinalized",
        "InternalAccepted",
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

    assert bucket_totals == [execution_budget, execution_budget, 0]
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
    expected_subtree = encode(
        [MESSAGE_ALLOCATION_NODE_ABI_TYPE],
        [
            [
                (
                    1,
                    True,
                    NODE_ROOT_SENTINEL,
                    recipient,
                    bytes(32),
                    75,
                    fee_params,
                )
            ]
        ],
    )

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
    assert message["allocationSubtree"] == "0x" + expected_subtree.hex()
    assert message["allocationSubtreeBytes"] == len(expected_subtree)
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
        fees_distribution=_fees_distribution(
            leader_timeunits=0,
            validator_timeunits=0,
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


def test_simulation_fee_report_fills_missing_subtree_for_prepopulated_mode2_message():
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
    expected_subtree = encode(
        [MESSAGE_ALLOCATION_NODE_ABI_TYPE],
        [
            [
                (
                    1,
                    True,
                    NODE_ROOT_SENTINEL,
                    recipient,
                    bytes.fromhex("12" * 32),
                    110,
                    fee_params,
                ),
                (
                    1,
                    True,
                    0,
                    child_recipient,
                    bytes.fromhex("34" * 32),
                    55,
                    child_fee_params,
                ),
            ]
        ],
    )

    assert recorded["message_fee_consumed"] == 110
    assert recorded["allocation_consumed"] == {"0": 110}
    assert message["messageFeeMode"] == "mode2"
    assert message["allocationSubtree"] == "0x" + expected_subtree.hex()
    assert message["allocationSubtreeBytes"] == len(expected_subtree)


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

    assert recorded["message_fee_consumed"] == 77
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
        }
    ]
    assert recorded["execution_fee_report"]["messageFees"] == {
        "budget": 1_000,
        "declaredConsumed": 0,
        "genvmMeteredConsumed": 0,
        "declaredRefunded": 0,
        "remaining": 923,
        "meteringDelta": 0,
        "externalReserved": 700,
        "externalReimbursed": 77,
        "externalRemainder": 623,
        "totalConsumed": 77,
    }
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

    assert refunded["message_fee_consumed"] == 77
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
            },
            "feeParamsBytes": len(fee_params),
            "declaredBudget": 55,
            "allocationSubtree": "0x" + expected_subtree.hex(),
            "allocationSubtreeBytes": len(expected_subtree),
            "callKey": "0x" + "34" * 32,
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
    assert preset["distribution"]["executionBudgetPerRound"] == floor + 10_000
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
            },
            "feeParamsBytes": len(fee_params_a),
            "declaredBudget": 60,
            "allocationSubtree": "0x",
            "allocationSubtreeBytes": 0,
            "callKey": "0x" + "12" * 32,
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
            },
            "feeParamsBytes": len(fee_params_b),
            "declaredBudget": 70,
            "allocationSubtree": "0x",
            "allocationSubtreeBytes": 0,
            "callKey": "0x" + "34" * 32,
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


def test_settle_fee_accounting_caps_execution_spend_to_actual_round_budget():
    fees_distribution = _fees_distribution(
        appeals=2,
        rotations=[0, 0, 0],
        execution_budget_per_round=100,
    )
    accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=12800,
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
    assert settled["primary_fee_spent"] == 1200
    assert refund == 11600


def test_settle_fee_accounting_uses_actual_round_for_primary_refund():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(appeals=2, rotations=[0, 0, 0]),
        num_of_validators=5,
        submitted_value=12300,
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
    assert refund == 11200


def test_settle_fee_accounting_charges_half_leader_allocation_for_timeout_round():
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

    assert settled["primary_fee_spent"] == 1050
    assert refund == 50


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


def test_mode2_message_fees_use_exact_then_wildcard_allocation_match():
    exact_call_key = "0x" + "12" * 32
    wildcard_call_key = "0x" + "0" * 32
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
    assert len(message["allocationSubtree"]) == 2
    assert message["allocationSubtree"][0]["parentIndex"] == NODE_ROOT_SENTINEL
    assert message["allocationSubtree"][0]["budget"] == 111
    assert message["allocationSubtree"][1]["parentIndex"] == 0
    assert message["allocationSubtree"][1]["budget"] == 56


def test_fill_message_fee_payload_rejects_mismatched_prepopulated_subtree():
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

    with pytest.raises(AllocationSubtreeMismatch):
        fill_message_fee_payload_from_allocation(
            accounting,
            {
                "messageType": 1,
                "recipient": recipient,
                "onAcceptance": True,
                "declaredBudget": 111,
                "feeParams": fee_params,
                "callKey": "0x" + "0" * 64,
                "allocationSubtree": [
                    _allocation(
                        recipient=recipient,
                        budget=111,
                        fee_params=fee_params,
                    )
                ],
            },
        )


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


def test_mode2_external_message_fees_use_legacy_fallback_without_matching_allocation():
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

    updated = consume_message_fees(
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

    assert updated["message_fee_consumed"] == 0
    assert updated["allocation_consumed"] == {}
    assert updated["external_message_events"] == []
    assert updated["message_consumption_events"][0] == {
        "consumed": 0,
        "internalConsumed": 0,
        "externalReimbursed": 0,
        "remaining": 55,
    }


def test_mode2_external_message_fees_ignore_on_acceptance_execution_reservation():
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
                "onAcceptance": True,
                "declaredBudget": 0,
                "callKey": allocation["callKey"],
                "gasUsed": 1_000,
            }
        ],
        policy=StudioFeePolicy(receipt_gas_price=7),
    )

    assert updated["message_fee_consumed"] == 0
    assert updated["allocation_consumed"] == {}
    assert updated["external_message_fee_reserved"] == 0
    assert updated["external_message_events"] == []


def test_external_message_execution_reservation_ignores_allocation_phase_pin():
    allocation = _allocation(
        message_type=0,
        on_acceptance=True,
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
        policy=StudioFeePolicy(receipt_gas_price=7),
    )

    assert updated["allocation_consumed"] == {"0": 700}
    assert updated["external_message_fee_reserved"] == 700
    assert updated["external_message_fee_reimbursed"] == 420
    assert updated["external_message_fee_remainder"] == 280
    assert updated["message_fee_consumed"] == 420


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
            call_key="0x" + "0" * 32,
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
    assert updated["message_fee_consumed"] == 210


def test_consume_message_fees_rejects_internal_message_without_internal_allocation():
    fee_params = _encode_internal_fee_params()
    recipient = "0x2222222222222222222222222222222222222222"
    call_key = "0x" + "12" * 32
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(total_message_fees=210_000),
        message_allocations=[
            _allocation(
                message_type=0,
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
    assert updated["message_fee_consumed"] == 7_000

    settled, refund = settle_fee_accounting(updated)
    assert refund == 203_000
    assert settled["message_fee_refunded"] == 203_000


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

    assert executed["message_fee_consumed"] == 420
    assert executed["external_message_fee_reserved"] == 700
    assert executed["external_message_fee_reimbursed"] == 420
    assert executed["external_message_fee_remainder"] == 280
    assert executed["external_message_events"][0]["executionRecorded"] is True
    assert executed["external_message_events"][0]["gasUsed"] == 60
    assert executed_again == executed


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
    assert refunded["message_fee_consumed"] == 7_000
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
    assert refund == 203_000
    assert settled["message_fee_refunded"] == 203_000


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
            call_key="0x" + "0" * 32,
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
    assert refunded["message_fee_consumed"] == 210
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
    consumed = consume_message_fees(
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
    assert unwound["allocation_consumed"] == {"0": 0, "1": 0}
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
            "externalRemainderRolledBack": 700,
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
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(),
        num_of_validators=5,
        submitted_value=1100,
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
    assert updated["primary_fee_budget"] == 1100
    assert updated["top_ups"] == accounting["top_ups"]
    assert updated["appeal_bonds"][0]["minimumRequired"] == 1400


def test_settle_fee_accounting_pays_successful_appeal_bond_plus_profit():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(appeals=1, rotations=[0, 0]),
        num_of_validators=5,
        submitted_value=4900,
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
            {"consensus_round": "Leader Appeal Successful"},
        ]
    }

    settled, refund = settle_fee_accounting(
        recorded,
        actual_final_round=1,
        num_of_validators=5,
        consensus_history=consensus_history,
    )

    assert refund == 2300
    assert settled["appeal_bonds_payout_total"] == 2100
    assert settled["appeal_bond_settlements"] == [
        {
            "bondIndex": 0,
            "appealer": "0x1111111111111111111111111111111111111111",
            "amount": 1400,
            "round": 0,
            "status": "successful",
            "payout": 2100,
            "outcomeRound": 1,
            "outcome": "Leader Appeal Successful",
        }
    ]


def test_settle_fee_accounting_explicitly_forfeits_failed_appeal_bond():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(appeals=1, rotations=[0, 0]),
        num_of_validators=5,
        submitted_value=4900,
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
            {"consensus_round": "Leader Appeal Failed"},
        ]
    }

    settled, refund = settle_fee_accounting(
        recorded,
        actual_final_round=1,
        num_of_validators=5,
        consensus_history=consensus_history,
    )

    assert refund == 2300
    assert settled["appeal_bonds_payout_total"] == 0
    assert settled["appeal_bond_settlements"][0]["status"] == "forfeited"
    assert settled["appeal_bond_settlements"][0]["bond_forfeited"] == 1400


def test_cancel_fee_accounting_returns_unadjudicated_appeal_bond():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(),
        num_of_validators=5,
        submitted_value=1100,
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

    assert refund == 1100
    assert canceled["appeal_bonds_payout_total"] == 1400
    assert canceled["appeal_bond_settlements"][0]["status"] == "returned"
    assert canceled["appeal_bond_settlements"][0]["payout"] == 1400


def test_top_up_and_submit_appeal_skips_generic_top_up_bookkeeping():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(
            max_price_gen_per_time_unit=100,
            storage_fee_max_gas_price=80,
            receipt_fee_max_gas_price=60,
        ),
        num_of_validators=5,
        submitted_value=1100,
        user_value=0,
    )

    updated = record_appeal_bond(
        accounting,
        amount=1400,
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

    assert updated["paid_fee_value"] == accounting["paid_fee_value"] + 1400
    assert updated["primary_fee_budget"] == accounting["primary_fee_budget"] + 1400
    assert updated["top_ups"] == accounting["top_ups"]
    assert updated["message_fee_budget"] == accounting["message_fee_budget"]
    assert updated["fees_distribution"]["appealRounds"] == 1
    assert updated["fees_distribution"]["totalMessageFees"] == 0
    assert updated["fees_distribution"]["maxPriceGenPerTimeUnit"] == 100
    assert updated["fees_distribution"]["storageFeeMaxGasPrice"] == 80
    assert updated["fees_distribution"]["receiptFeeMaxGasPrice"] == 60
    assert updated["appeal_bonds"][0]["topUpAndSubmit"] is True
    assert updated["appeal_bonds"][0]["feesDistributionIgnored"] is True


def test_accepted_appeal_bond_uses_active_timeunit_price():
    bond = calculate_min_appeal_bond(
        _fees_distribution(),
        current_round=0,
        status="ACCEPTED",
        policy=StudioFeePolicy(gen_per_time_unit=10**15),
    )

    assert bond == 1400 * 10**15


def test_timeout_appeal_bond_bypasses_stale_max_price_cap():
    policy = StudioFeePolicy(gen_per_time_unit=100)
    fees_distribution = _fees_distribution(
        appeals=2,
        rotations=[0, 1, 1],
        max_price_gen_per_time_unit=10,
    )

    with pytest.raises(MaxPriceExceeded):
        calculate_round_fees(fees_distribution, 5, policy=policy)

    bond = calculate_min_appeal_bond(
        fees_distribution,
        current_round=0,
        status="LEADER_TIMEOUT",
        policy=policy,
    )

    assert bond == 230_000


def test_top_up_and_submit_appeal_bypasses_stale_cap_without_rewriting_policy():
    submission_policy = StudioFeePolicy(gen_per_time_unit=1)
    fees_distribution = _fees_distribution(
        appeals=2,
        rotations=[0, 1, 1],
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

    assert updated["primary_fee_budget"] == accounting["primary_fee_budget"] + 2_300
    assert updated["fees_distribution"]["appealRounds"] == 3
    assert updated["fees_distribution"]["maxPriceGenPerTimeUnit"] == 10
    assert updated["appeal_bonds"][0]["minimumRequired"] == 2_300
    assert updated["appeal_bonds"][0]["feesDistributionIgnored"] is True


def test_top_up_and_submit_appeal_only_bumps_appeal_capacity_and_fee_pot():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(),
        num_of_validators=5,
        submitted_value=1100,
        user_value=0,
    )

    updated = record_appeal_bond(
        accounting,
        amount=1400,
        appealer="0x1111111111111111111111111111111111111111",
        current_round=0,
        status="ACCEPTED",
        fees_distribution=_fees_distribution(total_message_fees=55),
        top_up_and_submit=True,
    )

    assert updated["primary_fee_budget"] == 2500
    assert updated["fees_distribution"]["appealRounds"] == 1
    assert updated["fees_distribution"]["totalMessageFees"] == 0
    assert updated["message_fee_budget"] == 0
    assert updated["appeal_bonds"][0]["feesDistributionIgnored"] is True


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

    updated = record_appeal_bond(
        recorded,
        amount=1_400,
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


def test_create_child_fee_accounting_refunds_extra_leaf_budget_without_child_allocations():
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

    assert child_fees["totalMessageFees"] == 0
    assert child_fees["maxPriceGenPerTimeUnit"] == 999
    assert child_fees["storageFeeMaxGasPrice"] == 888
    assert child_fees["receiptFeeMaxGasPrice"] == 777
    assert child_accounting["paid_fee_value"] == 70
    assert child_accounting["primary_fee_budget"] == 70
    assert child_accounting["message_fee_budget"] == 0
    assert child_accounting["message_allocations"] == []
    assert child_accounting["user_value"] == 7


def test_create_child_fee_accounting_validates_primary_before_inherited_caps():
    policy = StudioFeePolicy(
        gen_per_time_unit=2,
        storage_unit_price=3,
        receipt_gas_price=4,
    )
    fee_params = _encode_internal_fee_params(
        leader_timeunits=5,
        validator_timeunits=10,
    )
    child_primary = calculate_round_fees(
        _fees_distribution(
            leader_timeunits=5,
            validator_timeunits=10,
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

    assert child_fees["maxPriceGenPerTimeUnit"] == 1
    assert child_fees["storageFeeMaxGasPrice"] == 2
    assert child_fees["receiptFeeMaxGasPrice"] == 3
    assert child_fees["totalMessageFees"] == 0
    assert child_accounting["primary_fee_required"] == child_primary
    assert child_accounting["primary_fee_budget"] == child_primary
    assert child_accounting["paid_fee_value"] == child_primary
    assert child_accounting["policy_snapshot"]["gen_per_time_unit"] == 2
    assert child_accounting["user_value"] == 7


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
    def __init__(self):
        self.updated_fee_accounting = None
        self.updated_hash = None

    def get_transaction_count(self, address):
        return 3

    def update_transaction_fee_accounting(self, tx_hash, accounting):
        self.updated_hash = tx_hash
        self.updated_fee_accounting = accounting


def _message_dispatch_context(accounting):
    processor = _MessageDispatchTxProcessor()
    tx = SimpleNamespace(
        hash="0x" + "ab" * 32,
        to_address="0x9999999999999999999999999999999999999999",
        from_address="0x1111111111111111111111111111111111111111",
        origin_address="0x1111111111111111111111111111111111111111",
        data={FEE_ACCOUNTING_KEY: accounting},
        status=None,
    )
    return SimpleNamespace(transaction=tx, transactions_processor=processor), processor


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


class _ExternalFreezeQuery:
    def __init__(self, *, scalar_value=None, rows=None):
        self.scalar_value = scalar_value
        self.rows = rows or []

    def filter(self, *args):
        return self

    def scalar(self):
        return self.scalar_value

    def all(self):
        return self.rows


class _ExternalFreezeSession:
    def __init__(self, *, current_created_at=None, accepted_rows=None):
        self.current_created_at = current_created_at
        self.accepted_rows = accepted_rows or []
        self.query_count = 0

    def query(self, *args):
        self.query_count += 1
        if self.query_count == 1:
            return _ExternalFreezeQuery(scalar_value=self.current_created_at)
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


def _pending_internal_value(value, *, on="accepted", recipient=None):
    return PendingTransaction(
        address=recipient or "0x5555555555555555555555555555555555555555",
        calldata=b"\x12\x34",
        code=None,
        salt_nonce=0,
        on=on,
        value=value,
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


def test_message_dispatch_creates_mode1_child_fee_accounting_from_pending_metadata(
    monkeypatch,
):
    monkeypatch.setenv("GENLAYER_STUDIO_GEN_PER_TIME_UNIT", "1")
    monkeypatch.setenv("GENLAYER_STUDIO_STORAGE_UNIT_PRICE", "0")
    monkeypatch.setenv("GENLAYER_STUDIO_RECEIPT_GAS_PRICE", "0")
    policy = StudioFeePolicy.from_env()
    fee_params = _encode_internal_fee_params()
    fees_distribution = _fees_distribution(total_message_fees=55)
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
        declared_budget=55,
        call_key="0x" + "12" * 32,
    )

    internal_messages, inserts = _get_messages_data(context, [pending], "accepted")

    assert len(inserts) == 1
    recipient, data, tx_type, nonce, value = inserts[0]
    assert recipient == pending.address
    assert tx_type == TransactionType.RUN_CONTRACT.value
    assert nonce == 3
    assert value == 7
    assert data["calldata"] == b"\x12\x34"
    assert data["fee_value"] == 55
    assert data["user_value"] == 7
    assert data["fees_distribution"]["totalMessageFees"] == 0
    assert data[FEE_ACCOUNTING_KEY]["source"] == "internal_message"
    assert data[FEE_ACCOUNTING_KEY]["paid_fee_value"] == 55
    assert data[FEE_ACCOUNTING_KEY]["message_fee_budget"] == 0
    assert data["message_allocations_count"] == 0

    assert len(internal_messages) == 1
    serialized_child = json.loads(internal_messages[0]["data"])
    assert serialized_child["fee_value"] == 55
    assert serialized_child["user_value"] == 7
    assert serialized_child["calldata"] == base64.b64encode(b"\x12\x34").decode("utf-8")
    assert processor.updated_hash == context.transaction.hash
    assert processor.updated_fee_accounting["message_fee_consumed"] == 55
    assert processor.updated_fee_accounting["allocation_consumed"] == {}
    assert processor.updated_fee_accounting["message_consumption_events"][-1] == {
        "consumed": 55,
        "internalConsumed": 55,
        "externalReimbursed": 0,
        "remaining": 0,
    }


def test_message_dispatch_fills_mode2_child_fee_accounting_from_allocation_subtree(
    monkeypatch,
):
    monkeypatch.setenv("GENLAYER_STUDIO_GEN_PER_TIME_UNIT", "1")
    monkeypatch.setenv("GENLAYER_STUDIO_STORAGE_UNIT_PRICE", "0")
    monkeypatch.setenv("GENLAYER_STUDIO_RECEIPT_GAS_PRICE", "0")
    policy = StudioFeePolicy.from_env()
    root_fee_params = _encode_internal_fee_params(leader_timeunits=6)
    child_fee_params = _encode_internal_fee_params(leader_timeunits=7)
    recipient = "0x2222222222222222222222222222222222222222"
    child_recipient = "0x3333333333333333333333333333333333333333"
    root_call_key = "0x" + "12" * 32
    child_call_key = "0x" + "34" * 32
    root_budget = 113
    fees_distribution = _fees_distribution(total_message_fees=root_budget)
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
                budget=57,
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
    )

    _, inserts = _get_messages_data(context, [pending], "accepted")

    child_data = inserts[0][1]
    child_accounting = child_data[FEE_ACCOUNTING_KEY]
    assert child_data["fee_value"] == root_budget
    assert child_data["fees_distribution"]["leaderTimeunitsAllocation"] == 6
    assert child_data["fees_distribution"]["totalMessageFees"] == 57
    assert child_data["message_allocations_count"] == 1
    assert child_accounting["message_fee_budget"] == 57
    assert child_accounting["message_allocations"] == [
        {
            "messageType": 1,
            "onAcceptance": True,
            "parentIndex": NODE_ROOT_SENTINEL,
            "recipient": child_recipient,
            "callKey": child_call_key,
            "budget": 57,
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
    fees_distribution = _fees_distribution(total_message_fees=1_000)
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
    assert inserts[0] == [recipient, {}, TransactionType.SEND.value, 3, 0]
    assert json.loads(internal_messages[0]["data"]) == {}
    updated = processor.updated_fee_accounting
    assert updated["message_fees_recorded_at_reveal"] is True
    assert updated["allocation_consumed"] == {"0": 700}
    assert updated["external_message_fee_reserved"] == 700
    assert updated["external_message_fee_reimbursed"] == 420
    assert updated["external_message_fee_remainder"] == 280
    assert updated["message_fee_consumed"] == 420
    assert updated["external_message_events"][0]["executionRecorded"] is True
    assert updated["external_message_events"][0]["gasUsed"] == 60


def test_apply_fee_top_up_extends_budgets():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(),
        num_of_validators=5,
        submitted_value=1100,
        user_value=0,
    )

    updated = apply_fee_top_up(
        accounting,
        fees_distribution=_fees_distribution(total_message_fees=55),
        amount=1155,
        sender="0x1111111111111111111111111111111111111111",
        perform_fee_checks=False,
    )

    assert updated["paid_fee_value"] == 2255
    assert updated["primary_fee_budget"] == 2200
    assert updated["message_fee_budget"] == 55


def test_apply_fee_top_up_locks_timeunit_and_appeal_round_policy():
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

    updated = apply_fee_top_up(
        accounting,
        fees_distribution=incoming_distribution,
        amount=calculate_round_fees(incoming_distribution, 5),
    )

    assert updated["fees_distribution"]["leaderTimeunitsAllocation"] == 100
    assert updated["fees_distribution"]["validatorTimeunitsAllocation"] == 200
    assert updated["fees_distribution"]["appealRounds"] == 1
    assert updated["fees_distribution"]["rotations"] == [0, 0, 2, 2, 2, 2]


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
            fees_distribution=_fees_distribution(
                leader_timeunits=0,
                validator_timeunits=0,
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
            fees_distribution=_fees_distribution(),
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
        fees_distribution=_fees_distribution(
            leader_timeunits=0,
            validator_timeunits=0,
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
        fees_distribution=_fees_distribution(execution_budget_per_round=1),
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
            fees_distribution=_fees_distribution(execution_budget_per_round=1),
            amount=1101,
            policy=policy,
        )


def test_apply_fee_top_up_only_raises_existing_price_caps():
    accounting = create_fee_accounting(
        fees_distribution=_fees_distribution(
            max_price_gen_per_time_unit=100,
            storage_fee_max_gas_price=80,
            receipt_fee_max_gas_price=60,
        ),
        num_of_validators=5,
        submitted_value=1100,
        user_value=0,
    )

    unchanged = apply_fee_top_up(
        accounting,
        fees_distribution=_fees_distribution(
            leader_timeunits=0,
            validator_timeunits=0,
            max_price_gen_per_time_unit=90,
            storage_fee_max_gas_price=0,
            receipt_fee_max_gas_price=60,
        ),
        amount=0,
    )

    assert unchanged["fees_distribution"]["maxPriceGenPerTimeUnit"] == 100
    assert unchanged["fees_distribution"]["storageFeeMaxGasPrice"] == 80
    assert unchanged["fees_distribution"]["receiptFeeMaxGasPrice"] == 60

    raised = apply_fee_top_up(
        unchanged,
        fees_distribution=_fees_distribution(
            leader_timeunits=0,
            validator_timeunits=0,
            max_price_gen_per_time_unit=120,
            storage_fee_max_gas_price=85,
            receipt_fee_max_gas_price=70,
        ),
        amount=0,
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
        fees_distribution=_fees_distribution(
            leader_timeunits=0,
            validator_timeunits=0,
            max_price_gen_per_time_unit=120,
            storage_fee_max_gas_price=85,
            receipt_fee_max_gas_price=70,
        ),
        amount=0,
    )

    assert still_uncapped["fees_distribution"]["maxPriceGenPerTimeUnit"] == 0
    assert still_uncapped["fees_distribution"]["storageFeeMaxGasPrice"] == 0
    assert still_uncapped["fees_distribution"]["receiptFeeMaxGasPrice"] == 0


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

    def get_transaction_by_hash(self, tx_hash):
        if tx_hash != self.transaction["hash"]:
            return None
        return self.transaction

    def update_transaction_fee_accounting(self, tx_hash, fee_accounting):
        assert tx_hash == self.transaction["hash"]
        self.updated_fee_accounting = fee_accounting
        self.transaction["data"]["fee_accounting"] = fee_accounting

    def set_transaction_appeal(self, tx_hash, appeal):
        assert tx_hash == self.transaction["hash"]
        self.appeal_updates.append((tx_hash, appeal))
        self.transaction["appealed"] = appeal


def _decoded_top_up(tx_id, *, amount, fees_distribution=None):
    return DecodedRollupTransaction(
        from_address="0x1111111111111111111111111111111111111111",
        to_address="0x0000000000000000000000000000000000000000",
        data=DecodedTopUpFeesDataArgs(
            tx_id=tx_id,
            fees_distribution=fees_distribution or _fees_distribution(),
        ),
        type="2",
        nonce=0,
        value=amount,
    )


def _decoded_appeal(
    tx_id,
    *,
    amount,
    fees_distribution=None,
    top_up_and_submit=False,
):
    return DecodedRollupTransaction(
        from_address="0x1111111111111111111111111111111111111111",
        to_address="0x0000000000000000000000000000000000000000",
        data=DecodedsubmitAppealDataArgs(
            tx_id=tx_id,
            fees_distribution=fees_distribution,
            top_up_and_submit=top_up_and_submit,
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
    fees_distribution = fees_distribution or _fees_distribution()
    return create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5, policy),
        user_value=0,
        policy=policy,
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


@pytest.mark.parametrize(
    "status", ["ACCEPTED", "UNDETERMINED", "FINALIZED", "CANCELED"]
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


def test_submit_appeal_endpoint_records_bond_without_expanding_fee_pot():
    accounting = _env_fee_accounting()
    tx = _fee_accounted_tx(status="ACCEPTED", accounting=accounting)
    accounts = _FakeAccountsManager(balance=0)
    transactions = _FakeTransactionsProcessor(tx)
    appeal_bond = 1400 * DEFAULT_GEN_PER_TIME_UNIT

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
        decoded_rollup_transaction=_decoded_appeal(tx["hash"], amount=appeal_bond),
    )

    updated = transactions.updated_fee_accounting
    assert tx_id == tx["hash"]
    assert updated["appeal_bonds_total"] == appeal_bond
    assert updated["primary_fee_budget"] == accounting["primary_fee_budget"]
    assert updated["appeal_bonds"][0]["minimumRequired"] == appeal_bond
    assert updated["appeal_bonds"][0]["topUpAndSubmit"] is False
    assert transactions.appeal_updates == [(tx["hash"], True)]
    assert len(handler.events) == 1
    assert accounts.credits == [
        ("0x1111111111111111111111111111111111111111", appeal_bond)
    ]
    assert accounts.debits == [
        ("0x1111111111111111111111111111111111111111", appeal_bond)
    ]


def test_top_up_and_submit_appeal_endpoint_expands_capacity_only():
    accounting = _env_fee_accounting()
    tx = _fee_accounted_tx(status="ACCEPTED", accounting=accounting)
    transactions = _FakeTransactionsProcessor(tx)
    appeal_bond = 1400 * DEFAULT_GEN_PER_TIME_UNIT

    class _MsgHandler:
        def send_message(self, log_event, log_to_terminal=True):
            pass

    tx_id = _handle_appeal_or_top_up_and_submit(
        accounts_manager=_FakeAccountsManager(balance=0),
        transactions_processor=transactions,
        msg_handler=_MsgHandler(),
        decoded_rollup_transaction=_decoded_appeal(
            tx["hash"],
            amount=appeal_bond,
            fees_distribution=_fees_distribution(total_message_fees=55),
            top_up_and_submit=True,
        ),
    )

    updated = transactions.updated_fee_accounting
    assert tx_id == tx["hash"]
    assert updated["paid_fee_value"] == accounting["paid_fee_value"] + appeal_bond
    assert updated["primary_fee_budget"] == (
        accounting["primary_fee_budget"] + appeal_bond
    )
    assert updated["message_fee_budget"] == 0
    assert updated["fees_distribution"]["appealRounds"] == 1
    assert updated["fees_distribution"]["totalMessageFees"] == 0
    assert updated["appeal_bonds"][0]["topUpAndSubmit"] is True
    assert updated["appeal_bonds"][0]["feesDistributionIgnored"] is True
    assert transactions.appeal_updates == [(tx["hash"], True)]


def test_submit_appeal_endpoint_rejects_bond_below_required_minimum():
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
            decoded_rollup_transaction=_decoded_appeal(
                tx["hash"],
                amount=(1400 * DEFAULT_GEN_PER_TIME_UNIT) - 1,
            ),
        )

    assert transactions.updated_fee_accounting is None
    assert transactions.appeal_updates == []


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


def test_current_fee_round_ignores_leader_rotation_events():
    consensus_history = {
        "consensus_results": [
            {"consensus_round": "Accepted"},
            {"consensus_round": "Leader Rotation"},
            {"consensus_round": "Leader Rotation Appeal"},
            {"consensus_round": "Leader Appeal Failed"},
        ]
    }

    assert _current_fee_round(consensus_history) == 1
    assert _infer_final_round(consensus_history) == 1


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


def test_transaction_status_rpc_shape_includes_canonical_status_code():
    class _Processor:
        def get_transaction_status(self, transaction_hash):
            return TransactionsProcessor._status_payload("ACCEPTED")

    assert get_transaction_status(_Processor(), "0x1234") == {
        "status": "ACCEPTED",
        "statusCode": 5,
    }


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
    }


def test_transaction_payload_fees_null_when_fee_accounting_disabled():
    parsed = TransactionsProcessor._parse_transaction_data(_processor_transaction())

    assert parsed["fees"] is None
    assert parsed["txExecutionResult"] == 0
    assert parsed["txExecutionResultName"] == "NOT_VOTED"
