"""Real-session money conservation and crash safety for empty-validator cancellation."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.consensus.base import PendingState
from backend.database_handler.accounts_manager import AccountsManager
from backend.database_handler.invariants import verify_conservation
from backend.database_handler.transactions_processor import TransactionsProcessor
from backend.protocol_rpc.fees import (
    FEE_ACCOUNTING_KEY,
    activate_fee_accounting,
    create_fee_accounting,
)


SENDER = "0x1111111111111111111111111111111111111111"
TARGET = "0x2222222222222222222222222222222222222222"
FEE_SENDER = "0x3333333333333333333333333333333333333333"
TX_HASH = "0x" + "ab" * 32
VALUE = 17
FEES = 1100


def _setup_pending(session, *, activated):
    manager = AccountsManager(session)
    processor = TransactionsProcessor(session)
    for address, balance in [(SENDER, 1000), (TARGET, 100), (FEE_SENDER, 5000)]:
        manager.credit_account_balance(address, balance)
    processor.insert_transaction(
        from_address=None,
        to_address=SENDER,
        data={},
        value=6100,
        type=0,
        nonce=0,
        leader_only=False,
        config_rotation_rounds=3,
        transaction_hash="0x" + "cd" * 32,
    )
    session.execute(
        text("UPDATE transactions SET status = 'FINALIZED', value_credited = true")
    )
    accounting = create_fee_accounting(
        fees_distribution={
            "leaderTimeunitsAllocation": 100,
            "validatorTimeunitsAllocation": 200,
            "appealRounds": 0,
            "executionBudgetPerRound": 0,
            "executionConsumed": 0,
            "totalMessageFees": 0,
            "rotations": [0],
            "maxPriceGenPerTimeUnit": 0,
            "storageFeeMaxGasPrice": 0,
            "receiptFeeMaxGasPrice": 0,
        },
        num_of_validators=5,
        submitted_value=VALUE + FEES,
        user_value=VALUE,
        sender=FEE_SENDER,
    )
    if activated:
        accounting, canceled = activate_fee_accounting(accounting)
        assert not canceled
        assert accounting["activation_prices_locked"]
    processor.insert_transaction(
        from_address=SENDER,
        to_address=TARGET,
        data={FEE_ACCOUNTING_KEY: accounting},
        value=VALUE,
        type=2,
        nonce=0,
        leader_only=False,
        config_rotation_rounds=3,
        transaction_hash=TX_HASH,
        num_of_initial_validators=5,
    )
    assert manager.debit_account_balance(SENDER, VALUE)
    assert manager.debit_account_balance(FEE_SENDER, FEES)
    if activated:
        assert manager.credit_tx_value_once(TX_HASH, TARGET, VALUE)
    # This also models recovery: PENDING, cleared consensus fields, with the
    # activation credit and locked fee accounting still present.
    session.commit()
    return SimpleNamespace(
        transaction=SimpleNamespace(hash=TX_HASH),
        transactions_processor=processor,
        accounts_manager=manager,
        validators_snapshot=SimpleNamespace(nodes=[]),
        msg_handler=SimpleNamespace(send_message=Mock()),
    )


def _balances(session):
    return dict(session.execute(text("SELECT id, balance FROM current_state")).all())


def _assert_canceled(session):
    row = session.execute(
        text(
            "SELECT status, data, consensus_data FROM transactions WHERE hash = :hash"
        ),
        {"hash": TX_HASH},
    ).one()
    assert row.status == "CANCELED"
    assert row.consensus_data == {"error": "no_validators_available"}
    assert row.data[FEE_ACCOUNTING_KEY]["status"] == "canceled"
    assert _balances(session) == {SENDER: 1000, TARGET: 100, FEE_SENDER: 5000}
    conserved, details = verify_conservation(session)
    assert conserved, details


@pytest.mark.asyncio
@pytest.mark.parametrize("activated", [False, True])
async def test_cancel_conserves_value_and_fees(session, activated):
    context = _setup_pending(session, activated=activated)

    assert await PendingState().handle(context) is None

    # Read in another session to check committed balances and terminal status.
    with Session(session.get_bind()) as observer:
        _assert_canceled(observer)
    # Both refund paths and fee cancellation are idempotent after completion.
    assert not context.accounts_manager.refund_activated_tx_value_once(
        TX_HASH, TARGET, SENDER
    )
    assert not context.accounts_manager.refund_tx_value(TX_HASH, SENDER)
    assert (
        context.accounts_manager.cancel_tx_fee_accounting_once(TX_HASH, FEE_SENDER) == 0
    )
    session.commit()
    _assert_canceled(session)


@pytest.mark.asyncio
@pytest.mark.parametrize("activated", [False, True])
async def test_crash_before_status_commit_rolls_back_refunds(
    session, monkeypatch, activated
):
    context = _setup_pending(session, activated=activated)
    before = _balances(session)
    processor = context.transactions_processor
    update_status = processor.update_transaction_status

    def crash_before_status(*args, **kwargs):
        # Even after writing the error result, another connection must not see
        # committed refunds while the transaction remains eligible for recovery.
        with Session(session.get_bind()) as observer:
            assert _balances(observer) == before
            row = observer.execute(
                text(
                    "SELECT status, data, consensus_data FROM transactions WHERE hash = :hash"
                ),
                {"hash": TX_HASH},
            ).one()
            assert row.status == "PENDING"
            assert row.consensus_data is None
            assert row.data[FEE_ACCOUNTING_KEY]["status"] != "canceled"
        raise RuntimeError("simulated worker crash")

    monkeypatch.setattr(processor, "update_transaction_status", crash_before_status)
    with pytest.raises(RuntimeError, match="simulated worker crash"):
        await PendingState().handle(context)
    session.rollback()
    assert _balances(session) == before
    # The helper counts value in transit but not fee escrow. Before cancellation
    # the only difference must be the still-reserved fee deposit.
    _, details = verify_conservation(session)
    assert details["delta"] == -FEES

    monkeypatch.setattr(processor, "update_transaction_status", update_status)
    assert await PendingState().handle(context) is None
    _assert_canceled(session)
