"""
Regression tests for the PENDING queue-depth admission control on
eth_sendRawTransaction.

Without this cap, a single user can pile up thousands of PENDING txs on
one contract (observed in Studio Prod: one oracle backend backlogged
~2000 verifications on a single contract for 5 days, starving other
contracts behind it). The cap rejects new submissions at admission time
with a structured QueueDepthExceeded error.

These tests exercise `_enforce_pending_queue_caps` directly against a
real Postgres session so the COUNT(*) query and the raise paths are
both covered.
"""

import importlib
import threading
from concurrent.futures import ThreadPoolExecutor
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


def _insert_pending(session, *, tx_hash: str, nonce: int) -> None:
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
            "from_addr": SENDER,
            "to_addr": CONTRACT,
            "nonce": nonce,
        },
    )


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


def test_unset_recipient_cap_uses_consensus_default_20(engine: Engine, monkeypatch):
    """Studio defaults to Queues.maxPendingTxsPerRecipient."""
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    with Session_() as session:
        _seed_pending(session, 100, to_address=CONTRACT, from_address=SENDER)
        endpoints = _reload_endpoints_module(
            monkeypatch,
            MAX_PENDING_PER_CONTRACT_DEFAULT=None,
            MAX_PENDING_PER_SENDER_DEFAULT=None,
        )
        tp = TransactionsProcessor(session)
        with pytest.raises(QueueDepthExceeded) as exc_info:
            endpoints._enforce_pending_queue_caps(
                transactions_processor=tp,
                to_address=CONTRACT,
                from_address=SENDER,
            )
        assert exc_info.value.data["limit"] == 20


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
        assert "protocol limits" in exc_info.value.message.lower()


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


def test_active_head_still_occupies_consensus_pending_queue_slot(
    engine: Engine, monkeypatch
):
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    with Session_() as session:
        _seed_pending(session, 20, to_address=CONTRACT, from_address=SENDER)
        session.execute(
            text(
                "UPDATE transactions SET status = CAST('COMMITTING' AS transaction_status) "
                "WHERE hash = :hash"
            ),
            {"hash": f"0x{0:064x}"},
        )
        session.commit()
        endpoints = _reload_endpoints_module(
            monkeypatch,
            MAX_PENDING_PER_CONTRACT_DEFAULT="20",
            MAX_PENDING_PER_SENDER_DEFAULT=None,
        )

        with pytest.raises(QueueDepthExceeded) as exc_info:
            endpoints._enforce_pending_queue_caps(
                transactions_processor=TransactionsProcessor(session),
                to_address=CONTRACT,
                from_address="0x" + "ee" * 20,
            )

        assert exc_info.value.data["pending"] == 20


def test_concurrent_admissions_cannot_both_claim_last_recipient_slot(
    engine: Engine, monkeypatch
):
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    with Session_() as session:
        _seed_pending(session, 19, to_address=CONTRACT, from_address=SENDER)
    endpoints = _reload_endpoints_module(
        monkeypatch,
        MAX_PENDING_PER_CONTRACT_DEFAULT="20",
        MAX_PENDING_PER_SENDER_DEFAULT=None,
    )
    barrier = threading.Barrier(2)

    def admit(index: int) -> str:
        with Session_() as session:
            tp = TransactionsProcessor(session)
            barrier.wait()
            try:
                endpoints._enforce_pending_queue_caps(
                    transactions_processor=tp,
                    to_address=CONTRACT,
                    from_address="0x" + f"{index + 1:02x}" * 20,
                )
            except QueueDepthExceeded:
                session.rollback()
                return "rejected"
            _insert_pending(
                session,
                tx_hash=f"0x{100 + index:064x}",
                nonce=100 + index,
            )
            session.commit()
            return "accepted"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(admit, range(2)))

    assert sorted(outcomes) == ["accepted", "rejected"]
    with Session_() as session:
        count = session.execute(
            text(
                "SELECT COUNT(*) FROM transactions "
                "WHERE to_address = :addr AND status = 'PENDING'"
            ),
            {"addr": CONTRACT},
        ).scalar_one()
    assert count == 20


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


def test_invalid_recipient_cap_env_falls_back_to_consensus_default(
    engine: Engine, monkeypatch
):
    """Garbage values do not silently disable the protocol recipient cap."""
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    with Session_() as session:
        _seed_pending(session, 100, to_address=CONTRACT, from_address=SENDER)
        endpoints = _reload_endpoints_module(
            monkeypatch,
            MAX_PENDING_PER_CONTRACT_DEFAULT="not-a-number",
            MAX_PENDING_PER_SENDER_DEFAULT="-5",  # negative → ignored
        )
        tp = TransactionsProcessor(session)
        with pytest.raises(QueueDepthExceeded) as exc_info:
            endpoints._enforce_pending_queue_caps(
                transactions_processor=tp,
                to_address=CONTRACT,
                from_address=SENDER,
            )
        assert exc_info.value.data["limit"] == 20


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
