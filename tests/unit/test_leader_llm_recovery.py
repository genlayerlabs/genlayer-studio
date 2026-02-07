"""Unit tests for leader LLM failure recovery in Node._run_genvm()."""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import asyncio

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


def _make_llm_error_result() -> ExecutionResult:
    """Create an ExecutionResult that simulates an LLM provider failure."""
    return ExecutionResult(
        result=ExecutionError(
            message="LLM provider failed",
            kind=ResultCode.USER_ERROR,
            error_code=GenVMErrorCode.LLM_NO_PROVIDER,
            raw_error={
                "causes": ["NO_PROVIDER_FOR_PROMPT"],
                "fatal": True,
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
        contract_snapshot_factory=lambda addr: _make_snapshot(),
        leader_receipt=leader_receipt,
        manager=MagicMock(),
    )
    # Mock the _execution_finished to avoid socket.io calls
    node._execution_finished = AsyncMock()
    return node


@pytest.mark.asyncio
async def test_leader_raises_on_llm_failure():
    """Leader should raise GenVMInternalError when LLM provider fails."""
    node = _make_node(ExecutionMode.LEADER)
    llm_error_result = _make_llm_error_result()
    # Mock the state to have snapshot states
    llm_error_result.state = MagicMock()
    llm_error_result.state.snapshot.states = {"accepted": {}}

    with patch(
        "backend.node.genvm.base.run_genvm_host",
        new_callable=AsyncMock,
        return_value=llm_error_result,
    ):
        with pytest.raises(GenVMInternalError) as exc_info:
            await node._run_genvm(
                from_address="0x000000000000000000000000000000000000dead",
                calldata=b"\x00",
                readonly=False,
                is_init=False,
                transaction_hash="0xtx",
                transaction_datetime=None,
            )

        assert exc_info.value.is_leader is True
        assert exc_info.value.error_code == GenVMErrorCode.LLM_NO_PROVIDER
        assert exc_info.value.is_fatal is True


@pytest.mark.asyncio
async def test_validator_returns_timeout_on_llm_failure():
    """Validator should return a receipt with Vote.TIMEOUT when LLM provider fails."""
    # Create a leader receipt that succeeded
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
    llm_error_result = _make_llm_error_result()
    llm_error_result.state = MagicMock()
    llm_error_result.state.snapshot.states = {"accepted": {}}

    with patch(
        "backend.node.genvm.base.run_genvm_host",
        new_callable=AsyncMock,
        return_value=llm_error_result,
    ):
        receipt = await node._run_genvm(
            from_address="0x000000000000000000000000000000000000dead",
            calldata=b"\x00",
            readonly=False,
            is_init=False,
            transaction_hash="0xtx",
            transaction_datetime=None,
        )

    assert receipt.vote == Vote.TIMEOUT
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
        receipt = await node._run_genvm(
            from_address="0x000000000000000000000000000000000000dead",
            calldata=b"\x00",
            readonly=False,
            is_init=False,
            transaction_hash="0xtx",
            transaction_datetime=None,
        )

    assert receipt.execution_result == ExecutionResultStatus.SUCCESS
    assert receipt.genvm_result["error_code"] is None
