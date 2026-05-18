"""
Regression tests for the PENDING queue-depth admission control on
eth_sendRawTransaction.

Without this cap, a single user can pile up thousands of PENDING txs on
one contract (observed in Studio Prod: one oracle backend backlogged
~2000 verifications on a single contract for 5 days, starving other
contracts behind it). The cap rejects new submissions at admission time
with a structured QueueDepthExceeded error, pointing heavy users toward
non-shared deployments.

These tests exercise `_enforce_pending_queue_caps` directly against a
real Postgres session so the COUNT(*) query and the raise paths are
both covered.
"""

import importlib
import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import sessionmaker

from backend.database_handler.transactions_processor import TransactionsProcessor
from backend.protocol_rpc.exceptions import QueueDepthExceeded


CONTRACT = "0x" + "ab" * 20
SENDER = "0x" + "cd" * 20


def _seed_pending(session, n: int, *, to_address: str, from_address: str) -> None:
    """Insert n PENDING txs; explicit unique hashes keep them distinct."""
    for i in range(n):
        tx_hash = f"0x{i:064x}"
        session.execute(
            text(
                """
                INSERT INTO transactions (
                    hash, status, from_address, to_address, data, value, type,
                    nonce, leader_only, execution_mode, appealed, appeal_failed,
                    appeal_undetermined, appeal_leader_timeout,
                    appeal_validators_timeout, appeal_processing_time,
                    recovery_count, value_credited
                ) VALUES (
                    :hash, CAST('PENDING' AS transaction_status),
                    :from_addr, :to_addr, CAST('{}' AS jsonb), 0, 2,
                    :nonce, false, 'NORMAL', false, 0,
                    false, false, false, 0, 0, false
                )
                """
            ),
            {
                "hash": tx_hash,
                "from_addr": from_address,
                "to_addr": to_address,
                "nonce": i,
            },
        )
    session.commit()


def _reload_endpoints_module(monkeypatch, **env_overrides):
    """Re-import endpoints with patched env so module-level cap vars are
    re-read. The caps are parsed at import time, so each test that wants
    a different cap setting needs a fresh module load."""
    for k, v in env_overrides.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    import backend.protocol_rpc.endpoints as endpoints

    return importlib.reload(endpoints)


def test_no_caps_set_allows_unlimited(engine: Engine, monkeypatch):
    """Self-hosted default: both caps unset → never raises."""
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    with Session_() as session:
        _seed_pending(session, 100, to_address=CONTRACT, from_address=SENDER)
        endpoints = _reload_endpoints_module(
            monkeypatch,
            MAX_PENDING_PER_CONTRACT_DEFAULT=None,
            MAX_PENDING_PER_SENDER_DEFAULT=None,
        )
        tp = TransactionsProcessor(session)
        # Should not raise even with 100 PENDING already.
        endpoints._enforce_pending_queue_caps(
            transactions_processor=tp,
            to_address=CONTRACT,
            from_address=SENDER,
        )


def test_per_contract_cap_rejects_when_at_limit(engine: Engine, monkeypatch):
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    with Session_() as session:
        _seed_pending(session, 50, to_address=CONTRACT, from_address=SENDER)
        endpoints = _reload_endpoints_module(
            monkeypatch,
            MAX_PENDING_PER_CONTRACT_DEFAULT="50",
            MAX_PENDING_PER_SENDER_DEFAULT=None,
        )
        tp = TransactionsProcessor(session)
        with pytest.raises(QueueDepthExceeded) as exc_info:
            endpoints._enforce_pending_queue_caps(
                transactions_processor=tp,
                to_address=CONTRACT,
                from_address="0x" + "ee" * 20,  # different sender, contract is full
            )
        assert exc_info.value.data["scope"] == "contract"
        assert exc_info.value.data["limit"] == 50
        assert exc_info.value.data["pending"] == 50
        # Error message should point users at alternatives.
        assert "self-hosted" in exc_info.value.message.lower()


def test_per_contract_cap_allows_under_limit(engine: Engine, monkeypatch):
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    with Session_() as session:
        _seed_pending(session, 49, to_address=CONTRACT, from_address=SENDER)
        endpoints = _reload_endpoints_module(
            monkeypatch,
            MAX_PENDING_PER_CONTRACT_DEFAULT="50",
            MAX_PENDING_PER_SENDER_DEFAULT=None,
        )
        tp = TransactionsProcessor(session)
        # 49 < 50, must not raise.
        endpoints._enforce_pending_queue_caps(
            transactions_processor=tp,
            to_address=CONTRACT,
            from_address="0x" + "ee" * 20,
        )


def test_per_sender_cap_rejects_when_at_limit(engine: Engine, monkeypatch):
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    with Session_() as session:
        _seed_pending(session, 20, to_address=CONTRACT, from_address=SENDER)
        endpoints = _reload_endpoints_module(
            monkeypatch,
            MAX_PENDING_PER_CONTRACT_DEFAULT=None,
            MAX_PENDING_PER_SENDER_DEFAULT="20",
        )
        tp = TransactionsProcessor(session)
        with pytest.raises(QueueDepthExceeded) as exc_info:
            # Submitting to a DIFFERENT contract — per-sender cap still trips.
            endpoints._enforce_pending_queue_caps(
                transactions_processor=tp,
                to_address="0x" + "ff" * 20,
                from_address=SENDER,
            )
        assert exc_info.value.data["scope"] == "sender"
        assert exc_info.value.data["limit"] == 20
        assert exc_info.value.data["pending"] == 20


def test_invalid_cap_env_value_falls_back_to_unlimited(engine: Engine, monkeypatch):
    """Garbage values in env shouldn't break submissions — fall back to no cap."""
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    with Session_() as session:
        _seed_pending(session, 100, to_address=CONTRACT, from_address=SENDER)
        endpoints = _reload_endpoints_module(
            monkeypatch,
            MAX_PENDING_PER_CONTRACT_DEFAULT="not-a-number",
            MAX_PENDING_PER_SENDER_DEFAULT="-5",  # negative → ignored
        )
        tp = TransactionsProcessor(session)
        endpoints._enforce_pending_queue_caps(
            transactions_processor=tp,
            to_address=CONTRACT,
            from_address=SENDER,
        )


def test_null_to_address_is_skipped(engine: Engine, monkeypatch):
    """Some tx types (faucet, burn) have NULL to_address. The cap should
    silently skip the contract check for them — there's no contract queue
    to overflow."""
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    with Session_() as session:
        endpoints = _reload_endpoints_module(
            monkeypatch,
            MAX_PENDING_PER_CONTRACT_DEFAULT="1",
            MAX_PENDING_PER_SENDER_DEFAULT=None,
        )
        tp = TransactionsProcessor(session)
        # to_address=None → contract check is skipped.
        endpoints._enforce_pending_queue_caps(
            transactions_processor=tp,
            to_address=None,
            from_address=SENDER,
        )
