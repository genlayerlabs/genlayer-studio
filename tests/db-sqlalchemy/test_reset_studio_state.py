"""sim_resetStudioState wipes transactions and accounts but keeps validators."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.database_handler.accounts_manager import AccountsManager
from backend.database_handler.models import (
    CurrentState,
    Transactions,
    TransactionSnapshotArchive,
    Validators,
)
from backend.database_handler.transactions_processor import TransactionsProcessor
from backend.protocol_rpc.endpoints import reset_studio_state


def _insert_tx(tp: TransactionsProcessor, triggered_by: str | None = None) -> str:
    tx_hash = f"0x{uuid.uuid4().hex}{uuid.uuid4().hex}"
    tp.insert_transaction(
        from_address="0x9F0e84243496AcFB3Cd99D02eA59673c05901501",
        to_address="0xAcec3A6d871C25F591aBd4fC24054e524BBbF794",
        data={"key": "value"},
        value=0,
        type=1,
        nonce=0,
        leader_only=False,
        config_rotation_rounds=3,
        triggered_by_hash=triggered_by,
        transaction_hash=tx_hash,
    )
    return tx_hash


@pytest.mark.asyncio
async def test_reset_studio_state_deletes_transactions_and_accounts(
    session: Session, monkeypatch
):
    monkeypatch.delenv("VITE_IS_HOSTED", raising=False)
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)

    tp = TransactionsProcessor(session)
    parent = _insert_tx(tp)
    session.commit()  # triggered_by_hash must reference a committed tx
    _insert_tx(tp, triggered_by=parent)  # self-referencing FK
    AccountsManager(session).create_new_account()
    session.add(
        Validators(
            stake=10,
            config={},
            address="0x" + "1" * 40,
            provider="openai",
            model="gpt-4o",
            plugin="openai-compatible",
            plugin_config={},
            private_key=None,
        )
    )
    session.execute(
        text(
            """
            INSERT INTO transaction_snapshot_archives
                (tx_hash, backend, object_key, uri, snapshot_sha256,
                 compressed_sha256, snapshot_bytes, compressed_bytes)
            VALUES (:h, 'file', 'k', 'file:///k', 'a', 'b', 1, 1)
            """
        ),
        {"h": parent},
    )
    session.commit()

    with patch(
        "backend.protocol_rpc.endpoints.publish_studio_reset_event",
        new=AsyncMock(return_value=2),
    ) as publish:
        result = await reset_studio_state(session=session)

    publish.assert_awaited_once()
    assert result == {
        "deleted_transactions": 2,
        "deleted_accounts": 1,
        "workers_notified": 2,
    }
    assert session.query(Transactions).count() == 0
    assert session.query(CurrentState).count() == 0
    assert session.query(TransactionSnapshotArchive).count() == 0
    # Validators survive so users don't have to re-create them.
    assert session.query(Validators).count() == 1
