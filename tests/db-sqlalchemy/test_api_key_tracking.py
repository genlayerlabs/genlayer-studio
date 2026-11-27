"""API key attribution of transactions and the admin usage queries."""

import asyncio
import datetime
import hashlib
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from web3 import Web3
from web3.providers import BaseProvider

from backend.database_handler.models import (
    ApiKey,
    ApiTier,
    Transactions,
    TransactionStatus,
)
from backend.database_handler.transactions_processor import TransactionsProcessor
from backend.protocol_rpc import api_key_usage
from backend.protocol_rpc import endpoints
from backend.protocol_rpc.rate_limiter import RateLimiterService
from backend.protocol_rpc.exceptions import InvalidParams, JSONRPCError, NotFoundError

EOA = "0x1111111111111111111111111111111111111111"
CONTRACT_A = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
CONTRACT_B = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"

_tx_counter = 700_000  # offset to avoid collisions with other test files


def _make_tx(tp, **overrides):
    global _tx_counter
    _tx_counter += 1
    defaults = dict(
        from_address=EOA,
        to_address=CONTRACT_A,
        data={"key": "value"},
        value=0,
        type=2,
        nonce=0,
        leader_only=False,
        config_rotation_rounds=3,
        transaction_hash=f"0x{_tx_counter:064x}",
    )
    defaults.update(overrides)
    tx_hash = tp.insert_transaction(**defaults)
    tp.session.commit()
    return tx_hash


def _api_key_id_of(tp, tx_hash):
    return tp.session.query(Transactions.api_key_id).filter_by(hash=tx_hash).scalar()


@pytest.fixture
def tp(transactions_processor: TransactionsProcessor):
    with patch.dict(
        os.environ,
        {
            "HARDHAT_PORT": "8545",
            "HARDHAT_URL": "http://localhost",
            "HARDHAT_PRIVATE_KEY": "0x0123456789",
        },
    ), patch("web3.Web3.HTTPProvider"):
        web3_instance = Web3(MagicMock(spec=BaseProvider))
        web3_instance.eth = MagicMock()
        web3_instance.is_connected = MagicMock(return_value=True)
        transactions_processor.web3 = web3_instance
        yield transactions_processor


@pytest.fixture
def api_keys(session):
    # Not "free": the migrations seed that tier on a fresh schema.
    tier = ApiTier(
        name="tracking", rate_limit_minute=30, rate_limit_hour=500, rate_limit_day=5000
    )
    session.add(tier)
    session.commit()
    keys = []
    for prefix in ("glk_aaaa", "glk_bbbb"):
        key = ApiKey(
            key_prefix=prefix,
            key_hash=hashlib.sha256(prefix.encode()).hexdigest(),
            tier_id=tier.id,
            description=f"{prefix} test key",
        )
        session.add(key)
        keys.append(key)
    session.commit()
    return keys


class TestAttribution:
    def test_top_level_transaction_stores_the_key(self, tp, api_keys):
        tx_hash = _make_tx(tp, api_key_id=api_keys[0].id)
        assert _api_key_id_of(tp, tx_hash) == api_keys[0].id

    def test_transaction_without_key_is_unattributed(self, tp):
        tx_hash = _make_tx(tp)
        assert _api_key_id_of(tp, tx_hash) is None

    def test_triggered_transactions_inherit_the_root_key(self, tp, api_keys):
        root = _make_tx(tp, api_key_id=api_keys[0].id)
        child = _make_tx(
            tp, from_address=CONTRACT_A, to_address=CONTRACT_B, triggered_by_hash=root
        )
        grandchild = _make_tx(
            tp, from_address=CONTRACT_B, to_address=CONTRACT_A, triggered_by_hash=child
        )

        assert _api_key_id_of(tp, child) == api_keys[0].id
        assert _api_key_id_of(tp, grandchild) == api_keys[0].id

    def test_child_of_unattributed_transaction_stays_unattributed(self, tp):
        root = _make_tx(tp)
        child = _make_tx(tp, from_address=CONTRACT_A, triggered_by_hash=root)
        assert _api_key_id_of(tp, child) is None

    def test_key_is_not_exposed_in_the_public_transaction(self, tp, api_keys):
        tx_hash = _make_tx(tp, api_key_id=api_keys[0].id)
        assert "api_key_id" not in tp.get_transaction_by_hash(tx_hash)


