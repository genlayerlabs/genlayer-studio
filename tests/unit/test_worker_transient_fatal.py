import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from backend.node.genvm.error_codes import GenVMInternalError


def _make_worker():
    from backend.consensus.worker import ConsensusWorker

    def get_session_side_effect():
        ctx = MagicMock()
        inner_session = MagicMock(spec=Session)
        ctx.__enter__ = MagicMock(return_value=inner_session)
        ctx.__exit__ = MagicMock(return_value=None)
        return ctx

    return ConsensusWorker(
        get_session=get_session_side_effect,
        msg_handler=MagicMock(),
        consensus_service=MagicMock(),
        validators_manager=MagicMock(),
        genvm_manager=MagicMock(),
        worker_id="test-worker",
    )


def _fatal_error(*, causes, is_leader=True):
    return GenVMInternalError(
        message="x",
        error_code="LLM_NO_PROVIDER",
        causes=causes,
        is_fatal=True,
        is_leader=is_leader,
        ctx=None,
        detail=None,
    )


@pytest.mark.asyncio
async def test_transient_leader_fatal_resets_and_keeps_worker_alive():
    worker = _make_worker()
    session = MagicMock(spec=Session)

    with (
        patch.dict(os.environ, {"GENVM_TRANSIENT_FATAL_BACKOFF_S": "3.0"}),
        patch.object(worker, "reset_transaction") as reset_transaction,
        patch.object(worker, "release_transaction") as release_transaction,
        patch(
            "backend.consensus.worker.asyncio.sleep", new_callable=AsyncMock
        ) as sleep,
    ):
        async with worker._transaction_context("0xtx", {}, session):
            raise _fatal_error(causes=["NO_PROVIDER_FOR_PROMPT"])

    assert worker.running is True
    reset_transaction.assert_called_once()
    release_transaction.assert_not_called()
    sleep.assert_awaited_once_with(3.0)


@pytest.mark.asyncio
async def test_non_transient_leader_fatal_stops_worker_without_backoff():
    worker = _make_worker()
    session = MagicMock(spec=Session)

    with (
        patch.object(worker, "reset_transaction") as reset_transaction,
        patch.object(worker, "release_transaction") as release_transaction,
        patch(
            "backend.consensus.worker.asyncio.sleep", new_callable=AsyncMock
        ) as sleep,
    ):
        async with worker._transaction_context("0xtx", {}, session):
            raise _fatal_error(causes=["SOMETHING_ELSE"])

    assert worker.running is False
    reset_transaction.assert_called_once()
    release_transaction.assert_not_called()
    assert 3.0 not in [call.args[0] for call in sleep.await_args_list]


@pytest.mark.asyncio
async def test_validator_fatal_keeps_worker_alive_without_reset():
    worker = _make_worker()
    session = MagicMock(spec=Session)

    with (
        patch.object(worker, "reset_transaction") as reset_transaction,
        patch.object(worker, "release_transaction") as release_transaction,
        patch(
            "backend.consensus.worker.asyncio.sleep", new_callable=AsyncMock
        ) as sleep,
    ):
        async with worker._transaction_context("0xtx", {}, session):
            raise _fatal_error(causes=["NO_PROVIDER_FOR_PROMPT"], is_leader=False)

    assert worker.running is True
    reset_transaction.assert_not_called()
    release_transaction.assert_called_once()
    sleep.assert_not_awaited()
