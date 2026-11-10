"""Terminal refunds must not return value a top-level SEND never debited.

Top-level SENDs are debited only inside ``execute_transfer`` (atomically with
the recipient credit), never at submission. Cancelling one, or finalizing one
that went UNDETERMINED on insufficient balance, used to call
``refund_tx_value`` and credit the sender the full value: a mint.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.consensus.base import (
    ConsensusAlgorithm,
    FinalizingState,
    TransactionContext,
)
from backend.database_handler.accounts_manager import AccountsManager
from backend.database_handler.contract_processor import ContractProcessor
from backend.database_handler.transactions_processor import TransactionsProcessor
from backend.domain.types import Transaction, TransactionType
from backend.protocol_rpc.endpoints import cancel_transaction

SENDER = "0x" + "ab" * 20
RECIPIENT = "0x" + "cd" * 20
CONTRACT = "0x" + "c0" * 20
VALUE = 1000


def _insert(
    tp: TransactionsProcessor,
    *,
    tx_hash: str,
    from_address: str,
    to_address: str,
    tx_type: TransactionType,
    triggered_by_hash: str | None = None,
) -> str:
    tp.insert_transaction(
        from_address=from_address,
        to_address=to_address,
        data={},
        value=VALUE,
        type=tx_type.value,
        nonce=0,
        leader_only=True,
        config_rotation_rounds=0,
        triggered_by_hash=triggered_by_hash,
        transaction_hash=tx_hash,
    )
    tp.session.commit()
    return tx_hash


def _row(session: Session, tx_hash: str):
    return session.execute(
        text("SELECT status, value_credited FROM transactions WHERE hash = :h"),
        {"h": tx_hash},
    ).one()


def _context(session: Session, tx: Transaction) -> TransactionContext:
    msg_handler = Mock()
    msg_handler.send_message_async = AsyncMock()
    return TransactionContext(
        transaction=tx,
        transactions_processor=TransactionsProcessor(session),
        chain_snapshot=None,
        accounts_manager=AccountsManager(session),
        contract_snapshot_factory=lambda _addr: None,
        contract_processor=ContractProcessor(session),
        node_factory=None,
        msg_handler=msg_handler,
        consensus_service=Mock(),
        validators_snapshot=None,
        genvm_manager=None,
    )


def test_cancel_pending_send_does_not_mint(session: Session, monkeypatch):
    monkeypatch.delenv("VITE_IS_HOSTED", raising=False)
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    tp = TransactionsProcessor(session)
    am = AccountsManager(session)
    am.update_account_balance(SENDER, 0)
    session.commit()
    tx_hash = _insert(
        tp,
        tx_hash="0x" + "a1" * 32,
        from_address=SENDER,
        to_address=RECIPIENT,
        tx_type=TransactionType.SEND,
    )

    result = cancel_transaction(session, tx_hash, Mock())

    assert result["status"] == "CANCELED"
    session.expire_all()
    assert am.get_account_balance(SENDER) == 0
    assert am.get_account_balance(RECIPIENT) == 0


@pytest.mark.asyncio
async def test_undetermined_send_finalization_does_not_mint(session: Session):
    tp = TransactionsProcessor(session)
    am = AccountsManager(session)
    am.update_account_balance(SENDER, 0)
    session.commit()
    tx_hash = _insert(
        tp,
        tx_hash="0x" + "a2" * 32,
        from_address=SENDER,
        to_address=RECIPIENT,
        tx_type=TransactionType.SEND,
    )

    await ConsensusAlgorithm.execute_transfer(
        Transaction.from_dict(tp.get_transaction_by_hash(tx_hash)),
        tp,
        am,
        SimpleNamespace(send_message_async=AsyncMock()),
    )
    session.commit()
    assert _row(session, tx_hash).status == "UNDETERMINED"

    tx = Transaction.from_dict(tp.get_transaction_by_hash(tx_hash))
    await FinalizingState().handle(_context(session, tx))
    session.commit()

    session.expire_all()
    assert _row(session, tx_hash).status == "FINALIZED"
    assert am.get_account_balance(SENDER) == 0
    assert am.get_account_balance(RECIPIENT) == 0


def test_refund_still_returns_value_for_contract_emitted_send(session: Session):
    """A child SEND's value was debited from the emitting contract at
    acceptance, so cancelling it before transfer must give it back."""
    tp = TransactionsProcessor(session)
    am = AccountsManager(session)
    am.update_account_balance(CONTRACT, 0)
    session.commit()
    parent_hash = _insert(
        tp,
        tx_hash="0x" + "a3" * 32,
        from_address=SENDER,
        to_address=CONTRACT,
        tx_type=TransactionType.RUN_CONTRACT,
    )
    child_hash = _insert(
        tp,
        tx_hash="0x" + "a4" * 32,
        from_address=CONTRACT,
        to_address=RECIPIENT,
        tx_type=TransactionType.SEND,
        triggered_by_hash=parent_hash,
    )

    assert am.refund_tx_value(child_hash, CONTRACT) is True
    session.commit()

    session.expire_all()
    assert am.get_account_balance(CONTRACT) == VALUE
    assert am.refund_tx_value(child_hash, CONTRACT) is False


def test_refund_still_returns_value_for_contract_call(session: Session):
    tp = TransactionsProcessor(session)
    am = AccountsManager(session)
    am.update_account_balance(SENDER, 0)
    session.commit()
    tx_hash = _insert(
        tp,
        tx_hash="0x" + "a5" * 32,
        from_address=SENDER,
        to_address=CONTRACT,
        tx_type=TransactionType.RUN_CONTRACT,
    )

    assert am.refund_tx_value(tx_hash, SENDER) is True
    session.commit()

    session.expire_all()
    assert am.get_account_balance(SENDER) == VALUE
