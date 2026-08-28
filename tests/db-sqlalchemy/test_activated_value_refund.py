"""PostgreSQL coverage for activated payable-value terminal refunds."""

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.database_handler.accounts_manager import AccountsManager


def _insert_activated_value(
    session: Session,
    *,
    tx_hash: str,
    sender: str,
    target: str,
    value: int,
    target_balance: int,
) -> None:
    session.execute(
        text(
            """
            INSERT INTO current_state (id, data, balance)
            VALUES
                (:sender, CAST('{}' AS jsonb), 100),
                (:target, CAST('{}' AS jsonb), :target_balance)
            """
        ),
        {
            "sender": sender,
            "target": target,
            "target_balance": target_balance,
        },
    )
    session.execute(
        text(
            """
            INSERT INTO transactions (
                hash, status, from_address, to_address, data, value, type,
                nonce, leader_only, execution_mode, appealed, appeal_failed,
                appeal_undetermined, appeal_leader_timeout,
                appeal_validators_timeout, appeal_processing_time,
                value_credited, consensus_history, num_of_initial_validators
            ) VALUES (
                :hash, 'LEADER_TIMEOUT', :sender, :target, CAST('{}' AS jsonb),
                :value, 2, 0, false, 'NORMAL', false, 0, false, false, false,
                0, true, CAST('{}' AS jsonb), 5
            )
            """
        ),
        {
            "hash": tx_hash,
            "sender": sender,
            "target": target,
            "value": value,
        },
    )
    session.commit()


def test_activated_value_refund_is_exact_and_idempotent(session: Session):
    tx_hash = "0x" + "ad" * 32
    emitting_contract = "0x1111111111111111111111111111111111111111"
    child = "0x2222222222222222222222222222222222222222"
    _insert_activated_value(
        session,
        tx_hash=tx_hash,
        sender=emitting_contract,
        target=child,
        value=500,
        target_balance=700,
    )

    manager = AccountsManager(session)
    assert manager.refund_activated_tx_value_once(
        tx_hash,
        child,
        emitting_contract,
    )
    session.commit()
    assert not manager.refund_activated_tx_value_once(
        tx_hash,
        child,
        emitting_contract,
    )

    balances = dict(
        session.execute(
            text(
                "SELECT id, balance FROM current_state WHERE id IN (:sender, :target)"
            ),
            {"sender": emitting_contract, "target": child},
        ).all()
    )
    marker = session.execute(
        text(
            "SELECT data ->> 'activatedValueRefunded' FROM transactions WHERE hash = :hash"
        ),
        {"hash": tx_hash},
    ).scalar_one()
    assert balances == {emitting_contract: 600, child: 200}
    assert marker == "true"


def test_activated_value_refund_fails_atomically_if_target_is_short(session: Session):
    tx_hash = "0x" + "ae" * 32
    emitting_contract = "0x3333333333333333333333333333333333333333"
    child = "0x4444444444444444444444444444444444444444"
    _insert_activated_value(
        session,
        tx_hash=tx_hash,
        sender=emitting_contract,
        target=child,
        value=500,
        target_balance=499,
    )

    with pytest.raises(RuntimeError, match="ActivatedValueRefundInsufficientBalance"):
        AccountsManager(session).refund_activated_tx_value_once(
            tx_hash,
            child,
            emitting_contract,
        )
    session.rollback()

    balances = dict(
        session.execute(
            text(
                "SELECT id, balance FROM current_state WHERE id IN (:sender, :target)"
            ),
            {"sender": emitting_contract, "target": child},
        ).all()
    )
    marker = session.execute(
        text(
            "SELECT data ->> 'activatedValueRefunded' FROM transactions WHERE hash = :hash"
        ),
        {"hash": tx_hash},
    ).scalar_one()
    assert balances == {emitting_contract: 100, child: 499}
    assert marker is None


def test_uncredited_value_refund_is_exact_and_idempotent(session: Session):
    tx_hash = "0x" + "af" * 32
    sender = "0x5555555555555555555555555555555555555555"
    target = "0x6666666666666666666666666666666666666666"
    _insert_activated_value(
        session,
        tx_hash=tx_hash,
        sender=sender,
        target=target,
        value=500,
        target_balance=0,
    )
    session.execute(
        text("UPDATE transactions SET value_credited = false WHERE hash = :hash"),
        {"hash": tx_hash},
    )
    session.commit()

    manager = AccountsManager(session)
    assert manager.refund_tx_value(tx_hash, sender)
    session.commit()
    assert not manager.refund_tx_value(tx_hash, sender)

    balance = session.execute(
        text("SELECT balance FROM current_state WHERE id = :sender"),
        {"sender": sender},
    ).scalar_one()
    marker = session.execute(
        text(
            "SELECT data ->> 'uncreditedValueRefunded' FROM transactions "
            "WHERE hash = :hash"
        ),
        {"hash": tx_hash},
    ).scalar_one()
    assert balance == 600
    assert marker == "true"
