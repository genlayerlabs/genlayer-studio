import functools

import pytest
from unittest.mock import AsyncMock, patch

from backend.node.genvm.base import (
    ExecutionResult,
    ExecutionReturn,
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


class _FakeHost:
    def __init__(self, _sock_listener, **kwargs):
        self.sock = None
        self._kwargs = kwargs

    def provide_result(self, _res, state):
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
