import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from eth_abi import encode

from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.domain.types import LLMProvider, Validator
from backend.node.base import Node, _SnapshotView
from backend.node.genvm.base import Context, ExecutionResult, ExecutionReturn
from backend.node.genvm.base import Host as GenVMHost
import backend.node.genvm.origin.calldata as gvm_calldata
from backend.node.genvm.origin.base_host import RunHostAndProgramRes
from backend.node.genvm.origin.public_abi import ResultCode
from backend.node.types import Address, ExecutionMode, ExecutionResultStatus
from backend.protocol_rpc.fees import (
    StudioFeePolicy,
    create_fee_accounting,
    required_fee_deposit,
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


class _StateProxyWithMetrics:
    def __init__(self, metrics: dict):
        self._metrics = metrics
        self.snapshot = SimpleNamespace(states={"accepted": {}})

    def get_metrics(self) -> dict:
        return self._metrics


def _make_node() -> Node:
    snapshot = MagicMock(spec=ContractSnapshot)
    snapshot.contract_address = "0x" + "ab" * 20
    snapshot.states = {"accepted": {}}
    snapshot.balance = 0

    validator = Validator(
        address="0x" + "12" * 20,
        stake=100,
        llmprovider=LLMProvider(
            provider="openai",
            model="gpt-4",
            config={},
            plugin="",
            plugin_config={},
        ),
    )

    manager = MagicMock()
    manager.url = "http://127.0.0.1:3999"

    node = Node(
        contract_snapshot=snapshot,
        validator_mode=ExecutionMode.LEADER,
        validator=validator,
        contract_snapshot_factory=lambda _addr: snapshot,
        manager=manager,
    )
    node._execution_finished = AsyncMock()
    return node


def test_host_provide_result_preserves_fee_metadata_from_genvm_emissions():
    state = _StateProxyWithMetrics({})
    host = GenVMHost(
        MagicMock(),
        calldata_bytes=b"",
        state_proxy=state,
        leader_results=None,
    )
    post_fee_params = _encode_internal_fee_params(leader_timeunits=6)
    post_fee_params_with_caps = encode(
        ["(uint256,uint256,uint256,uint256,uint256[],uint256,uint256,uint256)"],
        [(6, 10, 0, 0, [0], 11, 12, 13)],
    )
    deploy_fee_params = _encode_internal_fee_params(leader_timeunits=7)
    eth_send_fee_params = encode(["(uint256,uint256)"], [(21_000, 10)])
    allocation_subtree = [
        {
            "messageType": 1,
            "onAcceptance": True,
            "parentIndex": (1 << 256) - 1,
            "recipient": "0x" + "22" * 20,
            "callKey": "0x" + "12" * 32,
            "budget": 60,
            "feeParams": "0x" + post_fee_params.hex(),
        }
    ]
    genvm_subtree = encode(
        ["(uint8,bool,uint256,address,bytes32,uint256,bytes)[]"],
        [
            [
                (
                    1,
                    True,
                    (1 << 256) - 1,
                    "0x" + "22" * 20,
                    bytes.fromhex("12" * 32),
                    60,
                    post_fee_params_with_caps,
                )
            ]
        ],
    )
    res = RunHostAndProgramRes(
        stdout="",
        stderr="",
        genvm_log=[],
        execution_time=0,
        execution_hash=b"",
        result_kind=ResultCode.RETURN,
        result_data=b"ok",
        result_fingerprint=None,
        result_storage_changes=[],
        result_emissions=[
            {
                "type": "PostMessage",
                "address": Address("0x" + "22" * 20),
                "calldata": ["post", 1],
                "value": 7,
                "on": "accepted",
                "fee_params": {
                    "leader_timeunits_allocation": 6,
                    "validator_timeunits_allocation": 10,
                    "execution_budget_per_round": 0,
                    "rotations": [0],
                    "max_price_gen_per_time_unit": 11,
                    "storage_fee_max_gas_price": 12,
                    "receipt_fee_max_gas_price": 13,
                },
                "declaredBudget": 60,
                "callKey": bytes.fromhex("12" * 32),
                "subtree": genvm_subtree,
            },
            {
                "type": "DeployContract",
                "calldata": {"init": True},
                "code": b"class Child: pass",
                "salt_nonce": 9,
                "value": 0,
                "on": "finalized",
                "fee_params": "0x" + deploy_fee_params.hex(),
                "declared_budget": 70,
                "call_key": "0x" + "34" * 32,
                "allocation_subtree": allocation_subtree,
            },
            {
                "type": "EthSend",
                "address": Address("0x" + "44" * 20),
                "calldata": b"\xab\xcd",
                "value": 5,
                "fee_params": {
                    "gas_limit": 21_000,
                    "max_gas_price": 10,
                },
                "declaredBudget": 0,
                "callKey": bytes.fromhex("56" * 32),
                "allocationSubtree": [],
                "gasUsed": 123,
            },
        ],
        result_nondet_results=[],
        data_fees_remaining=[100, 90, 80],
    )

    execution = host.provide_result(res, state, Context())

    assert isinstance(execution.result, ExecutionReturn)
    post, deploy, eth_send = execution.pending_transactions
    assert post.address == "0x" + "22" * 20
    assert post.calldata == gvm_calldata.encode(["post", 1])
    assert post.value == 7
    assert post.on == "accepted"
    assert post.fee_params == post_fee_params
    assert post.declared_budget == 60
    assert post.call_key == "0x" + "12" * 32
    assert post.allocation_subtree == allocation_subtree

    assert deploy.address == "0x"
    assert deploy.calldata == gvm_calldata.encode({"init": True})
    assert deploy.code == b"class Child: pass"
    assert deploy.salt_nonce == 9
    assert deploy.on == "finalized"
    assert deploy.fee_params == deploy_fee_params
    assert deploy.declared_budget == 70
    assert deploy.call_key == "0x" + "34" * 32
    assert deploy.allocation_subtree == allocation_subtree

    assert eth_send.address == "0x" + "44" * 20
    assert eth_send.is_eth_send is True
    assert eth_send.calldata == b"\xab\xcd"
    assert eth_send.value == 5
    assert eth_send.on == "finalized"
    assert eth_send.fee_params == eth_send_fee_params
    assert eth_send.declared_budget == 0
    assert eth_send.call_key == "0x" + "56" * 32
    assert eth_send.gas_used == 123
    assert execution.data_fees_remaining == [100, 90, 80]


@pytest.mark.asyncio
async def test_state_proxy_metrics_use_executed_proxy_instance():
    node = _make_node()
    metrics = {
        "snapshot_cache_hits": 17,
        "snapshot_cache_misses": 3,
        "decoded_cache_hits": 9,
    }
    executed_state_proxy = _StateProxyWithMetrics(metrics)

    execution_result = ExecutionResult(
        result=ExecutionReturn(ret=b"\x00\x00"),
        eq_outputs={},
        pending_transactions=[],
        stdout="",
        stderr="",
        genvm_log=[],
        state=executed_state_proxy,
        processing_time=5,
        nondet_disagree=None,
        execution_stats={},
    )

    with patch(
        "backend.node.genvm.base.run_genvm_host",
        new_callable=AsyncMock,
        return_value=execution_result,
    ):
        receipt = await node._run_genvm(
            from_address="0x" + "de" * 20,
            calldata=b"\x00",
            readonly=False,
            is_init=False,
            transaction_hash="0xtx",
            transaction_datetime=None,
        )

    assert receipt.execution_stats is not None
    assert receipt.execution_stats["state_proxy"] == metrics


@pytest.mark.asyncio
async def test_run_genvm_receives_fee_context_from_transaction_accounting():
    node = _make_node()
    execution_result = ExecutionResult(
        result=ExecutionReturn(ret=b"\x00\x00"),
        eq_outputs={},
        pending_transactions=[],
        stdout="",
        stderr="",
        genvm_log=[],
        state=_StateProxyWithMetrics({}),
        processing_time=5,
        nondet_disagree=None,
        execution_stats={},
    )
    policy = StudioFeePolicy(receipt_gas_price=1)
    fees_distribution = {
        "leaderTimeunitsAllocation": 100,
        "validatorTimeunitsAllocation": 200,
        "appealRounds": 0,
        "executionBudgetPerRound": policy.message_fee_params_budget_floor(),
        "executionConsumed": 0,
        "totalMessageFees": 0,
        "rotations": [0],
        "maxPriceGenPerTimeUnit": 0,
        "storageFeeMaxGasPrice": 0,
        "receiptFeeMaxGasPrice": 0,
    }
    fee_accounting = create_fee_accounting(
        fees_distribution=fees_distribution,
        num_of_validators=5,
        submitted_value=required_fee_deposit(fees_distribution, 5, policy),
        user_value=0,
        policy=policy,
    )

    with patch(
        "backend.node.genvm.base.run_genvm_host",
        new_callable=AsyncMock,
        return_value=execution_result,
    ) as run_genvm_host:
        await node._run_genvm(
            from_address="0x" + "de" * 20,
            calldata=b"\x00",
            readonly=False,
            is_init=False,
            transaction_hash="0xtx",
            transaction_datetime=None,
            fee_accounting=fee_accounting,
        )

    fee_context = run_genvm_host.await_args.kwargs["fee_context"]
    assert fee_context.bucket_totals == [
        fees_distribution["executionBudgetPerRound"],
        fees_distribution["executionBudgetPerRound"],
        0,
    ]
    assert fee_context.gas_data["intrinsicGas"] == "21000"


@pytest.mark.asyncio
async def test_run_genvm_passes_mode2_message_fee_allocations_to_genvm():
    node = _make_node()
    fee_params = _encode_internal_fee_params(leader_timeunits=6)
    child_fee_params = _encode_internal_fee_params(leader_timeunits=7)
    recipient = "0x" + "cd" * 20
    child_recipient = "0x" + "ef" * 20
    call_key = "0x" + "34" * 32
    child_call_key = "0x" + "56" * 32
    execution_result = ExecutionResult(
        result=ExecutionReturn(ret=b"\x00\x00"),
        eq_outputs={},
        pending_transactions=[],
        stdout="",
        stderr="",
        genvm_log=[],
        state=_StateProxyWithMetrics({}),
        processing_time=5,
        nondet_disagree=None,
        execution_stats={},
    )
    fee_accounting = create_fee_accounting(
        fees_distribution={
            "leaderTimeunitsAllocation": 0,
            "validatorTimeunitsAllocation": 0,
            "appealRounds": 0,
            "executionBudgetPerRound": 0,
            "executionConsumed": 0,
            "totalMessageFees": 120,
            "rotations": [0],
            "maxPriceGenPerTimeUnit": 0,
            "storageFeeMaxGasPrice": 0,
            "receiptFeeMaxGasPrice": 0,
        },
        message_allocations=[
            {
                "messageType": 1,
                "onAcceptance": False,
                "parentIndex": (1 << 256) - 1,
                "recipient": recipient,
                "callKey": call_key,
                "budget": 120,
                "feeParams": fee_params,
            },
            {
                "messageType": 1,
                "onAcceptance": False,
                "parentIndex": 0,
                "recipient": child_recipient,
                "callKey": child_call_key,
                "budget": 60,
                "feeParams": child_fee_params,
            },
        ],
        num_of_validators=5,
        submitted_value=1_220,
        user_value=0,
    )

    with patch(
        "backend.node.genvm.base.run_genvm_host",
        new_callable=AsyncMock,
        return_value=execution_result,
    ) as run_genvm_host:
        await node._run_genvm(
            from_address="0x" + "de" * 20,
            calldata=b"\x00",
            readonly=False,
            is_init=False,
            transaction_hash="0xtx",
            transaction_datetime=None,
            fee_accounting=fee_accounting,
        )

    allocations = run_genvm_host.await_args.kwargs["fee_context"].message_fee_allocation
    assert len(allocations) == 2
    allocation = allocations[0]
    assert allocation["recipient"].as_hex.lower() == recipient
    assert allocation["call_key"] == bytes.fromhex("34" * 32)
    assert allocation["budget"] == 120
    assert allocation["on"] == "finalized"
    assert allocation["fee_params"] == {
        "Internal": {
            "leader_timeunits_allocation": 6,
            "validator_timeunits_allocation": 10,
            "execution_budget_per_round": 0,
            "rotations": [0],
            "max_price_gen_per_time_unit": 0,
            "storage_fee_max_gas_price": 0,
            "receipt_fee_max_gas_price": 0,
        },
    }
    assert allocation["children"][0]["recipient"].as_hex.lower() == child_recipient
    assert allocation["children"][0]["call_key"] == bytes.fromhex("56" * 32)
    assert allocation["children"][0]["budget"] == 60
    fallback = allocations[1]
    assert fallback["recipient"] is None
    assert fallback["call_key"] is None
    assert fallback["budget"] == 2**200
    assert fallback["on"] == "finalized"
    assert fallback["fee_params"] == {
        "External": {
            "gas_limit": 2**200,
            "max_gas_price": 0,
        },
    }


@pytest.mark.asyncio
async def test_run_genvm_rejects_fee_bearing_mode1_before_genvm():
    node = _make_node()
    execution_result = ExecutionResult(
        result=ExecutionReturn(ret=b"\x00\x00"),
        eq_outputs={},
        pending_transactions=[],
        stdout="",
        stderr="",
        genvm_log=[],
        state=_StateProxyWithMetrics({}),
        processing_time=5,
        nondet_disagree=None,
        execution_stats={},
    )
    fee_accounting = create_fee_accounting(
        fees_distribution={
            "leaderTimeunitsAllocation": 100,
            "validatorTimeunitsAllocation": 200,
            "appealRounds": 0,
            "executionBudgetPerRound": 0,
            "executionConsumed": 0,
            "totalMessageFees": 55,
            "rotations": [0],
            "maxPriceGenPerTimeUnit": 0,
            "storageFeeMaxGasPrice": 0,
            "receiptFeeMaxGasPrice": 0,
        },
        num_of_validators=5,
        submitted_value=1155,
        user_value=0,
    )

    with patch(
        "backend.node.genvm.base.run_genvm_host",
        new_callable=AsyncMock,
        return_value=execution_result,
    ) as run_genvm_host:
        receipt = await node._run_genvm(
            from_address="0x" + "de" * 20,
            calldata=b"\x00",
            readonly=False,
            is_init=False,
            transaction_hash="0xtx",
            transaction_datetime=None,
            fee_accounting=fee_accounting,
        )

    run_genvm_host.assert_not_awaited()
    assert receipt.execution_result == ExecutionResultStatus.ERROR
    assert (
        receipt.genvm_result["error_code"]
        == "Mode1MessageFeesRequireGenVMPerEmissionSupport"
    )
    assert receipt.genvm_result["raw_error"] == {
        "fatal": False,
        "causes": [
            "Mode1MessageFeesRequireGenVMPerEmissionSupport: fee-bearing "
            "GenVM messages require a message allocation tree"
        ],
        "ctx": {"source": "studio_fee_accounting"},
    }


@pytest.mark.asyncio
async def test_run_genvm_receipt_reports_data_fee_consumption():
    node = _make_node()
    execution_result = ExecutionResult(
        result=ExecutionReturn(ret=b"\x00\x00"),
        eq_outputs={},
        pending_transactions=[],
        stdout="",
        stderr="",
        genvm_log=[],
        state=_StateProxyWithMetrics({}),
        processing_time=5,
        nondet_disagree=None,
        execution_stats={},
        data_fee_bucket_totals=[100, 80, 60],
        data_fees_remaining=[70, 80, 10],
    )

    with patch(
        "backend.node.genvm.base.run_genvm_host",
        new_callable=AsyncMock,
        return_value=execution_result,
    ):
        receipt = await node._run_genvm(
            from_address="0x" + "de" * 20,
            calldata=b"\x00",
            readonly=False,
            is_init=False,
            transaction_hash="0xtx",
            transaction_datetime=None,
        )

    assert receipt.genvm_result["data_fee_bucket_totals"] == [100, 80, 60]
    assert receipt.genvm_result["data_fees_remaining"] == [70, 80, 10]
    assert receipt.genvm_result["data_fees_consumed"] == [30, 0, 50]


def test_snapshot_view_shared_decode_cache_reused_across_executions():
    slot = b"\x01" * 32
    value = b"shared-decoded-value"
    slot_key = base64.b64encode(slot).decode("ascii")
    raw_value = base64.b64encode(value).decode("utf-8")
    contract_address = "0x" + "ab" * 20

    def make_snapshot():
        snapshot = MagicMock(spec=ContractSnapshot)
        snapshot.contract_address = contract_address
        snapshot.states = {"accepted": {slot_key: raw_value}}
        snapshot.balance = 0
        return snapshot

    shared_decode_cache: dict[str, bytes] = {}
    snap1 = make_snapshot()
    view1 = _SnapshotView(
        snap1,
        lambda _addr: snap1,
        readonly=True,
        shared_decoded_value_cache=shared_decode_cache,
        collect_metrics=True,
    )
    got1 = view1.storage_read(Address(contract_address), slot, 0, len(value))
    assert got1 == value
    metrics1 = view1.get_metrics()
    assert metrics1["shared_decoded_cache_misses"] == 1
    assert raw_value in shared_decode_cache

    snap2 = make_snapshot()
    view2 = _SnapshotView(
        snap2,
        lambda _addr: snap2,
        readonly=True,
        shared_decoded_value_cache=shared_decode_cache,
        collect_metrics=True,
    )
    got2 = view2.storage_read(Address(contract_address), slot, 0, len(value))
    assert got2 == value
    metrics2 = view2.get_metrics()
    assert metrics2["shared_decoded_cache_hits"] == 1
    assert metrics2["decoded_slots_total"] == 0


def test_snapshot_view_primary_reads_use_fast_path_without_snapshot_factory():
    slot = b"\x02" * 32
    value = b"primary-fast-path"
    slot_key = base64.b64encode(slot).decode("ascii")
    raw_value = base64.b64encode(value).decode("utf-8")
    contract_address = "0x" + "cd" * 20

    primary_snapshot = MagicMock(spec=ContractSnapshot)
    primary_snapshot.contract_address = contract_address
    primary_snapshot.states = {"accepted": {slot_key: raw_value}}
    primary_snapshot.balance = 0

    snapshot_factory = MagicMock(side_effect=AssertionError("should not be called"))
    view = _SnapshotView(
        primary_snapshot,
        snapshot_factory,
        readonly=True,
        shared_decoded_value_cache={},
        collect_metrics=True,
    )

    for _ in range(3):
        got = view.storage_read(Address(contract_address), slot, 0, len(value))
        assert got == value

    assert snapshot_factory.call_count == 0


def test_snapshot_view_cross_contract_reads_remain_lazy():
    slot = b"\x03" * 32
    value = b"cross-lazy-value"
    slot_key = base64.b64encode(slot).decode("ascii")
    raw_value = base64.b64encode(value).decode("utf-8")
    primary_address = "0x" + "aa" * 20
    cross_address = "0x" + "bb" * 20

    primary_snapshot = MagicMock(spec=ContractSnapshot)
    primary_snapshot.contract_address = primary_address
    primary_snapshot.states = {"accepted": {}}
    primary_snapshot.balance = 0

    cross_snapshot = MagicMock(spec=ContractSnapshot)
    cross_snapshot.contract_address = cross_address
    cross_snapshot.states = {"accepted": {slot_key: raw_value}}
    cross_snapshot.balance = 0

    view = _SnapshotView(
        primary_snapshot,
        lambda _addr: cross_snapshot,
        readonly=True,
        shared_decoded_value_cache={},
        collect_metrics=True,
    )

    got1 = view.storage_read(Address(cross_address), slot, 0, len(value))
    got2 = view.storage_read(Address(cross_address), slot, 0, len(value))
    assert got1 == value
    assert got2 == value

    metrics = view.get_metrics()
    assert metrics["decoded_slots_total"] == 1
    assert metrics["decoded_cache_hits"] >= 1


def test_snapshot_view_reuses_shared_cross_contract_snapshot_cache():
    slot = b"\x04" * 32
    value = b"shared-snapshot-value"
    slot_key = base64.b64encode(slot).decode("ascii")
    raw_value = base64.b64encode(value).decode("utf-8")
    primary_address = "0x" + "11" * 20
    cross_address = "0x" + "22" * 20

    primary_snapshot = MagicMock(spec=ContractSnapshot)
    primary_snapshot.contract_address = primary_address
    primary_snapshot.states = {"accepted": {}}
    primary_snapshot.balance = 0

    cross_snapshot = MagicMock(spec=ContractSnapshot)
    cross_snapshot.contract_address = cross_address
    cross_snapshot.states = {"accepted": {slot_key: raw_value}}
    cross_snapshot.balance = 0

    snapshot_factory = MagicMock(return_value=cross_snapshot)
    shared_snapshot_cache: dict[str, ContractSnapshot] = {}

    view1 = _SnapshotView(
        primary_snapshot,
        snapshot_factory,
        readonly=True,
        shared_decoded_value_cache={},
        shared_contract_snapshot_cache=shared_snapshot_cache,
        collect_metrics=True,
    )
    got1 = view1.storage_read(Address(cross_address), slot, 0, len(value))
    assert got1 == value
    assert snapshot_factory.call_count == 1

    view2 = _SnapshotView(
        primary_snapshot,
        snapshot_factory,
        readonly=True,
        shared_decoded_value_cache={},
        shared_contract_snapshot_cache=shared_snapshot_cache,
        collect_metrics=True,
    )
    got2 = view2.storage_read(Address(cross_address), slot, 0, len(value))
    assert got2 == value
    assert snapshot_factory.call_count == 1
    metrics2 = view2.get_metrics()
    assert metrics2["snapshot_shared_cache_hits"] == 1
