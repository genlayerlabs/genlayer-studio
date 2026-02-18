"""Unit tests for Node._set_vote() voting logic."""

from unittest.mock import MagicMock

from backend.node.base import Node
from backend.node.types import Receipt, ExecutionMode, ExecutionResultStatus, Vote
from backend.node.genvm.origin.public_abi import ResultCode
from backend.node.genvm.error_codes import GenVMErrorCode
from backend.domain.types import Validator, LLMProvider


def _make_validator() -> Validator:
    return Validator(
        address="0x1234",
        stake=100,
        llmprovider=LLMProvider(
            provider="openai",
            model="gpt-4",
            config={},
            plugin="",
            plugin_config={},
        ),
    )


def _make_node(leader_receipt: Receipt) -> Node:
    return Node(
        contract_snapshot=None,
        validator_mode=ExecutionMode.VALIDATOR,
        validator=_make_validator(),
        contract_snapshot_factory=None,
        leader_receipt=leader_receipt,
        manager=MagicMock(),
    )


def _make_receipt(
    result_code: int,
    message: bytes,
    error_code: str | None = None,
    raw_error: dict | None = None,
    contract_state: dict | None = None,
    execution_result: ExecutionResultStatus = ExecutionResultStatus.ERROR,
) -> Receipt:
    return Receipt(
        result=bytes([result_code]) + message,
        calldata=b"",
        gas_used=0,
        mode=ExecutionMode.VALIDATOR,
        contract_state=contract_state or {},
        node_config={},
        eq_outputs={},
        execution_result=execution_result,
        vote=None,
        genvm_result={
            "stdout": "",
            "stderr": "",
            "error_code": error_code,
            "raw_error": raw_error,
        },
    )


def _make_success_receipt() -> Receipt:
    return Receipt(
        result=bytes([ResultCode.RETURN]) + b"\x00\x00",
        calldata=b"",
        gas_used=0,
        mode=ExecutionMode.LEADER,
        contract_state={"slot": "data"},
        node_config={},
        eq_outputs={},
        execution_result=ExecutionResultStatus.SUCCESS,
        vote=None,
        genvm_result=None,
    )


# --- LLM/fatal errors → DETERMINISTIC_VIOLATION at _set_vote level ---
# (Replacement is handled by the consensus layer, not _set_vote)


def test_llm_fatal_error_votes_deterministic_violation():
    """LLM fatal errors are no longer TIMEOUT — consensus layer handles replacement."""
    leader_receipt = _make_success_receipt()
    node = _make_node(leader_receipt)

    receipt = _make_receipt(
        ResultCode.USER_ERROR,
        b"LLM error",
        error_code=GenVMErrorCode.LLM_NO_PROVIDER,
        raw_error={"causes": ["NO_PROVIDER_FOR_PROMPT"], "fatal": True},
    )

    result = node._set_vote(receipt)
    assert result.vote == Vote.DETERMINISTIC_VIOLATION


# --- Web errors → DETERMINISTIC_VIOLATION ---


def test_web_error_not_timeout():
    leader_receipt = _make_success_receipt()
    node = _make_node(leader_receipt)

    receipt = _make_receipt(
        ResultCode.USER_ERROR,
        b"Web error",
        error_code=GenVMErrorCode.WEB_REQUEST_FAILED,
        raw_error={"causes": ["WEBPAGE_LOAD_FAILED"], "fatal": False},
    )

    result = node._set_vote(receipt)
    assert result.vote == Vote.DETERMINISTIC_VIOLATION


# --- Regression: VM_ERROR timeout still works ---


def test_vm_error_timeout_still_works():
    leader_receipt = _make_success_receipt()
    node = _make_node(leader_receipt)

    receipt = _make_receipt(ResultCode.VM_ERROR, b"timeout")

    result = node._set_vote(receipt)
    assert result.vote == Vote.TIMEOUT


def test_vm_error_genvm_internal_error_still_works():
    leader_receipt = _make_success_receipt()
    node = _make_node(leader_receipt)

    receipt = _make_receipt(ResultCode.VM_ERROR, b"GenVM internal error: something")

    result = node._set_vote(receipt)
    assert result.vote == Vote.TIMEOUT


# --- Regression: matching results → AGREE ---


def test_matching_results_vote_agree():
    leader_receipt = _make_receipt(
        ResultCode.RETURN,
        b"\x00\x00",
        execution_result=ExecutionResultStatus.SUCCESS,
        contract_state={"slot": "data"},
    )
    node = _make_node(leader_receipt)

    validator_receipt = _make_receipt(
        ResultCode.RETURN,
        b"\x00\x00",
        execution_result=ExecutionResultStatus.SUCCESS,
        contract_state={"slot": "data"},
    )

    result = node._set_vote(validator_receipt)
    assert result.vote == Vote.AGREE


