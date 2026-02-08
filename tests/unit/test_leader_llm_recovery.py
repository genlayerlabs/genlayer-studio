"""Unit tests for leader/validator fatal error handling in Node._run_genvm()."""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from backend.node.base import Node
from backend.node.types import Receipt, ExecutionMode, ExecutionResultStatus, Vote
from backend.node.genvm.origin.public_abi import ResultCode
from backend.node.genvm.error_codes import GenVMErrorCode, GenVMInternalError
from backend.node.genvm.base import ExecutionError, ExecutionReturn, ExecutionResult
from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.domain.types import Validator, LLMProvider


def _make_validator() -> Validator:
    return Validator(
        address="0x0000000000000000000000000000000000001234",
        stake=100,
        llmprovider=LLMProvider(
            provider="openai",
            model="gpt-4",
            config={},
            plugin="",
            plugin_config={},
        ),
    )


def _make_snapshot() -> ContractSnapshot:
    snapshot = MagicMock(spec=ContractSnapshot)
    snapshot.contract_address = "0x0000000000000000000000000000000000abcdef"
    snapshot.states = {"accepted": {}}
    snapshot.balance = 0
    return snapshot


def _make_fatal_error_result(
    error_code=GenVMErrorCode.LLM_NO_PROVIDER, fatal=True
) -> ExecutionResult:
    """Create an ExecutionResult that simulates a fatal infrastructure failure."""
    return ExecutionResult(
        result=ExecutionError(
            message="Provider failed",
            kind=ResultCode.USER_ERROR,
            error_code=error_code,
            raw_error={
                "causes": ["NO_PROVIDER_FOR_PROMPT"],
                "fatal": fatal,
                "ctx": {"primary_model": "gpt-4", "fallback_model": None},
            },
        ),
        eq_outputs={},
        pending_transactions=[],
        stdout="",
        stderr="provider failed",
        genvm_log=[],
        state=MagicMock(),
        processing_time=100,
        nondet_disagree=None,
    )


def _make_success_result() -> ExecutionResult:
    return ExecutionResult(
        result=ExecutionReturn(ret=b"\x00\x00"),
        eq_outputs={},
        pending_transactions=[],
        stdout="ok",
        stderr="",
        genvm_log=[],
        state=MagicMock(),
        processing_time=50,
        nondet_disagree=None,
    )


def _make_node(mode: ExecutionMode, leader_receipt: Receipt | None = None) -> Node:
    snapshot = _make_snapshot()
    node = Node(
        contract_snapshot=snapshot,
        validator_mode=mode,
        validator=_make_validator(),
        contract_snapshot_factory=lambda _addr: _make_snapshot(),
        leader_receipt=leader_receipt,
        manager=MagicMock(),
    )
    node._execution_finished = AsyncMock()
    return node


async def _run_genvm(node: Node):
    return await node._run_genvm(
        from_address="0x000000000000000000000000000000000000dead",
        calldata=b"\x00",
        readonly=False,
        is_init=False,
        transaction_hash="0xtx",
        transaction_datetime=None,
    )


# --- Leader fatal error → GenVMInternalError ---


@pytest.mark.asyncio
async def test_leader_raises_on_fatal_error():
    """Leader should raise GenVMInternalError when raw_error.fatal is True."""
    node = _make_node(ExecutionMode.LEADER)
    error_result = _make_fatal_error_result(fatal=True)
    error_result.state = MagicMock()
    error_result.state.snapshot.states = {"accepted": {}}

    with patch(
        "backend.node.genvm.base.run_genvm_host",
        new_callable=AsyncMock,
        return_value=error_result,
    ):
        with pytest.raises(GenVMInternalError) as exc_info:
            await _run_genvm(node)

        assert exc_info.value.is_leader is True
        assert exc_info.value.is_fatal is True
        assert exc_info.value.error_code == GenVMErrorCode.LLM_NO_PROVIDER


@pytest.mark.asyncio
async def test_leader_non_fatal_error_does_not_raise():
    """Leader should NOT raise GenVMInternalError when raw_error.fatal is False."""
    node = _make_node(ExecutionMode.LEADER)
    error_result = _make_fatal_error_result(fatal=False)
    error_result.state = MagicMock()
    error_result.state.snapshot.states = {"accepted": {}}

    with patch(
        "backend.node.genvm.base.run_genvm_host",
        new_callable=AsyncMock,
        return_value=error_result,
    ):
        # Should return receipt, not raise
        receipt = await _run_genvm(node)

    assert receipt.execution_result == ExecutionResultStatus.ERROR
    assert receipt.genvm_result["error_code"] == GenVMErrorCode.LLM_NO_PROVIDER


@pytest.mark.asyncio
async def test_leader_success_does_not_raise():
    """Leader should return normally when execution succeeds."""
    node = _make_node(ExecutionMode.LEADER)
    success_result = _make_success_result()
    success_result.state = MagicMock()
    success_result.state.snapshot.states = {"accepted": {}}

    with patch(
        "backend.node.genvm.base.run_genvm_host",
        new_callable=AsyncMock,
        return_value=success_result,
    ):
        receipt = await _run_genvm(node)

    assert receipt.execution_result == ExecutionResultStatus.SUCCESS
    assert receipt.genvm_result["error_code"] is None


# --- Validator fatal error → receipt with raw_error (consensus handles replacement) ---


@pytest.mark.asyncio
async def test_validator_fatal_error_returns_receipt():
    """Validator fatal error returns receipt (consensus layer handles replacement)."""
    leader_receipt = Receipt(
        result=bytes([ResultCode.RETURN]) + b"\x00\x00",
        calldata=b"",
        gas_used=0,
        mode=ExecutionMode.LEADER,
        contract_state={},
        node_config={},
        eq_outputs={},
        execution_result=ExecutionResultStatus.SUCCESS,
        vote=None,
        genvm_result=None,
    )

    node = _make_node(ExecutionMode.VALIDATOR, leader_receipt=leader_receipt)
    error_result = _make_fatal_error_result(fatal=True)
    error_result.state = MagicMock()
    error_result.state.snapshot.states = {"accepted": {}}

    with patch(
        "backend.node.genvm.base.run_genvm_host",
        new_callable=AsyncMock,
        return_value=error_result,
    ):
        receipt = await _run_genvm(node)

    # _set_vote sees a mismatch → DETERMINISTIC_VIOLATION
    # Consensus layer will check raw_error["fatal"] for replacement
    assert receipt.vote == Vote.DETERMINISTIC_VIOLATION
    assert receipt.genvm_result["raw_error"]["fatal"] is True
    assert receipt.genvm_result["error_code"] == GenVMErrorCode.LLM_NO_PROVIDER
