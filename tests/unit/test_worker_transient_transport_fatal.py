"""
Tests for transient-transport handling of fatal GenVM leader errors.

GenVM's shared HTTP helper marks every transport-level failure fatal
(SENDING_REQUEST when the request can't be sent, READING_BODY when the
response can't be read). Those fire whenever an upstream — the webdriver or
an LLM endpoint — is momentarily unreachable, which says nothing about the
health of the worker that observed it. Stopping the worker there turns one
restarting dependency into every worker restarting at once, so these causes
reset the transaction but leave the worker running. Genuinely fatal causes
still stop the worker.
"""

import pytest
from unittest.mock import MagicMock
from sqlalchemy.orm import Session

from backend.database_handler.models import TransactionStatus
from backend.node.genvm.error_codes import GenVMInternalError


def _make_worker():
    from backend.consensus.worker import ConsensusWorker

    def get_session_side_effect():
        ctx = MagicMock()
        inner = MagicMock(spec=Session)
        inner.commit = MagicMock()
        tx_row = MagicMock()
        tx_row.status = TransactionStatus.PROPOSING
        tx_row.consensus_data = None
        tx_row.timestamp_awaiting_finalization = None
        inner.query.return_value.filter_by.return_value.one.return_value = tx_row
        ctx.__enter__ = MagicMock(return_value=inner)
        ctx.__exit__ = MagicMock(return_value=None)
        return ctx

    worker = ConsensusWorker(
        get_session=get_session_side_effect,
        msg_handler=MagicMock(),
        consensus_service=MagicMock(),
        validators_manager=MagicMock(),
        genvm_manager=MagicMock(),
        worker_id="test-worker",
    )
    worker.running = True
    worker.reset_transaction = MagicMock()
    worker.release_transaction = MagicMock()
    return worker


def _fatal_leader_error(causes):
    """A fatal leader error as GenVM reports it — is_fatal comes from the module."""
    return GenVMInternalError(
        message=f"GenVM internal error: {', '.join(causes)}",
        error_code="INTERNAL_ERROR",
        causes=list(causes),
        is_fatal=True,
        is_leader=True,
        ctx=None,
        detail="ModuleError { causes: [...], fatal: true }",
    )


async def _run_with_error(worker, error):
    """Drive _transaction_context to completion with `error` raised inside it."""
    session = MagicMock(spec=Session)
    async with worker._transaction_context("0xdeadbeef", {}, session):
        raise error


class TestTransientTransportFatal:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("cause", ["SENDING_REQUEST", "READING_BODY"])
    async def test_transport_failure_keeps_worker_running(self, cause):
        """An unreachable upstream resets the tx but must not stop the worker."""
        worker = _make_worker()

        await _run_with_error(worker, _fatal_leader_error([cause]))

        assert worker.running is True, (
            f"{cause} is a transport failure of an upstream, not a broken worker — "
            "stopping here cascades one dead dependency into every worker"
        )
        assert worker.reset_transaction.called, "transaction must still be reset"

    @pytest.mark.asyncio
    async def test_genuine_fatal_still_stops_worker(self):
        """Causes outside the transient set keep the existing stop-and-restart behavior."""
        worker = _make_worker()

        await _run_with_error(worker, _fatal_leader_error(["SOME_OTHER_FATAL_CAUSE"]))

        assert worker.running is False
        assert worker.reset_transaction.called

    @pytest.mark.asyncio
    async def test_mixed_causes_treated_as_transient(self):
        """A transport cause anywhere in the chain is enough to keep the worker alive."""
        worker = _make_worker()

        await _run_with_error(
            worker, _fatal_leader_error(["SENDING_REQUEST", "WEBPAGE_LOAD_FAILED"])
        )

        assert worker.running is True

    @pytest.mark.asyncio
    async def test_validator_error_never_stops_worker(self):
        """Pre-existing behavior: validator-side errors leave consensus to continue."""
        worker = _make_worker()
        error = _fatal_leader_error(["SOME_OTHER_FATAL_CAUSE"])
        error.is_leader = False

        await _run_with_error(worker, error)

        assert worker.running is True
        assert not worker.reset_transaction.called

    def test_transient_set_covers_genvm_transport_causes(self):
        """
        Guards the constant itself: these are the two causes GenVM's shared HTTP
        helper raises with fatal=true (modules/implementation/src/scripting/mod.rs).
        Narrowing the set silently re-opens the cascade.
        """
        from backend.consensus.worker import _TRANSIENT_LEADER_FATAL_CAUSES

        assert {"SENDING_REQUEST", "READING_BODY"} <= _TRANSIENT_LEADER_FATAL_CAUSES
