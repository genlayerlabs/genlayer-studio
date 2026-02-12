import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.domain.types import LLMProvider, Validator
from backend.node.base import Node, _SnapshotView
from backend.node.genvm.base import ExecutionResult, ExecutionReturn
from backend.node.types import Address, ExecutionMode


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
    )
    got2 = view2.storage_read(Address(contract_address), slot, 0, len(value))
    assert got2 == value
    metrics2 = view2.get_metrics()
    assert metrics2["shared_decoded_cache_hits"] == 1
    assert metrics2["decoded_slots_total"] == 0
