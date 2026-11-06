import asyncio
import functools
import gc
import socket
import threading
import weakref
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.domain.types import LLMProvider, Validator
from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.node.base import Node
from backend.node.genvm import base as genvm_base
from backend.node.genvm.error_codes import GenVMErrorCode, GenVMInternalError
from backend.node.genvm.origin import base_host
from backend.node.genvm.origin import host_fns
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

    ctx.add_stat("call_counts", {})
    assert ctx.progress()["phase"] == "awaiting_manager_result"

    ctx.mark_run_finished()
    assert ctx.progress()["phase"] == "after_genvm"


@pytest.mark.asyncio
async def test_phase_follows_the_real_host_loop():
    server, client = socket.socketpair()
    server.setblocking(False)
    handler = MagicMock()
    handler.loop_enter = AsyncMock(return_value=server)
    ctx = genvm_base.Context()

    try:
        loop = asyncio.create_task(
            base_host.host_loop(handler, asyncio.Event(), ctx=ctx)
        )
        for _ in range(100):
            if "host_loop_entered_s" in ctx.stats:
                break
            await asyncio.sleep(0.01)
        assert ctx.progress()["phase"] == "awaiting_vm"

        client.sendall(bytes([host_fns.Methods.NOTIFY_FINISHED]))
        await asyncio.wait_for(loop, timeout=5)
        assert ctx.progress()["phase"] == "awaiting_manager_result"
    finally:
        client.close()
        server.close()


def test_retry_backoff_drops_progress_of_the_failed_attempt():
    ctx = genvm_base.Context()
    ctx.add_stat("manager_run_attempt_success", {"attempt": 0, "duration_ms": 1})
    ctx.add_stat("host_loop_entered_s", 1.0)
    ctx.add_stat("host_first_method", "storage_read")
    ctx.add_stat("manager_run_attempt_0_error", {"attempt": 0})
    ctx.add_stat("call_counts", {})
    ctx.add_stat("delete_genvm_request_error", "cannot connect to manager:3999")
    with ctx.host_call("storage_read"):
        pass

    ctx.begin_retry_backoff(2)
    progress = ctx.progress()
    assert progress["phase"] == "retry_backoff"
    assert progress["retries"] == 2
    assert progress["last_host_call"] is None
    assert progress["host_idle_ms"] is None
    assert progress["host_call_counts"] == {}
    assert "host_first_method" not in ctx.stats
    assert "call_counts" not in ctx.stats
    assert "delete_genvm_request_error" not in ctx.stats
    assert "manager_run_attempt_0_error" in ctx.stats

    ctx.end_retry_backoff()
    assert ctx.progress()["phase"] == "awaiting_manager"

    ctx.add_stat("host_loop_entered_s", 2.0)
    assert ctx.progress()["phase"] == "awaiting_vm"


class _CopyableStateProxy:
    pass


@pytest.mark.asyncio
async def test_real_retry_loop_reports_each_attempt_on_the_callers_context():
    ctx = genvm_base.Context()
    bound_contexts: list[genvm_base.Context | None] = []
    received_contexts: list[genvm_base.Context] = []
    phases_during_backoff: list[str] = []
    second_attempt_progress: dict = {}

    async def fake_run_genvm(handler, *, ctx, **_kwargs):
        bound_contexts.append(handler._ctx)
        received_contexts.append(ctx)
        if len(received_contexts) == 1:
            ctx.add_stat("host_loop_entered_s", 1.0)
            ctx.add_stat("call_counts", {})
            raise RuntimeError("executor crashed")
        second_attempt_progress.update(ctx.progress())
        raise GenVMInternalError(
            message="stop",
            error_code=GenVMErrorCode.LLM_NO_PROVIDER,
            causes=["stop"],
            is_fatal=True,
        )

    async def fake_sleep(_delay):
        phases_during_backoff.append(ctx.progress()["phase"])

    with patch(
        "backend.node.genvm.base.base_host.run_genvm", new=fake_run_genvm
    ), patch("backend.node.genvm.base.asyncio.sleep", new=fake_sleep):
        with pytest.raises(GenVMInternalError):
            await genvm_base.run_genvm_host(
                functools.partial(
                    genvm_base.Host,
                    calldata_bytes=b"",
                    state_proxy=_CopyableStateProxy(),
                    leader_results=None,
                ),
                timeout=60,
                is_sync=False,
                message={},
                ctx=ctx,
            )

    assert bound_contexts == [ctx, ctx]
    assert received_contexts == [ctx, ctx]
    assert phases_during_backoff == ["retry_backoff"]
    assert second_attempt_progress["phase"] == "awaiting_manager"
    assert second_attempt_progress["retries"] == 1


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
    assert await read == b"\x00"
    assert ctx.progress()["phase"] == "awaiting_manager"


@pytest.mark.asyncio
async def test_host_call_that_fails_does_not_stay_in_flight():
    state_proxy = MagicMock()
    state_proxy.get_balance.side_effect = RuntimeError("boom")
    host = genvm_base.Host(
        MagicMock(),
        calldata_bytes=b"",
        state_proxy=state_proxy,
        leader_results=None,
    )
    ctx = genvm_base.Context()
    host.bind_context(ctx)

    with pytest.raises(RuntimeError, match="boom"):
        await host.get_balance(b"\x00" * 20)

    progress = ctx.progress()
    assert progress["phase"] == "awaiting_manager"
    assert progress["host_call_counts"] == {"get_balance": 1}


def test_partial_stats_leave_out_raw_manager_errors():
    node = _make_node()
    node._genvm_ctx = genvm_base.Context()
    node._genvm_ctx.add_stat("delete_genvm_request_failed", True)
    node._genvm_ctx.add_stat(
        "delete_genvm_request_error", "cannot connect to manager:3999"
    )
    node._genvm_ctx.add_stat("delete_genvm_body", "internal detail")

    stats = node.partial_execution_stats()

    assert stats["delete_genvm_request_failed"] is True
    assert "delete_genvm_request_error" not in stats
    assert "delete_genvm_body" not in stats


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


@pytest.mark.asyncio
async def test_failed_run_no_longer_reports_a_genvm_phase():
    node = _make_node()

    async def failed_run_genvm_host(_supplier, **_kwargs):
        raise GenVMInternalError(
            message="stop",
            error_code=GenVMErrorCode.LLM_NO_PROVIDER,
            causes=["stop"],
            is_fatal=True,
        )

    with patch("backend.node.genvm.base.run_genvm_host", new=failed_run_genvm_host):
        with pytest.raises(GenVMInternalError):
            await node._run_genvm(
                from_address="0x" + "de" * 20,
                calldata=b"\x00",
                readonly=False,
                is_init=False,
                transaction_hash="0xtx",
                transaction_datetime=None,
            )

    assert node.partial_execution_stats()["timeout_progress"]["phase"] == "after_genvm"


def test_node_is_freed_without_the_cycle_collector():
    gc.collect()
    gc.disable()
    try:
        node = _make_node()
        node_ref = weakref.ref(node)
        del node
        assert node_ref() is None
    finally:
        gc.enable()
