"""Unit tests for per-contract daily snapshot storage quota."""

from backend.protocol_rpc.contract_storage_quota import (
    snapshot_cost_bytes,
    try_consume_daily_bytes,
    redis_key,
)
from backend.protocol_rpc.exceptions import StorageQuotaExceeded


class MemoryRedis:
    def __init__(self):
        self.store: dict[str, int] = {}

    def get(self, key: str):
        return self.store.get(key)

    def incrby(self, key: str, amount: int):
        self.store[key] = int(self.store.get(key) or 0) + int(amount)
        return self.store[key]

    def expire(self, key: str, ttl: int):
        return True


def test_snapshot_cost_is_two_slots():
    assert snapshot_cost_bytes(None) == 0
    assert snapshot_cost_bytes(0) == 0
    assert snapshot_cost_bytes(10) == 20


def test_first_write_of_day_always_allowed_even_if_over_limit():
    redis = MemoryRedis()
    ok, used = try_consume_daily_bytes(redis, "0xabc", cost=500, limit=100)
    assert ok is True
    assert used == 500


def test_second_write_rejected_when_budget_exhausted():
    redis = MemoryRedis()
    try_consume_daily_bytes(redis, "0xabc", cost=80, limit=100)
    ok, used = try_consume_daily_bytes(redis, "0xabc", cost=80, limit=100)
    assert ok is False
    assert used == 80


def test_writes_allowed_until_budget_exhausted():
    redis = MemoryRedis()
    ok1, used1 = try_consume_daily_bytes(redis, "0xabc", cost=40, limit=100)
    ok2, used2 = try_consume_daily_bytes(redis, "0xabc", cost=40, limit=100)
    ok3, used3 = try_consume_daily_bytes(redis, "0xabc", cost=40, limit=100)
    assert ok1 and ok2
    assert used1 == 40
    assert used2 == 80
    assert ok3 is False
    assert used3 == 80


def test_separate_contracts_have_independent_budgets():
    redis = MemoryRedis()
    try_consume_daily_bytes(redis, "0xaaa", cost=90, limit=100)
    ok, used = try_consume_daily_bytes(redis, "0xbbb", cost=90, limit=100)
    assert ok is True
    assert used == 90


def test_redis_key_includes_utc_day():
    assert redis_key("0xAb", day="20260813") == "studio:contract-storage:0xAb:20260813"


def test_storage_quota_error_code():
    err = StorageQuotaExceeded(message="nope", data={"scope": "contract_storage"})
    assert err.code == -32031
    assert err.data["scope"] == "contract_storage"