# --- Regression: mismatching results → DETERMINISTIC_VIOLATION ---


def test_mismatching_results_vote_deterministic_violation():
    leader_receipt = _make_success_receipt()
    node = _make_node(leader_receipt)

    validator_receipt = _make_receipt(
        ResultCode.RETURN,
        b"\x00\x01",  # different result
        execution_result=ExecutionResultStatus.SUCCESS,
    )

    result = node._set_vote(validator_receipt)
    assert result.vote == Vote.DETERMINISTIC_VIOLATION


# --- Edge case: genvm_result is None → should not crash ---


def test_no_genvm_result_does_not_crash():
    leader_receipt = _make_success_receipt()
    node = _make_node(leader_receipt)

    receipt = Receipt(
        result=bytes([ResultCode.USER_ERROR]) + b"some error",
        calldata=b"",
        gas_used=0,
        mode=ExecutionMode.VALIDATOR,
        contract_state={},
        node_config={},
        eq_outputs={},
        execution_result=ExecutionResultStatus.ERROR,
        vote=None,
        genvm_result=None,
    )

    result = node._set_vote(receipt)
    assert result.vote == Vote.DETERMINISTIC_VIOLATION


# --- Edge case: error_code is None → should not be TIMEOUT ---


def test_none_error_code_not_timeout():
    leader_receipt = _make_success_receipt()
    node = _make_node(leader_receipt)

    receipt = _make_receipt(
        ResultCode.USER_ERROR,
        b"some error",
        error_code=None,
    )

    result = node._set_vote(receipt)
    assert result.vote == Vote.DETERMINISTIC_VIOLATION


# --- VM crash (non-timeout) → DISAGREE ---


def test_vm_error_exit_code_votes_disagree():
    """VM crash (exit_code) should be DISAGREE — validator couldn't validate."""
    leader_receipt = _make_success_receipt()
    node = _make_node(leader_receipt)

    receipt = _make_receipt(
        ResultCode.VM_ERROR,
        b"exit_code 1",
    )

    result = node._set_vote(receipt)
    assert result.vote == Vote.DISAGREE


def test_vm_error_oom_votes_disagree():
    """VM OOM should be DISAGREE."""
    leader_receipt = _make_success_receipt()
    node = _make_node(leader_receipt)

    receipt = _make_receipt(
        ResultCode.VM_ERROR,
        b"OOM",
    )

    result = node._set_vote(receipt)
    assert result.vote == Vote.DISAGREE


def test_vm_error_matching_leader_still_disagree():
    """Even if leader and validator crash with same VM error, validator votes DISAGREE."""
    leader_receipt = _make_receipt(
        ResultCode.VM_ERROR,
        b"exit_code 1",
        execution_result=ExecutionResultStatus.ERROR,
        contract_state={"slot": "data"},
    )
    node = _make_node(leader_receipt)

    validator_receipt = _make_receipt(
        ResultCode.VM_ERROR,
        b"exit_code 1",
        execution_result=ExecutionResultStatus.ERROR,
        contract_state={"slot": "data"},
    )

    result = node._set_vote(validator_receipt)
    assert result.vote == Vote.DISAGREE


# --- Nondet disagree takes precedence ---


def test_nondet_disagree_before_det_violation():
    """GenVM nondet disagreement should be DISAGREE even if state matches leader."""
    leader_receipt = _make_receipt(
        ResultCode.RETURN,
        b"\x00\x00",
        execution_result=ExecutionResultStatus.SUCCESS,
        contract_state={"slot": "data"},
    )
    node = _make_node(leader_receipt)

    validator_receipt = _make_receipt(
        ResultCode.RETURN,
        b"\x00\x00",
        execution_result=ExecutionResultStatus.SUCCESS,
        contract_state={"slot": "data"},
    )
    validator_receipt.nondet_disagree = 3

    result = node._set_vote(validator_receipt)
    assert result.vote == Vote.DISAGREE


# --- USER_ERROR with matching state → AGREE (deterministic contract error) ---


def test_user_error_matching_state_votes_agree():
    """If both leader and validator get USER_ERROR with same state, it's a valid agree."""
    leader_receipt = _make_receipt(
        ResultCode.USER_ERROR,
        b"contract raised ValueError",
        execution_result=ExecutionResultStatus.ERROR,
        contract_state={"slot": "data"},
    )
    node = _make_node(leader_receipt)

    validator_receipt = _make_receipt(
        ResultCode.USER_ERROR,
        b"contract raised ValueError",
        execution_result=ExecutionResultStatus.ERROR,
        contract_state={"slot": "data"},
    )

    result = node._set_vote(validator_receipt)
    assert result.vote == Vote.AGREE
