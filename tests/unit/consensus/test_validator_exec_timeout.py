import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.consensus.base import CommittingState
from backend.database_handler.types import ConsensusData
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
        eq_outputs={},
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
    monkeypatch.setenv("CONSENSUS_VALIDATOR_EXEC_TIMEOUT_SECONDS", "0.01")

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
    ):
        if validator["address"] == "validator-1":
            return SimpleNamespace(exec_transaction=exec_hung)
        return SimpleNamespace(exec_transaction=exec_fast)

    context = SimpleNamespace(
        transaction=transaction,
        transactions_processor=tx_processor,
        msg_handler=_MessageHandler(),
        consensus_service=consensus_service,
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
    assert elapsed < 0.15

    votes = [r.vote for r in context.validation_results]
    assert votes.count(Vote.IDLE) == 1
    assert votes.count(Vote.AGREE) == 1

    timeout_receipt = next(r for r in context.validation_results if r.vote == Vote.IDLE)
    assert timeout_receipt.genvm_result is not None
    assert (
        timeout_receipt.genvm_result["error_code"] == "CONSENSUS_VALIDATOR_EXEC_TIMEOUT"
    )
    assert timeout_receipt.genvm_result["raw_error"]["causes"] == [
        "VALIDATOR_EXEC_TIMEOUT"
    ]

    timeout_timestamps = [
        call.args[1]
        for call in tx_processor.add_state_timestamp.call_args_list
        if "_TIMEOUT" in call.args[1]
    ]
    assert timeout_timestamps
