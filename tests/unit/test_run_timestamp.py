"""Every run carries a timestamp: the manager picks the executor line by the forks active at it."""

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.domain.types import LLMProvider, Validator
from backend.node.base import Node
from backend.node.genvm.base import ExecutionResult, ExecutionReturn
from backend.node.genvm.executor_selection import LEGACY_EXECUTOR_SELECTOR
from backend.node.types import ExecutionMode


def _make_snapshot(selector: str | None = None) -> ContractSnapshot:
    snapshot = MagicMock(spec=ContractSnapshot)
    snapshot.genvm_executor_selector = selector
    snapshot.contract_address = "0x0000000000000000000000000000000000abcdef"
    snapshot.states = {"accepted": {}}
    snapshot.balance = 0
    return snapshot


def _make_node(selector: str | None = None) -> Node:
    node = Node(
        contract_snapshot=_make_snapshot(selector),
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


async def _sent_timestamp(
    transaction_created_at: str | None, selector: str | None = None
) -> str:
    with patch(
        "backend.node.genvm.base.run_genvm_host",
        new_callable=AsyncMock,
        return_value=_success(),
    ) as run:
        await _make_node(selector).run_contract(
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


@pytest.mark.asyncio
async def test_v02_line_run_before_its_fork_runs_at_the_fork():
    timestamp = await _sent_timestamp(
        "2025-06-01T00:00:00+00:00", selector=LEGACY_EXECUTOR_SELECTOR
    )
    assert datetime.datetime.fromisoformat(timestamp) == datetime.datetime(
        2026, 1, 1, tzinfo=datetime.UTC
    )


@pytest.mark.asyncio
async def test_v02_line_run_after_its_fork_keeps_the_transaction_time():
    timestamp = await _sent_timestamp(
        "2026-03-04T05:06:07+00:00", selector=LEGACY_EXECUTOR_SELECTOR
    )
    assert datetime.datetime.fromisoformat(timestamp) == datetime.datetime(
        2026, 3, 4, 5, 6, 7, tzinfo=datetime.UTC
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("selector", [None, "v0.2.17", "v0.3.0"])
async def test_runs_outside_the_v02_line_selector_keep_an_early_time(selector):
    timestamp = await _sent_timestamp("2025-06-01T00:00:00+00:00", selector=selector)
    assert datetime.datetime.fromisoformat(timestamp) == datetime.datetime(
        2025, 6, 1, tzinfo=datetime.UTC
    )


@pytest.mark.asyncio
async def test_v02_header_deploy_before_its_fork_runs_at_the_fork():
    with patch(
        "backend.node.genvm.base.run_genvm_host",
        new_callable=AsyncMock,
        return_value=_success(),
    ) as run:
        await _make_node().deploy_contract(
            "0x000000000000000000000000000000000000dead",
            b"# v0.2.16\nfrom genlayer import *\n",
            b"\x00",
            transaction_hash="0xtx",
            transaction_created_at="2024-03-15T14:30:00Z",
        )
    assert run.await_args.kwargs["genvm_executor_selector"] == LEGACY_EXECUTOR_SELECTOR
    timestamp = run.await_args.kwargs["message"]["transaction_timestamp"]
    assert datetime.datetime.fromisoformat(timestamp) == datetime.datetime(
        2026, 1, 1, tzinfo=datetime.UTC
    )
