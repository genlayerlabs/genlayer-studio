"""Regression test for `execute_transfer` insufficient-balance stranding.

Bug history (May 2026): native SEND transactions with insufficient sender
balance were dispatched to UNDETERMINED via `dispatch_transaction_status_update`
without setting `timestamp_awaiting_finalization`. `claim_next_finalization`
filters on that field being non-NULL, so the rows sat forever — 16 such rows
accumulated on Studio Prod over 19 days. This test pins the fix in
`backend/consensus/base.py::execute_transfer` so the timestamp is always set
before the status flips.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from unittest.mock import AsyncMock

from backend.consensus.base import ConsensusAlgorithm
from backend.database_handler.accounts_manager import AccountsManager
from backend.database_handler.transactions_processor import (
    TransactionsProcessor,
    TransactionStatus,
)
from backend.domain.types import Transaction, TransactionType, TransactionExecutionMode


SENDER = "0x" + "ab" * 20
RECIPIENT = "0x" + "cd" * 20


def _insert_send_tx(tp: TransactionsProcessor, *, value: int) -> str:
    tx_hash = tp.insert_transaction(
        from_address=SENDER,
        to_address=RECIPIENT,
        data={},
        value=value,
        type=TransactionType.SEND.value,
        nonce=0,
        leader_only=True,
        config_rotation_rounds=0,
        triggered_by_hash=None,
        transaction_hash=("0x" + "ee" * 32),
    )
    tp.session.commit()
    return tx_hash


@pytest.mark.asyncio
async def test_insufficient_balance_send_sets_timestamp_awaiting_finalization(
    session: Session,
):
    """Insufficient-balance SEND must stamp timestamp_awaiting_finalization
    so claim_next_finalization can pick the row up. Without the stamp the
    row is invisible to the finalization claim query forever."""
    tp = TransactionsProcessor(session)
    am = AccountsManager(session)

    # Sender balance = 0, tx asks for 1000 → triggers the insufficient-balance
    # branch in execute_transfer.
    am.update_account_balance(SENDER, 0)
    session.commit()

    tx_hash = _insert_send_tx(tp, value=1000)

    tx = Transaction(
        hash=tx_hash,
        status=TransactionStatus.PENDING,
        type=TransactionType.SEND,
        from_address=SENDER,
        to_address=RECIPIENT,
        value=1000,
        nonce=0,
        leader_only=True,
        execution_mode=TransactionExecutionMode.NORMAL,
    )

    msg_handler = AsyncMock()

    await ConsensusAlgorithm.execute_transfer(
        transaction=tx,
        transactions_processor=tp,
        accounts_manager=am,
        msg_handler=msg_handler,
    )
    session.commit()

    # Read the row directly to bypass any caching in TransactionsProcessor.
    row = session.execute(
        text(
            "SELECT status, timestamp_awaiting_finalization FROM transactions WHERE hash = :h"
        ),
        {"h": tx_hash},
    ).one()

    assert row.status == "UNDETERMINED", (
        f"expected UNDETERMINED, got {row.status} — insufficient-balance "
        "SEND should short-circuit to UNDETERMINED"
    )
    assert row.timestamp_awaiting_finalization is not None, (
        "timestamp_awaiting_finalization must be set on the insufficient-"
        "balance UNDETERMINED path, otherwise claim_next_finalization (which "
        "filters on this column being non-NULL) skips the row forever. "
        "Regression: see Studio Prod stranded-UNDETERMINED rows, May 2026."
    )
    assert row.timestamp_awaiting_finalization > 0


@pytest.mark.asyncio
async def test_sufficient_balance_send_finalizes_without_undetermined(
    session: Session,
):
    """Control: when the sender has enough balance, execute_transfer should
    take the success path and dispatch FINALIZED, not UNDETERMINED. Guards
    against accidental over-broadening of the insufficient-balance branch."""
    tp = TransactionsProcessor(session)
    am = AccountsManager(session)

    am.update_account_balance(SENDER, 5000)
    session.commit()

    tx_hash = _insert_send_tx(tp, value=1000)

    tx = Transaction(
        hash=tx_hash,
        status=TransactionStatus.PENDING,
        type=TransactionType.SEND,
        from_address=SENDER,
        to_address=RECIPIENT,
        value=1000,
        nonce=0,
        leader_only=True,
        execution_mode=TransactionExecutionMode.NORMAL,
    )

    msg_handler = AsyncMock()

    await ConsensusAlgorithm.execute_transfer(
        transaction=tx,
        transactions_processor=tp,
        accounts_manager=am,
        msg_handler=msg_handler,
    )
    session.commit()

    row = session.execute(
        text("SELECT status FROM transactions WHERE hash = :h"),
        {"h": tx_hash},
    ).one()
    assert row.status == "FINALIZED"
