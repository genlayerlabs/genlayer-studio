"""sim_resetStudioState wipes transactions and accounts but keeps validators."""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

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
from backend.protocol_rpc.exceptions import JSONRPCError


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


def _redis(acking_worker_id: str | None = None) -> MagicMock:
    """Redis stand-in: optionally one subscribed worker that acks at once."""
    redis = MagicMock()
    acks: list[str] = []
    kv: dict[str, str] = {}

    async def publish(_channel, message):
        event = json.loads(message)
        if acking_worker_id is None:
            return 0
        if "ack_key" in event["data"]:
            acks.append(
                json.dumps(
                    {
                        "worker_id": acking_worker_id,
                        "reset_id": event["data"]["reset_id"],
                        "ok": True,
                        "cancelled": 0,
                    }
                )
            )
        return 1

    async def blpop(keys, timeout):
        return (keys[0], acks.pop(0)) if acks else None

    async def set_(key, value, nx=False, px=None):
        if nx and key in kv:
            return None
        kv[key] = value
        return True

    async def eval_(_script, _numkeys, key, value):
        if kv.get(key) == value:
            del kv[key]

    redis.publish = publish
    redis.blpop = blpop
    redis.set = set_
    redis.eval = eval_
    redis.delete = AsyncMock()
    redis.close = AsyncMock()
    return redis


def _claim(session: Session, tx_hash: str, worker_id: str, age: str) -> None:
    session.execute(
        text(
            "UPDATE transactions SET worker_id = :w, "
            "blocked_at = NOW() - CAST(:age AS INTERVAL) WHERE hash = :h"
        ),
        {"w": worker_id, "age": age, "h": tx_hash},
    )
    session.commit()


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

    redis = _redis(acking_worker_id="worker-1")
    with patch(
        "backend.protocol_rpc.endpoints._studio_reset_redis", return_value=redis
    ):
        result = await reset_studio_state(session=session)

    assert result == {
        "deleted_transactions": 2,
        "deleted_accounts": 1,
        "workers_reset": 1,
        "tasks_cancelled": 0,
    }
    assert session.query(Transactions).count() == 0
    assert session.query(CurrentState).count() == 0
    assert session.query(TransactionSnapshotArchive).count() == 0
    # Validators survive so users don't have to re-create them.
    assert session.query(Validators).count() == 1


@pytest.mark.asyncio
async def test_live_claim_from_uncounted_worker_blocks_reset(
    session: Session, monkeypatch
):
    """A worker missing from the Redis handshake (zero subscribers here) but
    still processing a transaction must stop the reset: nothing is deleted."""
    monkeypatch.delenv("VITE_IS_HOSTED", raising=False)
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)

    tp = TransactionsProcessor(session)
    busy = _insert_tx(tp)
    session.commit()
    AccountsManager(session).create_new_account()
    session.commit()
    _claim(session, busy, "worker-disconnected", "1 minute")

    with patch(
        "backend.protocol_rpc.endpoints._studio_reset_redis", return_value=_redis()
    ):
        with pytest.raises(JSONRPCError, match="worker-disconnected"):
            await reset_studio_state(session=session)

    assert session.query(Transactions).count() == 1
    assert session.query(CurrentState).count() == 1


@pytest.mark.asyncio
async def test_claims_of_acked_workers_and_stale_claims_do_not_block(
    session: Session, monkeypatch
):
    monkeypatch.delenv("VITE_IS_HOSTED", raising=False)
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)

    tp = TransactionsProcessor(session)
    own = _insert_tx(tp)
    stale = _insert_tx(tp)
    session.commit()
    # A claim the acked worker failed to release, and one left behind by a
    # crashed worker long ago (already re-claimable by any worker).
    _claim(session, own, "worker-1", "1 minute")
    _claim(session, stale, "worker-crashed", "2 hours")

    with patch(
        "backend.protocol_rpc.endpoints._studio_reset_redis",
        return_value=_redis(acking_worker_id="worker-1"),
    ):
        result = await reset_studio_state(session=session)

    assert result["deleted_transactions"] == 2
    assert session.query(Transactions).count() == 0