class TestUsage:
    def test_counts_direct_and_triggered_per_key(self, tp, session, api_keys):
        key_a, key_b = api_keys
        root = _make_tx(tp, api_key_id=key_a.id)
        _make_tx(tp, api_key_id=key_a.id, type=1)
        _make_tx(tp, from_address=CONTRACT_A, triggered_by_hash=root)
        _make_tx(tp, api_key_id=key_b.id)
        _make_tx(tp)
        session.query(Transactions).filter_by(hash=root).update(
            {"status": TransactionStatus.FINALIZED}
        )
        session.commit()

        usage = api_key_usage.get_usage(session)

        by_id = {entry["id"]: entry for entry in usage["keys"]}
        assert by_id[key_a.id]["key_prefix"] == "glk_aaaa"
        assert by_id[key_a.id]["tier"] == "tracking"
        assert by_id[key_a.id]["transactions"] == {
            "total": 3,
            "direct": 2,
            "triggered": 1,
            "by_status": {"FINALIZED": 1, "PENDING": 2},
            "by_type": {"RUN_CONTRACT": 2, "DEPLOY_CONTRACT": 1},
        }
        assert by_id[key_b.id]["transactions"]["total"] == 1

    def test_key_without_transactions_reports_zero(self, session, api_keys):
        usage = api_key_usage.get_usage(session, api_key_id=api_keys[1].id)

        assert [entry["id"] for entry in usage["keys"]] == [api_keys[1].id]
        assert usage["keys"][0]["transactions"] == {
            "total": 0,
            "direct": 0,
            "triggered": 0,
            "by_status": {},
            "by_type": {},
        }

    def test_filters_by_prefix_and_time_range(self, tp, session, api_keys):
        key_a = api_keys[0]
        old = _make_tx(tp, api_key_id=key_a.id)
        _make_tx(tp, api_key_id=key_a.id)
        session.query(Transactions).filter_by(hash=old).update(
            {"created_at": datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)}
        )
        session.commit()

        recent = api_key_usage.get_usage(
            session, key_prefix="glk_aaaa", from_timestamp="2021-01-01T00:00:00Z"
        )
        before = api_key_usage.get_usage(
            session, key_prefix="glk_aaaa", to_timestamp=1609459200
        )

        assert recent["keys"][0]["transactions"]["total"] == 1
        assert before["keys"][0]["transactions"]["total"] == 1
        assert recent["from"] == "2021-01-01T00:00:00+00:00"

    def test_unknown_key_is_not_found(self, session, api_keys):
        with pytest.raises(NotFoundError):
            api_key_usage.get_usage(session, key_prefix="glk_zzzz")

    def test_rejects_malformed_parameters(self, session, api_keys):
        with pytest.raises(InvalidParams):
            api_key_usage.get_usage(session, from_timestamp="yesterday")
        with pytest.raises(InvalidParams):
            api_key_usage.get_usage(session, api_key_id="1")


class TestListTransactions:
    def test_lists_newest_first_with_pagination(self, tp, session, api_keys):
        key_a, key_b = api_keys
        hashes = [_make_tx(tp, api_key_id=key_a.id) for _ in range(3)]
        child = _make_tx(tp, from_address=CONTRACT_A, triggered_by_hash=hashes[0])
        _make_tx(tp, api_key_id=key_b.id)
        for age, tx_hash in enumerate(reversed([*hashes, child])):
            session.query(Transactions).filter_by(hash=tx_hash).update(
                {
                    "created_at": datetime.datetime(
                        2024, 1, 10 - age, tzinfo=datetime.timezone.utc
                    )
                }
            )
        session.commit()

        first = api_key_usage.list_transactions(session, api_key_id=key_a.id, limit=3)
        second = api_key_usage.list_transactions(
            session, api_key_id=key_a.id, limit=3, offset=3
        )

        assert [tx["hash"] for tx in first["transactions"]] == [
            child,
            hashes[2],
            hashes[1],
        ]
        assert first["has_more"] is True
        assert first["transactions"][0]["triggered_by"] == hashes[0]
        assert first["transactions"][0]["type"] == "RUN_CONTRACT"
        assert first["transactions"][0]["status"] == "PENDING"
        assert [tx["hash"] for tx in second["transactions"]] == [hashes[0]]
        assert second["has_more"] is False

    def test_rejects_out_of_range_page(self, session, api_keys):
        with pytest.raises(InvalidParams):
            api_key_usage.list_transactions(session, limit=0)
        with pytest.raises(InvalidParams):
            api_key_usage.list_transactions(
                session, limit=api_key_usage.MAX_PAGE_SIZE + 1
            )
        with pytest.raises(InvalidParams):
            api_key_usage.list_transactions(session, offset=-1)


