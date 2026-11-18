"""
DXP-726: when a worker drops in-flight work (before a snapshot restore), the
whole consensus task tree must stop — including the validator/leader execution
tasks spawned by the state handlers — and finish its cleanup BEFORE the claim
is released. Otherwise orphaned validator tasks keep running and write (e.g.
add_state_timestamp → consensus_history commit) into the restored state.

Uses the real CommittingState.handle(); only node execution and DB writes are
faked.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from backend.consensus.base import (
    CommittingState,
    _cancel_and_wait,
    _wait_owning,
)
from backend.database_handler.types import ConsensusData
from backend.node.genvm.origin.public_abi import ResultCode
from backend.node.types import ExecutionMode, ExecutionResultStatus, Receipt, Vote


def _receipt(address: str) -> Receipt:
    return Receipt(
        result=bytes([ResultCode.RETURN]) + b"ok",
        calldata=b"",
        gas_used=0,
        mode=ExecutionMode.VALIDATOR,
        contract_state={},
        node_config={"address": address},
        execution_result=ExecutionResultStatus.SUCCESS,
        vote=Vote.AGREE,
        genvm_result={"raw_error": {"fatal": False}},
    )


def _snapshot_node(address: str) -> SimpleNamespace:
    validator = MagicMock()
    validator.to_dict.return_value = {"address": address}
    return SimpleNamespace(validator=validator)


class _Recorder:
    """Records DB writes and lifecycle events in order."""

    def __init__(self):
        self.events: list[str] = []
        self.released = False
        self.writes_after_release: list[str] = []

    def write(self, what: str):
        self.events.append(f"write:{what}")
        if self.released:
            self.writes_after_release.append(what)


def _make_worker(recorder: _Recorder):
    from backend.consensus.worker import ConsensusWorker

    def get_session():
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=MagicMock(spec=Session))
        ctx.__exit__ = MagicMock(return_value=None)
        return ctx

    worker = ConsensusWorker(
        get_session=get_session,
        msg_handler=MagicMock(),
        consensus_service=MagicMock(),
        validators_manager=MagicMock(),
        genvm_manager=MagicMock(),
        worker_id="test-worker",
    )

    def release_transaction(_session, tx_hash):
        recorder.events.append(f"release:{tx_hash}")
        recorder.released = True

    worker.release_transaction = release_transaction
    worker.release_all_claims = MagicMock(return_value=0)
    return worker


def _make_context(recorder: _Recorder, validators: dict):
    tx_processor = MagicMock()
    tx_processor.add_state_timestamp.side_effect = lambda _h, name: recorder.write(name)
    tx_processor.update_transaction_status.side_effect = lambda *a, **k: recorder.write(
        "update_transaction_status"
    )
    tx_processor.set_transaction_timestamp_last_vote.side_effect = (
        lambda *a, **k: recorder.write("timestamp_last_vote")
    )

    def node_factory(validator, *_args):
        return SimpleNamespace(exec_transaction=validators[validator["address"]])

    return SimpleNamespace(
        transaction=SimpleNamespace(hash="0xtx"),
        transactions_processor=tx_processor,
        msg_handler=SimpleNamespace(send_message=lambda *a, **k: None),
        consensus_service=MagicMock(),
        contract_processor=MagicMock(),
        node_factory=node_factory,
        contract_snapshot=MagicMock(),
        contract_snapshot_factory=MagicMock(),
        validators_snapshot=SimpleNamespace(
            nodes=[_snapshot_node(a) for a in ["leader", *validators]]
        ),
        genvm_manager=MagicMock(),
        shared_decoded_value_cache={},
        shared_contract_snapshot_cache={},
        leader={"address": "leader"},
        remaining_validators=[{"address": a} for a in validators],
        consensus_data=ConsensusData(votes={}, leader_receipt=None, validators=[]),
        validation_results=[],
    )


@pytest.mark.asyncio
async def test_release_in_flight_stops_validator_tasks_before_releasing_claim(
    monkeypatch,
):
    monkeypatch.setenv("CONSENSUS_VALIDATOR_EXEC_TIMEOUT_SECONDS", "30")
    recorder = _Recorder()
    worker = _make_worker(recorder)
    running = asyncio.Event()
    started = 0
    finish = asyncio.Event()  # "GenVM finished" — would complete the validators

    def hung_validator(address):
        async def exec_transaction(_transaction):
            nonlocal started
            started += 1
            if started == 3:
                running.set()
            try:
                await finish.wait()
                return _receipt(address)
            except asyncio.CancelledError:
                # GenVM cleanup takes a while (e.g. telling the manager to stop)
                await asyncio.sleep(0.05)
                recorder.events.append(f"child-cleaned:{address}")
                raise

        return exec_transaction

    validators = {a: hung_validator(a) for a in ("v1", "v2", "v3")}
    context = _make_context(recorder, validators)

    async def process():
        async with worker._transaction_context("0xtx", {}, MagicMock(spec=Session)):
            await CommittingState().handle(context)
            recorder.write("after-committing")

    task = asyncio.create_task(process())
    worker._active_tasks.add(task)
    await asyncio.wait_for(running.wait(), 2)

    await worker.release_in_flight()

    # Every validator child finished its cleanup before the claim was released.
    release_at = recorder.events.index("release:0xtx")
    cleaned = [i for i, e in enumerate(recorder.events) if e.startswith("child-")]
    assert len(cleaned) == 3
    assert all(i < release_at for i in cleaned)

    # The DB is now "restored". Let anything that survived run to completion.
    finish.set()
    await asyncio.sleep(0.2)

    assert recorder.writes_after_release == []
    assert not any(
        t for t in asyncio.all_tasks() if "exec_transaction" in repr(t.get_coro())
    )


@pytest.mark.asyncio
async def test_wait_owning_cancels_and_awaits_children_when_waiter_is_cancelled():
    cleaned = []

    async def child():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(0.02)
            cleaned.append(True)
            raise

    children = {asyncio.create_task(child())}

    waiter = asyncio.create_task(
        _wait_owning(children, return_when=asyncio.FIRST_COMPLETED)
    )
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert cleaned == [True]
    assert all(c.done() for c in children)


@pytest.mark.asyncio
async def test_parent_cancel_during_child_reap_is_not_swallowed(monkeypatch):
    """The slot-timeout path reaps the timed-out child with a broad
    `except CancelledError`; a cancellation of the parent arriving during that
    reap must still stop the parent (no VALIDATOR_TIMEOUT/END writes)."""
    monkeypatch.setenv("CONSENSUS_VALIDATOR_EXEC_TIMEOUT_SECONDS", "0.02")
    recorder = _Recorder()
    reaping = asyncio.Event()

    async def slow_to_die(_transaction):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            reaping.set()
            await asyncio.sleep(0.05)  # parent gets cancelled meanwhile
            raise

    context = _make_context(recorder, {"v1": slow_to_die})
    parent = asyncio.create_task(CommittingState().handle(context))
    await asyncio.wait_for(reaping.wait(), 2)
    recorder.released = True  # anything from now on is a stale write
    parent.cancel()

    with pytest.raises(asyncio.CancelledError):
        await parent
    await asyncio.sleep(0.1)
    assert recorder.writes_after_release == []


@pytest.mark.asyncio
async def test_cancel_and_wait_ignores_finished_tasks():
    done = asyncio.create_task(asyncio.sleep(0))
    await done
    await _cancel_and_wait([done])
    assert not done.cancelled()
