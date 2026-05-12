import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.consensus.base import CommittingState
from backend.database_handler.types import ConsensusData
from backend.node.genvm.error_codes import GenVMInternalError, GenVMErrorCode
from backend.node.genvm.origin.public_abi import ResultCode
from backend.node.types import ExecutionMode, ExecutionResultStatus, Receipt, Vote


class _MessageHandler:
    def send_message(self, *_args, **_kwargs):
        return None


def _make_receipt(address: str, vote: Vote) -> Receipt:
    return Receipt(
        result=bytes([ResultCode.RETURN]) + b"ok",
        calldata=b"",
        gas_used=0,
        mode=ExecutionMode.VALIDATOR,
        contract_state={},
        node_config={"address": address},
        execution_result=ExecutionResultStatus.SUCCESS,
        vote=vote,
        genvm_result={"raw_error": {"fatal": False}},
    )


def _snapshot_node(address: str) -> SimpleNamespace:
    validator = MagicMock()
    validator.to_dict.return_value = {"address": address}
    return SimpleNamespace(validator=validator)


@pytest.mark.asyncio
async def test_committing_times_out_hung_validator_without_blocking(monkeypatch):
    """Outer slot timeout now produces a terminal TIMEOUT vote, not fatal IDLE."""
    monkeypatch.setenv("CONSENSUS_VALIDATOR_EXEC_TIMEOUT_SECONDS", "0.05")

    transaction = SimpleNamespace(hash="tx-hash")

    tx_processor = MagicMock()
    tx_processor.add_state_timestamp = MagicMock()
    tx_processor.update_transaction_status = MagicMock()
    tx_processor.set_transaction_timestamp_last_vote = MagicMock()

    consensus_service = MagicMock()

    async def exec_hung(_transaction):
        await asyncio.sleep(0.2)
        return _make_receipt("validator-1", Vote.AGREE)

    async def exec_fast(_transaction):
        return _make_receipt("validator-2", Vote.AGREE)

    def node_factory(
        validator,
        _mode,
        _contract_snapshot,
        _leader_receipt,
        _msg_handler,
        _contract_snapshot_factory,
        _validators_snapshot,
        _timing_callback,
        _genvm_manager,
        _shared_decoded_value_cache,
        _shared_contract_snapshot_cache,
    ):
        if validator["address"] == "validator-1":
            return SimpleNamespace(exec_transaction=exec_hung)
        return SimpleNamespace(exec_transaction=exec_fast)

    context = SimpleNamespace(
        transaction=transaction,
        transactions_processor=tx_processor,
        msg_handler=_MessageHandler(),
        consensus_service=consensus_service,
        contract_processor=MagicMock(),
        node_factory=node_factory,
        contract_snapshot=MagicMock(),
        contract_snapshot_factory=MagicMock(),
        validators_snapshot=SimpleNamespace(
            nodes=[
                _snapshot_node("leader"),
                _snapshot_node("validator-1"),
                _snapshot_node("validator-2"),
            ]
        ),
        genvm_manager=MagicMock(),
        shared_decoded_value_cache={},
        shared_contract_snapshot_cache={},
        leader={"address": "leader"},
        remaining_validators=[
            {"address": "validator-1"},
            {"address": "validator-2"},
        ],
        consensus_data=ConsensusData(votes={}, leader_receipt=None, validators=[]),
        validation_results=[],
    )

    start = asyncio.get_running_loop().time()
    next_state = await CommittingState().handle(context)
    elapsed = asyncio.get_running_loop().time() - start

    assert next_state.__class__.__name__ == "RevealingState"
    assert elapsed <= 0.7

    votes = [r.vote for r in context.validation_results]
    assert votes.count(Vote.TIMEOUT) == 1
    assert votes.count(Vote.AGREE) == 1

    timeout_receipt = next(
        r for r in context.validation_results if r.vote == Vote.TIMEOUT
    )
    assert timeout_receipt.genvm_result is not None
    assert (
        timeout_receipt.genvm_result["error_code"] == "CONSENSUS_VALIDATOR_EXEC_TIMEOUT"
    )
    assert timeout_receipt.genvm_result["raw_error"]["causes"] == [
        "VALIDATOR_EXEC_TIMEOUT"
    ]
    assert timeout_receipt.genvm_result["raw_error"]["fatal"] is False

    timeout_timestamps = [
        call.args[1]
        for call in tx_processor.add_state_timestamp.call_args_list
        if "_TIMEOUT" in call.args[1]
    ]
    assert timeout_timestamps


def _make_context(
    *,
    validators: list[str],
    node_factory,
    snapshot_addresses: list[str] | None = None,
) -> SimpleNamespace:
    tx_processor = MagicMock()
    tx_processor.add_state_timestamp = MagicMock()
    tx_processor.update_transaction_status = MagicMock()
    tx_processor.set_transaction_timestamp_last_vote = MagicMock()

    return SimpleNamespace(
        transaction=SimpleNamespace(hash="tx-hash"),
        transactions_processor=tx_processor,
        msg_handler=_MessageHandler(),
        consensus_service=MagicMock(),
        contract_processor=MagicMock(),
        node_factory=node_factory,
        contract_snapshot=MagicMock(),
        contract_snapshot_factory=MagicMock(),
        validators_snapshot=SimpleNamespace(
            nodes=[
                _snapshot_node(address)
                for address in (snapshot_addresses or ["leader", *validators])
            ]
        ),
        genvm_manager=MagicMock(),
        shared_decoded_value_cache={},
        shared_contract_snapshot_cache={},
        leader={"address": "leader"},
        remaining_validators=[{"address": address} for address in validators],
        consensus_data=ConsensusData(votes={}, leader_receipt=None, validators=[]),
        validation_results=[],
    )


