"""Cancelling a transaction's processing task must stop its execution tree.

sim_resetStudioState (DXP-725) cancels worker tasks. Leader and validator
GenVM runs are child tasks awaited via asyncio.wait, which does not propagate
cancellation, so the consensus states must cancel and drain them themselves.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.consensus.base import (
    CommittingState,
    ProposingState,
    _wait_owning_children,
)
from backend.database_handler.types import ConsensusData
from backend.domain.types import TransactionExecutionMode


class _MessageHandler:
    def send_message(self, *_args, **_kwargs):
        return None


class _Execution:
    """Fake node execution that blocks until cancelled and records it."""

    def __init__(self):
        self.started = asyncio.Event()
        self.cancelled = False
        self.finished = False

    async def exec_transaction(self, _transaction):
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            # Simulate GenVM teardown (DELETE /genvm) taking a little while.
            await asyncio.sleep(0.01)
            raise
        finally:
            self.finished = True


def _snapshot_node(address: str) -> SimpleNamespace:
    validator = MagicMock()
    validator.to_dict.return_value = {"address": address}
    return SimpleNamespace(validator=validator)


def _context(addresses: list[str], executions: dict[str, _Execution]):
    def node_factory(validator, *_args):
        return SimpleNamespace(
            exec_transaction=executions[validator["address"]].exec_transaction
        )

    tx_processor = MagicMock()
    return SimpleNamespace(
        transaction=SimpleNamespace(
            hash="tx-hash", execution_mode=TransactionExecutionMode.NORMAL
        ),
        transactions_processor=tx_processor,
        msg_handler=_MessageHandler(),
        consensus_service=MagicMock(),
        contract_processor=MagicMock(),
        node_factory=node_factory,
        contract_snapshot={},
        contract_snapshot_factory=MagicMock(),
        validators_snapshot=SimpleNamespace(
            nodes=[_snapshot_node(a) for a in addresses]
        ),
        genvm_manager=MagicMock(),
        shared_decoded_value_cache={},
        shared_contract_snapshot_cache={},
        involved_validators=[{"address": a} for a in addresses],
        leader={"address": addresses[0]},
        remaining_validators=[{"address": a} for a in addresses[1:]],
        consensus_data=ConsensusData(votes={}, leader_receipt=None, validators=[]),
        validation_results=[],
    )


async def _cancel_and_wait(task: asyncio.Task) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_cancel_during_leader_execution_stops_leader_genvm():
    executions = {"leader": _Execution(), "v1": _Execution()}
    context = _context(["leader", "v1"], executions)

    with (
        patch("backend.consensus.base.prepare_proposing", return_value=[]),
        patch(
            "backend.consensus.base.EffectExecutor",
            return_value=SimpleNamespace(execute=AsyncMock()),
        ),
        patch("backend.consensus.base.random.shuffle"),
    ):
        task = asyncio.create_task(ProposingState().handle(context))
        await asyncio.wait_for(executions["leader"].started.wait(), timeout=1)

        await _cancel_and_wait(task)

    # The leader's GenVM run was cancelled and fully finished before the
    # cancellation reached the worker.
    assert executions["leader"].cancelled
    assert executions["leader"].finished


@pytest.mark.asyncio
async def test_cancel_during_validator_execution_stops_all_validators(monkeypatch):
    monkeypatch.setenv("CONSENSUS_VALIDATOR_EXEC_TIMEOUT_SECONDS", "60")
    validators = ["v1", "v2", "v3"]
    executions = {a: _Execution() for a in validators}
    context = _context(["leader", *validators], executions)
    context.remaining_validators = [{"address": a} for a in validators]

    task = asyncio.create_task(CommittingState().handle(context))
    await asyncio.wait_for(
        asyncio.gather(*(e.started.wait() for e in executions.values())),
        timeout=1,
    )

    await _cancel_and_wait(task)

    for address, execution in executions.items():
        assert execution.cancelled, address
        assert execution.finished, address


@pytest.mark.asyncio
async def test_wait_owning_children_drains_children_on_cancel():
    execution = _Execution()
    child = asyncio.create_task(execution.exec_transaction(None))

    parent = asyncio.create_task(_wait_owning_children({child}, timeout=60))
    await execution.started.wait()
    await _cancel_and_wait(parent)

    assert child.done()
    assert execution.cancelled and execution.finished


@pytest.mark.asyncio
async def test_wait_owning_children_behaves_like_wait_without_cancel():
    child = asyncio.create_task(asyncio.sleep(0, result=7))
    done, pending = await _wait_owning_children({child}, timeout=1)
    assert done == {child} and not pending
    assert child.result() == 7
