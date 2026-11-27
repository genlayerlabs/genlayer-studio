"""execute_transfer and sim_fundAccount's credit_tx_value_once must credit a SEND once."""

import asyncio
import threading
import time
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import sessionmaker

from backend.consensus.base import ConsensusAlgorithm
from backend.database_handler.accounts_manager import AccountsManager
from backend.database_handler.transactions_processor import (
    TransactionsProcessor,
    TransactionStatus,
)
from backend.domain.types import Transaction, TransactionExecutionMode, TransactionType

RECIPIENT = "0x" + "cd" * 20
SENDER = "0x" + "ab" * 20
AMOUNT = 1000


def _insert_faucet_send(SessionFactory, tx_hash: str) -> None:
    # Mirrors the pre-#1815 endpoint: the PENDING row is committed uncredited.
    with SessionFactory() as s:
        TransactionsProcessor(s).insert_transaction(
            None,
            RECIPIENT,
            None,
            AMOUNT,
            TransactionType.SEND.value,
            0,
            False,
            0,
            None,
            tx_hash,
        )
        s.commit()


def _send(tx_hash: str, from_address: str | None = None) -> Transaction:
    return Transaction(
        hash=tx_hash,
        status=TransactionStatus.PENDING,
        type=TransactionType.SEND,
        from_address=from_address,
        to_address=RECIPIENT,
        value=AMOUNT,
        nonce=0,
        leader_only=False,
        execution_mode=TransactionExecutionMode.NORMAL,
    )


def _run_execute_transfer(
    session, tx: Transaction, accounts_manager: AccountsManager | None = None
) -> None:
    asyncio.run(
        ConsensusAlgorithm.execute_transfer(
            tx,
            TransactionsProcessor(session),
            accounts_manager or AccountsManager(session),
            AsyncMock(),
        )
    )


def _state(SessionFactory, tx_hash: str):
    with SessionFactory() as s:
        balance = AccountsManager(s).get_account_balance(RECIPIENT)
        row = s.execute(
            text("SELECT status, value_credited FROM transactions WHERE hash = :h"),
            {"h": tx_hash},
        ).one()
    return balance, row


def test_endpoint_credit_during_worker_transfer_is_noop(engine: Engine):
    SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)
    tx_hash = "0x" + "f1" * 32
    _insert_faucet_send(SessionFactory, tx_hash)

    endpoint_result: list[bool] = []
    endpoint_blocked: list[bool] = []
    errors: list[BaseException] = []

    def endpoint_credit():
        try:
            with SessionFactory() as s:
                endpoint_result.append(
                    AccountsManager(s).credit_tx_value_once(tx_hash, RECIPIENT, AMOUNT)
                )
                s.commit()
        except BaseException as exc:
            errors.append(exc)

    endpoint = threading.Thread(target=endpoint_credit)

    with SessionFactory() as worker_session:
        manager = AccountsManager(worker_session)
        real_credit = manager.credit_account_balance

        def credit_after_endpoint_races(address, amount):
            endpoint.start()
            # Let the endpoint reach the tx row before the worker credits and commits.
            time.sleep(0.5)
            endpoint_blocked.append(endpoint.is_alive())
            real_credit(address, amount)

        manager.credit_account_balance = credit_after_endpoint_races
        _run_execute_transfer(worker_session, _send(tx_hash), manager)
        worker_session.commit()
    endpoint.join(timeout=10)

    assert not endpoint.is_alive()
    assert errors == []
    assert endpoint_blocked == [True]
    assert endpoint_result == [False]
    balance, row = _state(SessionFactory, tx_hash)
    assert balance == AMOUNT
    assert row.status == TransactionStatus.FINALIZED.value
    assert row.value_credited is True


def test_worker_after_uncommitted_endpoint_credit_is_noop(engine: Engine):
    SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)
    tx_hash = "0x" + "f2" * 32
    _insert_faucet_send(SessionFactory, tx_hash)

    errors: list[BaseException] = []
    worker_started = threading.Event()

    def worker():
        try:
            with SessionFactory() as s:
                worker_started.set()
                _run_execute_transfer(s, _send(tx_hash))
                s.commit()
        except BaseException as exc:
            errors.append(exc)

    with SessionFactory() as endpoint_session:
        assert AccountsManager(endpoint_session).credit_tx_value_once(
            tx_hash, RECIPIENT, AMOUNT
        )
        worker_thread = threading.Thread(target=worker)
        worker_thread.start()
        worker_started.wait(timeout=5)
        time.sleep(0.5)
        assert worker_thread.is_alive()
        endpoint_session.commit()
    worker_thread.join(timeout=10)

    assert not worker_thread.is_alive()
    assert errors == []
    balance, row = _state(SessionFactory, tx_hash)
    assert balance == AMOUNT
    assert row.status == TransactionStatus.FINALIZED.value
    assert row.value_credited is True


def test_concurrent_endpoint_and_worker_credit_once(engine: Engine):
    SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)
    tx_hash = "0x" + "f3" * 32
    _insert_faucet_send(SessionFactory, tx_hash)

    barrier = threading.Barrier(2, timeout=5)
    errors: list[BaseException] = []

    def endpoint_credit():
        try:
            with SessionFactory() as s:
                barrier.wait()
                AccountsManager(s).credit_tx_value_once(tx_hash, RECIPIENT, AMOUNT)
                s.commit()
        except BaseException as exc:
            errors.append(exc)

    def worker():
        try:
            with SessionFactory() as s:
                barrier.wait()
                _run_execute_transfer(s, _send(tx_hash))
                s.commit()
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=endpoint_credit),
        threading.Thread(target=worker),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not any(t.is_alive() for t in threads)
    assert errors == []
    balance, row = _state(SessionFactory, tx_hash)
    assert balance == AMOUNT
    assert row.value_credited is True


def test_insufficient_balance_releases_claim_for_refund(engine: Engine):
    SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)
    tx_hash = "0x" + "f4" * 32
    with SessionFactory() as s:
        AccountsManager(s).update_account_balance(SENDER, 0)
        TransactionsProcessor(s).insert_transaction(
            SENDER,
            RECIPIENT,
            {},
            AMOUNT,
            TransactionType.SEND.value,
            0,
            False,
            0,
            None,
            tx_hash,
        )
        s.commit()

    with SessionFactory() as s:
        _run_execute_transfer(s, _send(tx_hash, from_address=SENDER))
        s.commit()

    balance, row = _state(SessionFactory, tx_hash)
    assert balance == 0
    assert row.status == TransactionStatus.UNDETERMINED.value
    assert row.value_credited is False


@pytest.mark.asyncio
async def test_retried_transfer_moves_value_once(session):
    tp = TransactionsProcessor(session)
    am = AccountsManager(session)
    tx_hash = "0x" + "f5" * 32
    am.update_account_balance(SENDER, 5 * AMOUNT)
    tp.insert_transaction(
        SENDER,
        RECIPIENT,
        {},
        AMOUNT,
        TransactionType.SEND.value,
        0,
        False,
        0,
        None,
        tx_hash,
    )
    session.commit()

    for _ in range(2):
        await ConsensusAlgorithm.execute_transfer(
            _send(tx_hash, from_address=SENDER), tp, am, AsyncMock()
        )
        session.commit()

    assert am.get_account_balance(SENDER) == 4 * AMOUNT
    assert am.get_account_balance(RECIPIENT) == AMOUNT
