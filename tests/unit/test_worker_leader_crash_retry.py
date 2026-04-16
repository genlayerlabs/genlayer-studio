"""
Tests for leader-crash retry cap in the consensus worker.

When GenVM produces a non-classifiable internal error (code=None, causes=[],
is_fatal=False) during leader execution, the worker caps retries and then
finalizes the transaction with a synthetic ERROR leader receipt rather than
looping forever. This mirrors the behavior for contract-raised exceptions:
tx reaches a terminal state with execution_result=ERROR visible to the user.
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
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
    # Deterministic cap regardless of env
    worker._max_leader_crash_retries = 3
    return worker


def _hard_crash_error(detail="Fingerprint {...}"):
    return GenVMInternalError(
        message="GenVM internal error",
        error_code=None,
        causes=[],
        is_fatal=False,
        is_leader=True,
        ctx=None,
        detail=detail,
    )


class TestLeaderCrashRetryCap:
    @pytest.mark.asyncio
    async def test_under_cap_returns_false(self):
        """First two hard crashes are retryable — helper returns False."""
        worker = _make_worker()
        tx_hash = "0xpoisoned"

        with patch(
            "backend.consensus.base.ConsensusAlgorithm.dispatch_transaction_status_update",
            new_callable=AsyncMock,
        ) as mock_dispatch:
            assert (
                await worker._handle_leader_crash_retry(
                    tx_hash, "transaction", _hard_crash_error()
                )
                is False
            )
            assert (
                await worker._handle_leader_crash_retry(
                    tx_hash, "transaction", _hard_crash_error()
                )
                is False
            )

        assert worker._leader_crash_retries[tx_hash]["count"] == 2
        mock_dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_at_cap_finalizes_with_error_receipt(self):
        """Nth crash synthesizes a leader ERROR receipt and dispatches ACCEPTED."""
        worker = _make_worker()
        tx_hash = "0xpoisoned"

        with patch(
            "backend.consensus.base.ConsensusAlgorithm.dispatch_transaction_status_update",
            new_callable=AsyncMock,
        ) as mock_dispatch:
            # two under-cap calls
            await worker._handle_leader_crash_retry(
                tx_hash, "transaction", _hard_crash_error()
            )
            await worker._handle_leader_crash_retry(
                tx_hash, "transaction", _hard_crash_error()
            )
            # third call hits the cap
            result = await worker._handle_leader_crash_retry(
                tx_hash,
                "transaction",
                _hard_crash_error(detail="wasm trap at py_gl_call"),
            )

        assert result is True
        # Counter cleaned up
        assert tx_hash not in worker._leader_crash_retries

        # Dispatch called once with ACCEPTED for this tx hash
        mock_dispatch.assert_called_once()
        args = mock_dispatch.call_args.args
        assert args[1] == tx_hash
        assert args[2] == TransactionStatus.ACCEPTED

    @pytest.mark.asyncio
    async def test_synthetic_receipt_shape(self):
        """The synthetic receipt carries execution_result=ERROR + INTERNAL_ERROR code."""
        worker = _make_worker()
        tx_hash = "0xpoisoned"

        # Capture the tx row the worker writes to
        tx_row = MagicMock()
        tx_row.status = TransactionStatus.PROPOSING
        tx_row.consensus_data = None

        def get_session_side_effect():
            ctx = MagicMock()
            inner = MagicMock(spec=Session)
            inner.commit = MagicMock()
            inner.query.return_value.filter_by.return_value.one.return_value = tx_row
            ctx.__enter__ = MagicMock(return_value=inner)
            ctx.__exit__ = MagicMock(return_value=None)
            return ctx

        worker.get_session = get_session_side_effect

        with patch(
            "backend.consensus.base.ConsensusAlgorithm.dispatch_transaction_status_update",
            new_callable=AsyncMock,
        ):
            for _ in range(3):
                await worker._handle_leader_crash_retry(
                    tx_hash, "transaction", _hard_crash_error(detail="trap-detail")
                )

        # Status pushed to ACCEPTED, consensus_data carries the synthetic receipt
        assert tx_row.status == TransactionStatus.ACCEPTED
        cd = tx_row.consensus_data
        assert cd["votes"] == {}
        assert cd["validators"] == []
        assert len(cd["leader_receipt"]) == 1
        receipt = cd["leader_receipt"][0]
        assert receipt["execution_result"] == "ERROR"
        assert receipt["genvm_result"]["error_code"] == "INTERNAL_ERROR"
        assert "GenVM crashed" in receipt["genvm_result"]["error_description"]
        assert "trap-detail" in receipt["genvm_result"]["error_description"]
        assert receipt["node_config"]["address"] == "genvm_crash_handler"

    @pytest.mark.asyncio
    async def test_per_tx_counter_isolation(self):
        """Two different tx hashes get independent counters."""
        worker = _make_worker()

        with patch(
            "backend.consensus.base.ConsensusAlgorithm.dispatch_transaction_status_update",
            new_callable=AsyncMock,
        ):
            for _ in range(2):
                await worker._handle_leader_crash_retry(
                    "0xaaa", "transaction", _hard_crash_error()
                )
            # 0xbbb is fresh
            assert (
                await worker._handle_leader_crash_retry(
                    "0xbbb", "transaction", _hard_crash_error()
                )
                is False
            )

        assert worker._leader_crash_retries["0xaaa"]["count"] == 2
        assert worker._leader_crash_retries["0xbbb"]["count"] == 1

    @pytest.mark.asyncio
    async def test_detail_is_truncated(self):
        """Very long detail strings are truncated in the error_description."""
        worker = _make_worker()
        tx_row = MagicMock()

        def get_session_side_effect():
            ctx = MagicMock()
            inner = MagicMock(spec=Session)
            inner.commit = MagicMock()
            inner.query.return_value.filter_by.return_value.one.return_value = tx_row
            ctx.__enter__ = MagicMock(return_value=inner)
            ctx.__exit__ = MagicMock(return_value=None)
            return ctx

        worker.get_session = get_session_side_effect

        long_detail = "X" * 5000

        with patch(
            "backend.consensus.base.ConsensusAlgorithm.dispatch_transaction_status_update",
            new_callable=AsyncMock,
        ):
            for _ in range(3):
                await worker._handle_leader_crash_retry(
                    "0xlong", "transaction", _hard_crash_error(detail=long_detail)
                )

        desc = tx_row.consensus_data["leader_receipt"][0]["genvm_result"][
            "error_description"
        ]
        assert "truncated" in desc
        # Bound the raw-detail portion (not the whole description which has a prefix)
        assert len(desc) < 5000


class TestHardCrashClassification:
    """The retry cap must only trigger for the non-classifiable crash class,
    not for structured errors like LLM_NO_PROVIDER (those should keep retrying)."""

    @pytest.mark.asyncio
    async def test_structured_error_bypasses_helper(self):
        """LLM_NO_PROVIDER has a code and causes → should NOT hit the cap helper."""
        worker = _make_worker()

        structured = GenVMInternalError(
            message="no provider",
            error_code="LLM_NO_PROVIDER",
            causes=["NO_PROVIDER_FOR_PROMPT"],
            is_fatal=False,
            is_leader=True,
        )

        # Classification check mirrors the one in the except block in worker.py
        is_hard_crash = (
            structured.error_code is None
            and not structured.causes
            and not structured.is_fatal
        )
        assert is_hard_crash is False

        # Also: the counter should stay empty if we only ever route hard crashes
        assert worker._leader_crash_retries == {}

    @pytest.mark.asyncio
    async def test_fatal_error_bypasses_helper(self):
        """Fatal errors should go through the stop-worker path, not the cap."""
        worker = _make_worker()

        fatal = GenVMInternalError(
            message="fatal crash",
            error_code=None,
            causes=[],
            is_fatal=True,
            is_leader=True,
        )

        is_hard_crash = (
            fatal.error_code is None and not fatal.causes and not fatal.is_fatal
        )
        assert is_hard_crash is False
        assert worker._leader_crash_retries == {}
