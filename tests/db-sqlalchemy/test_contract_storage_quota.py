"""DB tests for live-state size lookup and storage quota admission."""

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import sessionmaker

from backend.protocol_rpc.contract_storage_quota import (
    enforce_contract_storage_quota,
    live_state_column_size,
    snapshot_cost_bytes,
)
from eth_utils import to_checksum_address

from backend.protocol_rpc.exceptions import StorageQuotaExceeded

CONTRACT = to_checksum_address("0x" + "ab" * 20)


def _seed_state(session, address: str, payload: dict) -> None:
    session.execute(
        text(
            """
            INSERT INTO current_state (id, data, balance)
            VALUES (:id, CAST(:data AS jsonb), 0)
            """
        ),
        {"id": address, "data": __import__("json").dumps(payload)},
    )
    session.commit()


def test_missing_contract_returns_none(engine: Engine):
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    with Session_() as session:
        assert live_state_column_size(session, CONTRACT) is None


def test_live_state_column_size_does_not_require_python_hydrate(engine: Engine):
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    with Session_() as session:
        _seed_state(session, CONTRACT, {"k": "v" * 1000})
        size = live_state_column_size(session, CONTRACT)
        assert size is not None
        assert size > 0
        assert snapshot_cost_bytes(size) == size * 2


def test_quota_disabled_when_env_unset(engine: Engine, monkeypatch):
    monkeypatch.delenv("MAX_CONTRACT_SNAPSHOT_BYTES_PER_DAY", raising=False)
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    with Session_() as session:
        _seed_state(session, CONTRACT, {"blob": "x" * 5000})
        enforce_contract_storage_quota(session, CONTRACT)


def test_quota_rejects_after_first_write(engine: Engine, monkeypatch):
    monkeypatch.setenv("MAX_CONTRACT_SNAPSHOT_BYTES_PER_DAY", "50")
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)

    class MemoryRedis:
        def __init__(self):
            self.store = {}

        def get(self, key):
            return self.store.get(key)

        def incrby(self, key, amount):
            self.store[key] = int(self.store.get(key) or 0) + int(amount)
            return self.store[key]

        def expire(self, key, ttl):
            return True

    redis = MemoryRedis()
    monkeypatch.setattr(
        "backend.protocol_rpc.contract_storage_quota._get_redis",
        lambda: redis,
    )
    with Session_() as session:
        _seed_state(session, CONTRACT, {"blob": "x" * 5000})
        enforce_contract_storage_quota(session, CONTRACT)
        with pytest.raises(StorageQuotaExceeded) as exc_info:
            enforce_contract_storage_quota(session, CONTRACT)
        assert exc_info.value.code == -32031
        assert exc_info.value.data["scope"] == "contract_storage"
        assert exc_info.value.data["address"] == CONTRACT
        assert "self-hosted" in exc_info.value.message.lower()
