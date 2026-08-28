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


@pytest.mark.asyncio
async def test_transient_leader_fatal_during_appeal_preserves_admission_for_retry():
    worker = _make_worker()
    session = MagicMock(spec=Session)

    with (
        patch.dict(os.environ, {"GENVM_TRANSIENT_FATAL_BACKOFF_S": "3.0"}),
        patch.object(worker, "reset_transaction") as reset_transaction,
        patch.object(worker, "release_transaction") as release_transaction,
        patch.object(
            worker, "_handle_leader_crash_retry", new_callable=AsyncMock
        ) as handle_leader_crash_retry,
        patch.object(
            worker, "_handle_generic_error_retry", new_callable=AsyncMock
        ) as handle_generic_error_retry,
        patch(
            "backend.consensus.worker.asyncio.sleep", new_callable=AsyncMock
        ) as sleep,
    ):
        async with worker._transaction_context(
            "0xtx", {"appealed": True}, session, "appeal"
        ):
            raise _fatal_error(causes=["NO_PROVIDER_FOR_PROMPT"])

    session.rollback.assert_called_once()
    reset_transaction.assert_not_called()
    handle_leader_crash_retry.assert_not_awaited()
    handle_generic_error_retry.assert_awaited_once()
    assert handle_generic_error_retry.await_args.kwargs == {
        "cancel_on_exhaustion": False
    }
    release_transaction.assert_called_once()
    sleep.assert_awaited_once_with(3.0)
    assert worker.running is True


@pytest.mark.asyncio
async def test_non_transient_leader_fatal_during_appeal_stops_without_reset():
    worker = _make_worker()
    session = MagicMock(spec=Session)

    with (
        patch.object(worker, "reset_transaction") as reset_transaction,
        patch.object(worker, "release_transaction") as release_transaction,
        patch.object(
            worker, "_handle_leader_crash_retry", new_callable=AsyncMock
        ) as handle_leader_crash_retry,
        patch.object(
            worker, "_handle_generic_error_retry", new_callable=AsyncMock
        ) as handle_generic_error_retry,
    ):
        async with worker._transaction_context(
            "0xtx", {"appealed": True}, session, "appeal"
        ):
            raise _fatal_error(causes=["SOMETHING_ELSE"])

    session.rollback.assert_called_once()
    reset_transaction.assert_not_called()
    handle_leader_crash_retry.assert_not_awaited()
    handle_generic_error_retry.assert_awaited_once()
    assert handle_generic_error_retry.await_args.kwargs == {
        "cancel_on_exhaustion": False
    }
    release_transaction.assert_called_once()
    assert worker.running is False


@pytest.mark.asyncio
async def test_unclassified_leader_crash_during_appeal_never_synthesizes_result():
    worker = _make_worker()
    session = MagicMock(spec=Session)
    error = GenVMInternalError(
        message="hard crash",
        error_code=None,
        causes=[],
        is_fatal=False,
        is_leader=True,
        ctx=None,
        detail="trap",
    )

    with (
        patch.object(worker, "reset_transaction") as reset_transaction,
        patch.object(worker, "release_transaction") as release_transaction,
        patch.object(
            worker, "_handle_leader_crash_retry", new_callable=AsyncMock
        ) as handle_leader_crash_retry,
        patch.object(
            worker, "_handle_generic_error_retry", new_callable=AsyncMock
        ) as handle_generic_error_retry,
    ):
        async with worker._transaction_context(
            "0xtx", {"appealed": True}, session, "appeal"
        ):
            raise error

    session.rollback.assert_called_once()
    reset_transaction.assert_not_called()
    handle_leader_crash_retry.assert_not_awaited()
    handle_generic_error_retry.assert_awaited_once()
    assert handle_generic_error_retry.await_args.kwargs == {
        "cancel_on_exhaustion": False
    }
    release_transaction.assert_called_once()
    assert worker.running is True


@pytest.mark.asyncio
async def test_generic_appeal_failure_never_cancels_the_agreed_transaction():
    worker = _make_worker()
    worker._generic_error_base_backoff = 0

    for attempt in range(worker.MAX_GENERIC_ERROR_RETRIES + 3):
        await worker._handle_generic_error_retry(
            "0xtx",
            RuntimeError(f"appeal infrastructure failure {attempt}"),
            cancel_on_exhaustion=False,
        )

    assert worker._generic_error_retries["0xtx"]["count"] == (
        worker.MAX_GENERIC_ERROR_RETRIES + 3
    )


@pytest.mark.asyncio
async def test_paid_appeal_claim_honors_retry_backoff():
    worker = _make_worker()
    session = MagicMock(spec=Session)
    worker.claim_next_appeal = AsyncMock(return_value={"hash": "0xtx"})
    worker.claim_next_finalization = AsyncMock(return_value=None)
    worker.claim_next_transaction = AsyncMock(return_value=None)
    worker._generic_error_retries["0xtx"] = {
        "count": 1,
        "last_attempt": 100.0,
        "last_error": "boom",
    }

    with (
        patch("backend.consensus.worker.time.time", return_value=100.0),
        patch.object(worker, "release_transaction") as release_transaction,
        patch("backend.consensus.worker.asyncio.create_task") as create_task,
    ):
        assert await worker._try_claim_work(session) is False

    release_transaction.assert_called_once_with(session, "0xtx")
    create_task.assert_not_called()


@pytest.mark.asyncio
async def test_generic_appeal_error_is_retryable_without_cancellation():
    worker = _make_worker()
    session = MagicMock(spec=Session)

    with (
        patch.object(worker, "release_transaction") as release_transaction,
        patch.object(
            worker, "_handle_generic_error_retry", new_callable=AsyncMock
        ) as handle_generic_error_retry,
    ):
        async with worker._transaction_context(
            "0xtx", {"appealed": True}, session, "appeal"
        ):
            raise RuntimeError("temporary worker failure")

    session.rollback.assert_called_once()
    handle_generic_error_retry.assert_awaited_once()
    assert handle_generic_error_retry.await_args.kwargs == {
        "cancel_on_exhaustion": False
    }
    release_transaction.assert_called_once()


@pytest.mark.asyncio
async def test_terminal_recomputation_error_restores_paid_appeal_without_cancellation():
    worker = _make_worker()
    session = MagicMock(spec=Session)
    transaction_data = {"data": {"appealRecoverySnapshot": {"status": "ACCEPTED"}}}

    with (
        patch.object(
            worker, "_restore_admitted_appeal_for_retry", return_value=True
        ) as restore_appeal,
        patch.object(worker, "release_transaction") as release_transaction,
        patch.object(
            worker, "_handle_generic_error_retry", new_callable=AsyncMock
        ) as handle_generic_error_retry,
    ):
        async with worker._transaction_context(
            "0xtx", transaction_data, session, "transaction"
        ):
            raise RuntimeError("terminal recomputation crashed")

    session.rollback.assert_called_once()
    restore_appeal.assert_called_once_with("0xtx")
    handle_generic_error_retry.assert_awaited_once()
    assert handle_generic_error_retry.await_args.kwargs == {
        "cancel_on_exhaustion": False
    }
    release_transaction.assert_called_once()
