"""Every run carries a timestamp: the manager picks the executor line by the forks active at it."""

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.domain.types import LLMProvider, Validator
from backend.node.base import Node
from backend.node.genvm.base import ExecutionResult, ExecutionReturn
from backend.node.types import ExecutionMode


def _make_snapshot() -> ContractSnapshot:
    snapshot = MagicMock(spec=ContractSnapshot)
    snapshot.contract_address = "0x0000000000000000000000000000000000abcdef"
    snapshot.states = {"accepted": {}}
    snapshot.balance = 0
    return snapshot


def _make_node() -> Node:
    node = Node(
        contract_snapshot=_make_snapshot(),
        validator_mode=ExecutionMode.LEADER,
        validator=Validator(
            address="0x0000000000000000000000000000000000001234",
            stake=100,
            llmprovider=LLMProvider(
                provider="openai",
                model="gpt-4",
                config={},
                plugin="",
                plugin_config={},
            ),
        ),
        contract_snapshot_factory=lambda _addr: _make_snapshot(),
        manager=MagicMock(),
    )
    node._execution_finished = AsyncMock()
    return node


def _success() -> ExecutionResult:
    result = ExecutionResult(
        result=ExecutionReturn(ret=b"\x00\x00"),
        eq_outputs={},
        pending_transactions=[],
        stdout="",
        stderr="",
        genvm_log=[],
        state=MagicMock(),
        processing_time=0,
        nondet_disagree=None,
    )
    result.state.snapshot.states = {"accepted": {}}
    return result


async def _sent_timestamp(transaction_created_at: str | None) -> str:
    with patch(
        "backend.node.genvm.base.run_genvm_host",
        new_callable=AsyncMock,
        return_value=_success(),
    ) as run:
        await _make_node().run_contract(
            "0x000000000000000000000000000000000000dead",
            b"\x00",
            transaction_hash="0xtx",
            transaction_created_at=transaction_created_at,
        )
    return run.await_args.kwargs["message"]["transaction_timestamp"]


@pytest.mark.asyncio
async def test_run_without_creation_time_runs_at_the_current_time():
    before = datetime.datetime.now(datetime.UTC)
    timestamp = await _sent_timestamp(None)
    assert before <= datetime.datetime.fromisoformat(timestamp)


@pytest.mark.asyncio
async def test_run_keeps_the_transaction_creation_time():
    timestamp = await _sent_timestamp("2026-03-04T05:06:07+00:00")
    assert datetime.datetime.fromisoformat(timestamp) == datetime.datetime(
        2026, 3, 4, 5, 6, 7, tzinfo=datetime.UTC
    )
