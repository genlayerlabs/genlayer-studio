import asyncio
import threading
from unittest.mock import MagicMock, patch

import pytest

from backend.domain.types import LLMProvider, Validator
from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.node.base import Node
from backend.node.genvm import base as genvm_base
from backend.node.genvm.origin.public_abi import StorageType
from backend.node.types import ExecutionMode


def _make_node() -> Node:
    snapshot = MagicMock(spec=ContractSnapshot)
    snapshot.contract_address = "0x" + "ab" * 20
    snapshot.states = {"accepted": {}}
    snapshot.balance = 0
    manager = MagicMock()
    manager.url = "http://127.0.0.1:3999"
    return Node(
        contract_snapshot=snapshot,
        validator_mode=ExecutionMode.VALIDATOR,
        validator=Validator(
            address="0x" + "12" * 20,
            stake=100,
            llmprovider=LLMProvider(
                provider="openai",
                model="gpt-4",
                config={},
                plugin="",
                plugin_config={},
            ),
        ),
        contract_snapshot_factory=lambda _addr: snapshot,
        manager=manager,
    )


def test_phase_follows_the_run():
    ctx = genvm_base.Context()
    assert ctx.progress()["phase"] == "awaiting_manager"

    ctx.add_stat("manager_run_attempt_success", {"attempt": 0, "duration_ms": 1})
    assert ctx.progress()["phase"] == "awaiting_executor_connect"

    ctx.add_stat("host_loop_entered_s", 1.0)
    assert ctx.progress()["phase"] == "awaiting_vm"

    with ctx.host_call("storage_read"):
        assert ctx.progress()["phase"] == "host_call:storage_read"
    progress = ctx.progress()
    assert progress["phase"] == "awaiting_vm"
    assert progress["last_host_call"] == "storage_read"
    assert progress["host_call_counts"] == {"storage_read": 1}
    assert progress["host_idle_ms"] >= 0

    ctx.mark_run_finished()
    assert ctx.progress()["phase"] == "after_genvm"


def test_retry_backoff_drops_markers_of_the_failed_attempt():
    ctx = genvm_base.Context()
    ctx.add_stat("manager_run_attempt_success", {"attempt": 0, "duration_ms": 1})
    ctx.add_stat("host_loop_entered_s", 1.0)

    ctx.begin_retry_backoff(2)
    progress = ctx.progress()
    assert progress["phase"] == "retry_backoff"
    assert progress["retries"] == 2

    ctx.end_retry_backoff()
    assert ctx.progress()["phase"] == "awaiting_manager"


@pytest.mark.asyncio
async def test_host_reports_a_storage_read_stuck_in_flight():
    reading = threading.Event()
    release = threading.Event()
    state_proxy = MagicMock()

    def blocking_read(*_args):
        reading.set()
        release.wait(timeout=5)
        return b"\x00"

    state_proxy.storage_read = blocking_read
    host = genvm_base.Host(
        MagicMock(),
        calldata_bytes=b"",
        state_proxy=state_proxy,
        leader_results=None,
    )
    ctx = genvm_base.Context()
    host.bind_context(ctx)

    read = asyncio.create_task(
        host.storage_read(StorageType.DEFAULT, b"\x00" * 20, b"\x00" * 32, 0, 1)
    )
    assert await asyncio.to_thread(reading.wait, 5)
    assert ctx.progress()["phase"] == "host_call:storage_read"

    release.set()
    await read
    assert ctx.progress()["phase"] == "awaiting_manager"


def test_node_reports_step_before_genvm_starts():
    node = _make_node()
    node.timing_callback("SNAPSHOT_CREATION_START")

    assert node.partial_execution_stats() == {
        "timeout_progress": {
            "phase": "before_genvm",
            "node_step": "SNAPSHOT_CREATION_START",
        }
    }


@pytest.mark.asyncio
async def test_node_exposes_progress_of_a_run_that_never_returns():
    node = _make_node()
    entered = asyncio.Event()

    async def hung_run_genvm_host(_supplier, *, ctx, **_kwargs):
        ctx.add_stat("manager_run_attempt_success", {"attempt": 0, "duration_ms": 3})
        ctx.add_stat("host_loop_entered_s", 1.0)
        with ctx.host_call("get_balance"):
            pass
        entered.set()
        await asyncio.sleep(10)

    with patch(
        "backend.node.genvm.base.run_genvm_host",
        new=hung_run_genvm_host,
    ):
        run = asyncio.create_task(
            node._run_genvm(
                from_address="0x" + "de" * 20,
                calldata=b"\x00",
                readonly=False,
                is_init=False,
                transaction_hash="0xtx",
                transaction_datetime=None,
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=5)
        stats = node.partial_execution_stats()
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run

    assert stats["manager_run_attempt_success"]["duration_ms"] == 3
    progress = stats["timeout_progress"]
    assert progress["phase"] == "awaiting_vm"
    assert progress["node_step"] == "GENVM_EXECUTION_START"
    assert progress["last_host_call"] == "get_balance"
    assert progress["host_call_counts"] == {"get_balance": 1}