class TestAdminAccess:
    def test_usage_requires_the_admin_key(self, session, api_keys, monkeypatch):
        monkeypatch.setenv("ADMIN_API_KEY", "expected-admin-key")

        with pytest.raises(JSONRPCError):
            endpoints.admin_get_api_key_usage(session=session, admin_key="wrong")
        with pytest.raises(JSONRPCError):
            endpoints.admin_get_api_key_transactions(session=session)

        usage = endpoints.admin_get_api_key_usage(
            session=session, admin_key="expected-admin-key"
        )
        assert len(usage["keys"]) == 2


def _limiter(engine, redis=None, enabled=False):
    if redis is None:
        redis = AsyncMock()
        redis.hgetall = AsyncMock(return_value={})
    return RateLimiterService(
        redis_client=redis, get_session=sessionmaker(bind=engine), enabled=enabled
    )


def _last_used_at(session, api_key):
    session.expire_all()
    return session.get(ApiKey, api_key.id).last_used_at


class TestKeyResolution:
    def test_cache_miss_touches_last_used_at(self, engine, session, api_keys):
        service = _limiter(engine)

        assert asyncio.run(service.resolve_api_key_id("glk_aaaa")) == api_keys[0].id
        assert _last_used_at(session, api_keys[0]) is not None

    def test_locked_key_is_resolved_without_waiting(self, engine, session, api_keys):
        """A key locked by an open transaction (e.g. being deactivated) must not block."""
        service = _limiter(engine)
        with engine.connect() as locker:
            locker.execute(
                text("SELECT id FROM api_keys WHERE id = :id FOR UPDATE"),
                {"id": api_keys[0].id},
            )

            async def resolve():
                return await asyncio.wait_for(
                    service.resolve_api_key_id("glk_aaaa"), timeout=5
                )

            assert asyncio.run(resolve()) == api_keys[0].id
            locker.rollback()

        assert _last_used_at(session, api_keys[0]) is None


class TestDatabaseFallback:
    """Key identification when the Redis cache is unavailable."""

    @staticmethod
    def _service(engine):
        redis = AsyncMock()
        redis.hgetall = AsyncMock(side_effect=ConnectionError("redis down"))
        return _limiter(engine, redis)

    def test_active_key_is_identified_without_the_cache(self, engine, api_keys):
        service = self._service(engine)

        resolved = asyncio.run(service.resolve_api_key_id("glk_aaaa"))

        assert resolved == api_keys[0].id

    def test_inactive_and_unknown_keys_are_not_identified(
        self, engine, session, api_keys
    ):
        api_keys[1].is_active = False
        session.commit()
        service = self._service(engine)

        assert asyncio.run(service.resolve_api_key_id("glk_bbbb")) is None
        assert asyncio.run(service.resolve_api_key_id("glk_zzzz")) is None

    def test_fallback_does_not_touch_last_used_at(self, engine, session, api_keys):
        service = self._service(engine)

        asyncio.run(service.resolve_api_key_id("glk_aaaa"))

        assert _last_used_at(session, api_keys[0]) is None


def test_api_key_index_is_valid_and_partial(session):
    row = session.execute(
        text(
            """
            SELECT i.indisvalid, pg_get_indexdef(i.indexrelid)
            FROM pg_index i
            JOIN pg_class c ON c.oid = i.indexrelid
            WHERE c.relname = 'idx_transactions_api_key_created_at'
            """
        )
    ).one()

    assert row[0] is True
    assert "(api_key_id, created_at)" in row[1]
    assert "WHERE (api_key_id IS NOT NULL)" in row[1]
