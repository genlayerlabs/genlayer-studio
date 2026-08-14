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

    def eval(self, script, numkeys, quota_key, reservation_key, *args):
        if args:
            cost, limit, _ttl = map(int, args)
            used = self.store.get(quota_key, 0)
            if reservation_key in self.store:
                return [1, used, 0]
            if used > 0 and used + cost > limit:
                return [0, used, 0]
            self.store[quota_key] = used + cost
            self.store[reservation_key] = cost
            return [1, used + cost, 1]
        cost = self.store.pop(reservation_key, 0)
        self.store[quota_key] = max(0, self.store.get(quota_key, 0) - cost)
        return self.store[quota_key]


def test_snapshot_cost_matches_complete_live_state_row():
    assert snapshot_cost_bytes(None) == 0
    assert snapshot_cost_bytes(0) == 0
    assert snapshot_cost_bytes(10) == 10


def test_first_write_of_day_always_allowed_even_if_over_limit():
    redis = MemoryRedis()
    ok, used, _ = try_consume_daily_bytes(
        redis, "0xabc", cost=500, limit=100, transaction_hash="first-tx"
    )
    assert ok is True
    assert used == 500


def test_second_write_rejected_when_budget_exhausted():
    redis = MemoryRedis()
    try_consume_daily_bytes(redis, "0xabc", cost=80, limit=100, transaction_hash="tx1")
    ok, used, _ = try_consume_daily_bytes(
        redis, "0xabc", cost=80, limit=100, transaction_hash="tx2"
    )
    assert ok is False
    assert used == 80


def test_writes_allowed_until_budget_exhausted():
    redis = MemoryRedis()
    ok1, used1, _ = try_consume_daily_bytes(
        redis, "0xabc", cost=40, limit=100, transaction_hash="tx1"
    )
    ok2, used2, _ = try_consume_daily_bytes(
        redis, "0xabc", cost=40, limit=100, transaction_hash="tx2"
    )
    ok3, used3, _ = try_consume_daily_bytes(
        redis, "0xabc", cost=40, limit=100, transaction_hash="tx3"
    )
    assert ok1 and ok2
    assert used1 == 40
    assert used2 == 80
    assert ok3 is False
    assert used3 == 80


def test_separate_contracts_have_independent_budgets():
    redis = MemoryRedis()
    try_consume_daily_bytes(redis, "0xaaa", cost=90, limit=100, transaction_hash="tx-a")
    ok, used, _ = try_consume_daily_bytes(
        redis, "0xbbb", cost=90, limit=100, transaction_hash="tx-b"
    )
    assert ok is True
    assert used == 90


def test_duplicate_transaction_hash_is_only_charged_once():
    redis = MemoryRedis()
    _, first_used, first = try_consume_daily_bytes(
        redis, "0xabc", cost=40, limit=100, transaction_hash="same-tx"
    )
    _, second_used, second = try_consume_daily_bytes(
        redis, "0xabc", cost=40, limit=100, transaction_hash="same-tx"
    )
    assert first_used == second_used == 40
    assert first is not None and first.owned is True
    assert second is not None and second.owned is False


def test_failed_admission_can_refund_owned_reservation():
    redis = MemoryRedis()
    _, _, reservation = try_consume_daily_bytes(
        redis, "0xabc", cost=80, limit=100, transaction_hash="failed-tx"
    )
    assert reservation is not None
    reservation.release()
    ok, used, _ = try_consume_daily_bytes(
        redis, "0xabc", cost=80, limit=100, transaction_hash="replacement-tx"
    )
    assert ok is True
    assert used == 80


def test_redis_key_includes_utc_day():
    assert redis_key("0xAb", day="20260813") == "studio:contract-storage:0xAb:20260813"


def test_storage_quota_error_code():
    err = StorageQuotaExceeded(message="nope", data={"scope": "contract_storage"})
    assert err.code == -32031
    assert err.data["scope"] == "contract_storage"
