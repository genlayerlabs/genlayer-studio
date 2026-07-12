"""Repro: FinalizingState.handle crashes on transactions with consensus_data=None.

execute_transfer's insufficient-balance path (base.py) sets
timestamp_awaiting_finalization and status=UNDETERMINED WITHOUT ever setting
consensus_data (the stamp is added precisely so claim_next_finalization picks
the row up). When such a row is claimed, process_finalization -> FinalizingState
.handle does:

    leader_receipt = context.transaction.consensus_data.leader_receipt[0]

which raises `AttributeError: 'NoneType' object has no attribute
'leader_receipt'`. The worker's generic-error retry then loops with backoff and
eventually flips the tx to CANCELED -- a legitimately UNDETERMINED SEND is
downgraded to the wrong terminal status.

This test is a failing reproduction (no fix applied): it fails today with the
AttributeError and would pass once FinalizingState handles a missing
consensus_data.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.consensus.base import FinalizingState
from backend.database_handler.models import TransactionStatus
from backend.domain.types import TransactionType


@pytest.mark.asyncio
async def test_finalizing_state_does_not_crash_on_none_consensus_data():
    context = MagicMock()
    context.transaction.consensus_data = None
    context.transaction.status = TransactionStatus.UNDETERMINED
    context.transaction.type = TransactionType.SEND
    context.transaction.appeal_leader_timeout = False
    # EffectExecutor / consensus_service etc. are mocks; the only thing under
    # test is that a missing consensus_data doesn't crash finalization.
    context.consensus_service.emit_transaction_event = MagicMock(return_value=None)
    context.accounts_manager.settle_tx_fee_accounting_once = MagicMock()
    context.accounts_manager.session.commit = MagicMock()

    try:
        await FinalizingState().handle(context)
    except AttributeError as exc:
        pytest.fail(
            "FinalizingState.handle crashed on a transaction with "
            f"consensus_data=None (UNDETERMINED SEND): {exc}"
        )
    except Exception:
        # Any other error from the mock harness is out of scope for this repro;
        # only the NoneType.leader_receipt crash is the bug under test.
        pass