@pytest.mark.asyncio
async def test_outer_timeout_does_not_trigger_replacement(monkeypatch):
    """Replacement is no longer driven by the outer timeout receipt's fatal flag."""
    monkeypatch.setenv("CONSENSUS_VALIDATOR_EXEC_TIMEOUT_SECONDS", "0.02")
    calls: list[str] = []

    async def exec_hung(_transaction):
        await asyncio.sleep(0.2)
        return _make_receipt("validator-1", Vote.AGREE)

    async def exec_replacement(_transaction):
        return _make_receipt("replacement", Vote.AGREE)

    def node_factory(validator, *_args):
        calls.append(validator["address"])
        if validator["address"] == "validator-1":
            return SimpleNamespace(exec_transaction=exec_hung)
        return SimpleNamespace(exec_transaction=exec_replacement)

    context = _make_context(
        validators=["validator-1"],
        snapshot_addresses=["leader", "validator-1", "replacement"],
        node_factory=node_factory,
    )

    await CommittingState().handle(context)

    assert "replacement" not in calls
    timeout_receipt = next(
        r
        for r in context.validation_results
        if r.node_config["address"] == "validator-1"
    )
    assert timeout_receipt.vote == Vote.TIMEOUT
    assert timeout_receipt.genvm_result["raw_error"]["fatal"] is False


@pytest.mark.asyncio
async def test_fatal_internal_error_still_replaces_within_slot(monkeypatch):
    """Fatal GenVM internal errors still use replacement validators within budget."""
    monkeypatch.setenv("CONSENSUS_VALIDATOR_EXEC_TIMEOUT_SECONDS", "1")
    calls: list[str] = []

    async def exec_validator(_transaction):
        raise GenVMInternalError(
            message="fatal",
            error_code=GenVMErrorCode.LLM_NO_PROVIDER,
            causes=["NO_PROVIDER_FOR_PROMPT"],
            is_fatal=True,
        )

    async def exec_replacement(_transaction):
        return _make_receipt("replacement", Vote.AGREE)

    def node_factory(validator, *_args):
        calls.append(validator["address"])
        if validator["address"] == "validator-1":
            return SimpleNamespace(exec_transaction=exec_validator)
        return SimpleNamespace(exec_transaction=exec_replacement)

    context = _make_context(
        validators=["validator-1"],
        snapshot_addresses=["leader", "validator-1", "replacement"],
        node_factory=node_factory,
    )

    await CommittingState().handle(context)

    assert calls == ["validator-1", "replacement"]
    assert context.validation_results[0].node_config["address"] == "replacement"
    assert context.validation_results[0].vote == Vote.AGREE


@pytest.mark.asyncio
async def test_quorum_short_circuits_pending_validators(monkeypatch):
    monkeypatch.setenv("CONSENSUS_VALIDATOR_EXEC_TIMEOUT_SECONDS", "1")

    async def exec_fast(_transaction, address: str):
        return _make_receipt(address, Vote.AGREE)

    async def exec_slow(_transaction):
        await asyncio.sleep(1)
        return _make_receipt("slow", Vote.DISAGREE)

    def node_factory(validator, *_args):
        if validator["address"] in {"validator-4", "validator-5"}:
            return SimpleNamespace(exec_transaction=exec_slow)
        return SimpleNamespace(
            exec_transaction=lambda transaction, address=validator[
                "address"
            ]: exec_fast(transaction, address)
        )

    context = _make_context(
        validators=[
            "validator-1",
            "validator-2",
            "validator-3",
            "validator-4",
            "validator-5",
        ],
        node_factory=node_factory,
    )

    start = asyncio.get_running_loop().time()
    await CommittingState().handle(context)
    elapsed = asyncio.get_running_loop().time() - start

    assert elapsed < 0.2
    assert len(context.validation_results) == 5
    votes = [result.vote for result in context.validation_results]
    assert votes.count(Vote.AGREE) == 3
    assert votes.count(Vote.IDLE) == 2


@pytest.mark.asyncio
async def test_no_quorum_replaces_timeout_slots_only(monkeypatch):
    monkeypatch.setenv("CONSENSUS_VALIDATOR_EXEC_TIMEOUT_SECONDS", "0.02")
    calls: list[str] = []

    async def exec_hung(_transaction):
        await asyncio.sleep(0.2)
        return _make_receipt("validator-1", Vote.AGREE)

    async def exec_vote(address: str, vote: Vote):
        return _make_receipt(address, vote)

    async def exec_replacement(_transaction):
        return _make_receipt("replacement", Vote.AGREE)

    def node_factory(validator, *_args):
        calls.append(validator["address"])
        if validator["address"] == "validator-1":
            return SimpleNamespace(exec_transaction=exec_hung)
        if validator["address"] == "replacement":
            return SimpleNamespace(exec_transaction=exec_replacement)
        vote = Vote.AGREE if validator["address"] == "validator-4" else Vote.DISAGREE
        return SimpleNamespace(
            exec_transaction=lambda _transaction, address=validator["address"]: (
                exec_vote(address, vote)
            )
        )

    context = _make_context(
        validators=["validator-1", "validator-2", "validator-3", "validator-4"],
        snapshot_addresses=[
            "leader",
            "validator-1",
            "validator-2",
            "validator-3",
            "validator-4",
            "replacement",
        ],
        node_factory=node_factory,
    )

    await CommittingState().handle(context)

    assert calls.count("validator-2") == 1
    assert calls.count("validator-3") == 1
    assert calls.count("validator-4") == 1
    assert "replacement" in calls
    assert [result.node_config["address"] for result in context.validation_results] == [
        "replacement",
        "validator-2",
        "validator-3",
        "validator-4",
    ]
