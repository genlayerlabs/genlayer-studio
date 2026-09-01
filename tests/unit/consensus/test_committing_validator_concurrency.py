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
        execution_result=ExecutionResultStatus.SUCCESS,
        vote=vote,
        genvm_result={"raw_error": {"fatal": False}},
    )


def _snapshot_node(address: str) -> SimpleNamespace:
    validator = MagicMock()
    validator.to_dict.return_value = {"address": address}
    return SimpleNamespace(validator=validator)


def _build_context(validator_addresses, node_factory):
    transaction = SimpleNamespace(hash="tx-hash")

    tx_processor = MagicMock()
    tx_processor.add_state_timestamp = MagicMock()
    tx_processor.update_transaction_status = MagicMock()
    tx_processor.set_transaction_timestamp_last_vote = MagicMock()

    return SimpleNamespace(
        transaction=transaction,
        transactions_processor=tx_processor,
        msg_handler=_MessageHandler(),
        consensus_service=MagicMock(),
        contract_processor=MagicMock(),
        node_factory=node_factory,
        contract_snapshot=MagicMock(),
        contract_snapshot_factory=MagicMock(),
        validators_snapshot=SimpleNamespace(
            nodes=[_snapshot_node("leader")]
            + [_snapshot_node(addr) for addr in validator_addresses]
        ),
        genvm_manager=MagicMock(),
        shared_decoded_value_cache={},
        shared_contract_snapshot_cache={},
        leader={"address": "leader"},
        remaining_validators=[{"address": addr} for addr in validator_addresses],
        consensus_data=ConsensusData(votes={}, leader_receipt=None, validators=[]),
        validation_results=[],
    )


@pytest.mark.asyncio
async def test_committing_respects_env_configured_concurrency(monkeypatch):
    """CONSENSUS_VALIDATOR_CONCURRENCY must actually bound concurrent validator
    execution. Before the fix, the semaphore size was hardcoded to 8 and this
    env var was silently ignored -- with a limit of 2 and 5 validators, more
    than 2 would run at once and this test would fail."""
    monkeypatch.setenv("CONSENSUS_VALIDATOR_CONCURRENCY", "2")

    validator_addresses = [f"validator-{i}" for i in range(5)]

    current_concurrent = 0
    max_concurrent = 0
    lock = asyncio.Lock()

    async def exec_transaction(_transaction):
        nonlocal current_concurrent, max_concurrent
        async with lock:
            current_concurrent += 1
            max_concurrent = max(max_concurrent, current_concurrent)
        await asyncio.sleep(0.05)
        async with lock:
            current_concurrent -= 1
        return _make_receipt("validator", Vote.AGREE)

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
        return SimpleNamespace(exec_transaction=exec_transaction)

    context = _build_context(validator_addresses, node_factory)

    await CommittingState().handle(context)

    assert (
        max_concurrent <= 2
    ), f"expected at most 2 validators executing concurrently, got {max_concurrent}"


@pytest.mark.asyncio
async def test_committing_defaults_to_eight_when_env_unset(monkeypatch):
    monkeypatch.delenv("CONSENSUS_VALIDATOR_CONCURRENCY", raising=False)

    from backend.consensus.base import _validator_concurrency

    assert _validator_concurrency() == 8


@pytest.mark.parametrize("raw_value", ["0", "-3", "not-a-number"])
@pytest.mark.asyncio
async def test_committing_falls_back_to_default_on_invalid_env(monkeypatch, raw_value):
    monkeypatch.setenv("CONSENSUS_VALIDATOR_CONCURRENCY", raw_value)

    from backend.consensus.base import _validator_concurrency

    assert _validator_concurrency() == 8
