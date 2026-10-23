import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from backend.consensus.base import InternalMessageEmissionError
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


def test_acceptance_dispatch_pending_requires_current_unacknowledged_generation():
    from backend.consensus.worker import _acceptance_dispatch_pending

    pending = {
        "data": {
            "fee_accounting": {
                "active_message_generation": {
                    "acceptanceDispatchRequired": True,
                    "acceptanceDispatched": False,
                }
            }
        }
    }

    assert _acceptance_dispatch_pending(pending) is True
    pending["data"]["fee_accounting"]["active_message_generation"][
        "acceptanceDispatched"
    ] = True
    assert _acceptance_dispatch_pending(pending) is False


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
async def test_helper_message_failure_never_cancels_the_agreed_transaction():
    worker = _make_worker()
    session = MagicMock(spec=Session)

    with (
        patch.object(worker, "release_transaction") as release_transaction,
        patch.object(
            worker, "_handle_generic_error_retry", new_callable=AsyncMock
        ) as handle_generic_error_retry,
    ):
        async with worker._transaction_context("0xtx", {}, session, "transaction"):
            raise InternalMessageEmissionError("helper unavailable")

    session.rollback.assert_called_once()
    handle_generic_error_retry.assert_awaited_once()
    assert handle_generic_error_retry.await_args.kwargs == {
        "cancel_on_exhaustion": False
    }
    release_transaction.assert_called_once()


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
async def test_acceptance_repair_finalization_honors_retry_backoff():
    worker = _make_worker()
    session = MagicMock(spec=Session)
    worker.claim_next_appeal = AsyncMock(return_value=None)
    worker.claim_next_finalization = AsyncMock(return_value={"hash": "0xtx"})
    worker.claim_next_transaction = AsyncMock(return_value=None)
    worker._generic_error_retries["0xtx"] = {
        "count": 1,
        "last_attempt": 100.0,
        "last_error": "helper unavailable",
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
async def test_finalization_claim_excludes_backed_off_heads():
    worker = _make_worker()
    session = MagicMock(spec=Session)
    worker.claim_next_appeal = AsyncMock(return_value=None)
    worker.claim_next_finalization = AsyncMock(return_value={"hash": "0xother"})
    worker.claim_next_transaction = AsyncMock(return_value=None)
    worker._generic_error_retries["0xstuck"] = {
        "count": 1,
        "last_attempt": 100.0,
        "last_error": "missing finalization state",
    }

    with (
        patch("backend.consensus.worker.time.time", return_value=100.0),
        patch.object(worker, "release_transaction") as release_transaction,
        patch("backend.consensus.worker.asyncio.create_task") as create_task,
        patch.object(worker, "_process_finalization_task", new=MagicMock()),
    ):
        assert await worker._try_claim_work(session) is True

    worker.claim_next_finalization.assert_awaited_once_with(
        session, exclude_hashes=["0xstuck"]
    )
    release_transaction.assert_not_called()
    create_task.assert_called_once()


def test_backoff_hashes_lists_only_in_window_entries():
    worker = _make_worker()
    worker._no_validators_base_backoff = 30.0
    worker._generic_error_base_backoff = 10.0
    worker._no_validators_retries = {
        "0xnv_active": {"count": 1, "last_attempt": 90.0},
        "0xnv_expired": {"count": 1, "last_attempt": 50.0},
    }
    worker._generic_error_retries = {
        "0xge_active": {"count": 2, "last_attempt": 85.0, "last_error": "x"},
        "0xge_expired": {"count": 1, "last_attempt": 80.0, "last_error": "x"},
    }

    with patch("backend.consensus.worker.time.time", return_value=100.0):
        assert worker._backoff_hashes() == ["0xge_active", "0xnv_active"]
        assert worker._is_in_backoff({"hash": "0xge_active"}) is True
        assert worker._is_in_backoff({"hash": "0xge_expired"}) is False


@pytest.mark.asyncio
async def test_successful_finalization_clears_retry_tracking():
    worker = _make_worker()
    session = MagicMock(spec=Session)
    worker._no_validators_retries["0xtx"] = {"count": 1, "last_attempt": 1.0}
    worker._generic_error_retries["0xtx"] = {
        "count": 2,
        "last_attempt": 1.0,
        "last_error": "boom",
    }
    worker._generic_error_retries["0xother"] = {
        "count": 1,
        "last_attempt": 1.0,
        "last_error": "boom",
    }
    worker.consensus_algorithm = MagicMock()
    worker.consensus_algorithm.can_finalize_transaction.return_value = True
    worker.consensus_algorithm.process_finalization = AsyncMock()
    worker.usage_metrics_service = MagicMock()
    worker.usage_metrics_service.send_finalized_transaction_metrics = AsyncMock()

    with (
        patch(
            "backend.consensus.worker.Transaction.from_dict",
            return_value=MagicMock(hash="0xtx"),
        ),
        patch("backend.consensus.base.transactions_processor_factory"),
        patch.object(worker, "release_transaction"),
    ):
        await worker.process_finalization({"hash": "0xtx"}, session)

    worker.consensus_algorithm.process_finalization.assert_awaited_once()
    assert "0xtx" not in worker._no_validators_retries
    assert "0xtx" not in worker._generic_error_retries
    assert "0xother" in worker._generic_error_retries


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
