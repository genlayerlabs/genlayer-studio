"""Regression: ConsensusAlgorithm.can_finalize_transaction crashed with a
TypeError when timestamp_awaiting_finalization is None.

worker.py's claim_next_finalization has a defensive branch that claims rows
whose timestamp_awaiting_finalization IS NULL once they are older than the
stranded threshold (legacy strands). When such a row is processed,
can_finalize_transaction evaluated
`time.time() - transaction.timestamp_awaiting_finalization - ...`
unconditionally, raising `TypeError: unsupported operand type(s) for -: 'float'
and 'NoneType'`. process_finalization then looped through the generic-error
retry and the row was eventually CANCELED -- so the defensive drain branch
could never succeed.
"""

from unittest.mock import MagicMock

from backend.consensus.base import ConsensusAlgorithm
from backend.domain.types import TransactionExecutionMode


def _algo():
    algo = ConsensusAlgorithm.__new__(ConsensusAlgorithm)
    algo.finality_window_time = 30
    algo.finality_window_appeal_failed_reduction = 0.5
    return algo


def test_can_finalize_transaction_with_null_awaiting_finalization():
    algo = _algo()

    tx = MagicMock()
    tx.execution_mode = TransactionExecutionMode.NORMAL
    tx.timestamp_awaiting_finalization = None
    tx.appeal_processing_time = 0
    tx.appeal_failed = 0

    # A NULL-timestamp row claimed for finalization is past the window by
    # definition; must return True, not raise TypeError.
    result = algo.can_finalize_transaction(
        transactions_processor=MagicMock(),
        transaction=tx,
        index=0,
        awaiting_finalization_queue=[],
    )
    assert result is True
