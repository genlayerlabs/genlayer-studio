import functools

import pytest
from unittest.mock import AsyncMock, patch

from backend.node.genvm.base import (
    ExecutionResult,
    ExecutionReturn,
    GENVM_GASLESS_GAS_DATA,
    StateProxy,
    run_genvm_host,
)


class _DummyStateProxy(StateProxy):
    def __init__(self, marker: str):
        self.marker = marker
        self.snapshot_factory = lambda _addr: None

    def storage_read(self, account, slot, index, le, /) -> bytes:
        return b"\x00" * le

    def get_balance(self, addr) -> int:
        return 0


class _FakeManagerClient:
    """Stands in for base_host.ManagerClient's async-context lifecycle so
    run_genvm_host does not open a real manager websocket when run_genvm itself
    is mocked."""

    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _FakeHost:
    def __init__(self, _sock_listener, **kwargs):
        self.sock = None
        self._kwargs = kwargs

    def bind_context(self, _ctx):
        pass

    async def close_connections(self):
        pass

    def provide_result(self, _res, state, _ctx=None):
        return ExecutionResult(
            result=ExecutionReturn(ret=b"\x00"),
            eq_outputs={},
            pending_transactions=[],
            stdout="",
            stderr="",
            genvm_log=[],
            state=state,
            processing_time=0,
            nondet_disagree=None,
            execution_stats={},
        )


@pytest.mark.asyncio
async def test_run_genvm_host_skips_state_copy_on_first_attempt():
    original_state = _DummyStateProxy("original")
    host_supplier = functools.partial(
        _FakeHost,
        state_proxy=original_state,
        calldata_bytes=b"",
        leader_results=None,
    )

    with patch(
        "backend.node.genvm.base.base_host.run_genvm",
        new_callable=AsyncMock,
        return_value=object(),
    ) as run_mock, patch(
        "backend.node.genvm.base.base_host.ManagerClient",
        _FakeManagerClient,
    ), patch(
        "backend.node.genvm.base._copy_state_proxy",
        new_callable=AsyncMock,
        side_effect=lambda s: s,
    ) as copy_mock:
        result = await run_genvm_host(
            host_supplier,
            timeout=5,
            is_sync=False,
            message={},
            capture_output=False,
        )

    assert copy_mock.await_count == 0
    assert result.state is original_state
    assert run_mock.await_args.kwargs["bucket_totals"] == {
        "execution_data_gas": 2**200,
        "message_fee": 2**200,
        "nondet_outputs": 2**200,
        "submitted_messages": 2**200,
        "submitted_messages_count": 2**200,
    }
    assert run_mock.await_args.kwargs["gas_data"] == GENVM_GASLESS_GAS_DATA


@pytest.mark.asyncio
async def test_run_genvm_host_copies_state_on_retry():
    original_state = _DummyStateProxy("original")
    copied_state = _DummyStateProxy("copied")
    host_supplier = functools.partial(
        _FakeHost,
        state_proxy=original_state,
        calldata_bytes=b"",
        leader_results=None,
    )

    with patch(
        "backend.node.genvm.base.base_host.run_genvm",
        new_callable=AsyncMock,
        side_effect=[RuntimeError("boom"), object()],
    ), patch(
        "backend.node.genvm.base.base_host.ManagerClient",
        _FakeManagerClient,
    ), patch(
        "backend.node.genvm.base._copy_state_proxy",
        new_callable=AsyncMock,
        return_value=copied_state,
    ) as copy_mock, patch(
        "backend.node.genvm.base.asyncio.sleep",
        new_callable=AsyncMock,
        return_value=None,
    ):
        result = await run_genvm_host(
            host_supplier,
            timeout=15,
            is_sync=False,
            message={},
            capture_output=False,
        )

    assert copy_mock.await_count == 1
    assert result.state is copied_state
