"""Regression: concurrent native SENDs from the same sender mint tokens.

execute_transfer debited the sender with a read-modify-write
(get_account_balance -> update_account_balance overwrite). Worker claims
serialize only on to_address (pg_try_advisory_xact_lock(to_address)), so two
SENDs from one sender to different recipients (A->B and A->C) are claimed by
two workers concurrently. Both read the sender balance before either writes,
and the second commit overwrites the first's debit: the sender is debited once
while both recipients are credited -- token supply inflated.

The fix makes execute_transfer use the atomic debit_account_balance
(UPDATE ... WHERE balance >= amount) and credit_account_balance, so concurrent
sends serialize on the sender row and conserve the balance.
"""

import asyncio
import threading

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import sessionmaker
from unittest.mock import AsyncMock

from backend.consensus.base import ConsensusAlgorithm
from backend.database_handler.accounts_manager import AccountsManager
from backend.database_handler.transactions_processor import (
    TransactionsProcessor,
    TransactionStatus,
)
from backend.domain.types import Transaction, TransactionType, TransactionExecutionMode


SENDER = "0x" + "ab" * 20
RECIPIENT_B = "0x" + "bb" * 20
RECIPIENT_C = "0x" + "cc" * 20
INITIAL = 100
VALUE_B = 30
VALUE_C = 50


def _insert_send_tx(tp: TransactionsProcessor, *, to_address, value, tx_hash):
    tp.insert_transaction(
        from_address=SENDER,
        to_address=to_address,
        data={},
        value=value,
        type=TransactionType.SEND.value,
        nonce=0,
        leader_only=True,
        config_rotation_rounds=0,
        triggered_by_hash=None,
        transaction_hash=tx_hash,
    )
    tp.session.commit()


def _run_transfer(engine, *, tx_hash, to_address, value, barrier):
    Session_ = sessionmaker(bind=engine)
    with Session_() as session:
        tp = TransactionsProcessor(session)
        am = AccountsManager(session)

        # Force the read-modify-write race: both workers read the sender
        # balance before either writes. Only the buggy path calls
        # get_account_balance for the sender; the atomic-debit fix does not, so
        # the barrier simply times out harmlessly there.
        original_get_balance = am.get_account_balance
        state = {"synced": False}

        def barriered_get_balance(addr):
            balance = original_get_balance(addr)
            if addr == SENDER and not state["synced"]:
                state["synced"] = True
                try:
                    barrier.wait(timeout=5)
                except threading.BrokenBarrierError:
                    pass
            return balance

        am.get_account_balance = barriered_get_balance

        tx = Transaction(
            hash=tx_hash,
            status=TransactionStatus.PENDING,
            type=TransactionType.SEND,
            from_address=SENDER,
            to_address=to_address,
            value=value,
            nonce=0,
            leader_only=True,
            execution_mode=TransactionExecutionMode.NORMAL,
        )

        asyncio.run(
            ConsensusAlgorithm.execute_transfer(
                transaction=tx,
                transactions_processor=tp,
                accounts_manager=am,
                msg_handler=AsyncMock(),
            )
        )
        session.commit()


def test_concurrent_sends_from_same_sender_conserve_balance(engine: Engine):
    Session_ = sessionmaker(bind=engine)
    with Session_() as s:
        am = AccountsManager(s)
        am.credit_account_balance(SENDER, INITIAL)
        tp = TransactionsProcessor(s)
        _insert_send_tx(tp, to_address=RECIPIENT_B, value=VALUE_B, tx_hash="0x" + "e1" * 32)
        _insert_send_tx(tp, to_address=RECIPIENT_C, value=VALUE_C, tx_hash="0x" + "e2" * 32)
        s.commit()

    barrier = threading.Barrier(2)
    errors = []

    def worker(tx_hash, to_address, value):
        try:
            _run_transfer(
                engine, tx_hash=tx_hash, to_address=to_address, value=value, barrier=barrier
            )
        except Exception as e:  # pragma: no cover - surfaced via assert below
            errors.append(e)

    t1 = threading.Thread(target=worker, args=("0x" + "e1" * 32, RECIPIENT_B, VALUE_B))
    t2 = threading.Thread(target=worker, args=("0x" + "e2" * 32, RECIPIENT_C, VALUE_C))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert not errors, f"worker errors: {errors}"

    with Session_() as s:
        am = AccountsManager(s)
        sender_balance = am.get_account_balance(SENDER)
        b_balance = am.get_account_balance(RECIPIENT_B)
        c_balance = am.get_account_balance(RECIPIENT_C)

    # Conservation: the sender must be debited for BOTH sends.
    assert sender_balance == INITIAL - VALUE_B - VALUE_C, (
        f"lost update minted tokens: sender balance {sender_balance}, "
        f"expected {INITIAL - VALUE_B - VALUE_C}"
    )
    assert b_balance == VALUE_B
    assert c_balance == VALUE_C
