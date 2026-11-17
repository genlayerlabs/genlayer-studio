"""Per-contract daily budget for contract_snapshot bytes.

Studio copies live current_state into transactions.contract_snapshot on
every write. A slow writer with unbounded on-chain history can add tens
of GiB/day without tripping request or PENDING-queue caps.

This module meters estimated snapshot bytes per contract per UTC day.
Unset MAX_CONTRACT_SNAPSHOT_BYTES_PER_DAY disables it (self-hosted
default). Redis failures fail open. The first write of the day is always
allowed so a contract whose single snapshot exceeds the daily budget is
not hard-locked.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from eth_utils import to_checksum_address
from sqlalchemy import text

from backend.protocol_rpc.exceptions import StorageQuotaExceeded

logger = logging.getLogger(__name__)

_REDIS_KEY_TTL_SECONDS = 48 * 3600
_STORAGE_HELP = (
    "The public Studio is a shared sandbox with a per-contract daily "
    "snapshot storage budget. For production-volume workloads, run a "
    "self-hosted instance."
)

_redis_client: Any = None
_redis_init_attempted = False

_CONSUME_SCRIPT = """
local used = tonumber(redis.call('GET', KEYS[1]) or '0')
if redis.call('EXISTS', KEYS[2]) == 1 then
    return {1, used, 0}
end
local cost = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
if used > 0 and used + cost > limit then
    return {0, used, 0}
end
local new_used = redis.call('INCRBY', KEYS[1], cost)
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
redis.call('SET', KEYS[2], cost, 'EX', tonumber(ARGV[3]))
return {1, new_used, 1}
"""

_RELEASE_SCRIPT = """
local cost = tonumber(redis.call('GET', KEYS[2]) or '0')
if cost == 0 then
    return 0
end
redis.call('DEL', KEYS[2])
local used = tonumber(redis.call('GET', KEYS[1]) or '0')
local new_used = used - cost
if new_used <= 0 then
    redis.call('DEL', KEYS[1])
    return 0
end
redis.call('DECRBY', KEYS[1], cost)
return new_used
"""


@dataclass(frozen=True)
class StorageQuotaReservation:
    redis_client: Any
    quota_key: str
    reservation_key: str
    owned: bool

    def release(self) -> None:
        """Refund this request's reservation after admission fails."""
        if not self.owned:
            return
        try:
            self.redis_client.eval(
                _RELEASE_SCRIPT, 2, self.quota_key, self.reservation_key
            )
        except Exception:
            logger.exception("Failed to refund contract storage quota reservation")


def reset_storage_quota_client_for_tests() -> None:
    global _redis_client, _redis_init_attempted
    _redis_client = None
    _redis_init_attempted = False


def _parse_optional_positive_int(env_name: str) -> int | None:
    raw = os.environ.get(env_name)
    if raw is None or raw.strip() == "":
        return None
    try:
        parsed = int(raw)
    except (ValueError, TypeError):
        return None
    return parsed if parsed > 0 else None


def daily_byte_limit() -> int | None:
    return _parse_optional_positive_int("MAX_CONTRACT_SNAPSHOT_BYTES_PER_DAY")


def snapshot_cost_bytes(live_state_column_size: int | None) -> int:
    """Estimate the next snapshot from the already-complete live state row.

    ``current_state.data`` contains both accepted and finalized slots, matching
    the shape copied into ``transactions.contract_snapshot``.  Multiplying it
    again would double-count the snapshot cost.
    """
    if not live_state_column_size:
        return 0
    return live_state_column_size


def redis_key(address: str, day: str | None = None) -> str:
    day_token = day or datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"studio:contract-storage:{address}:{day_token}"


def reservation_key(address: str, transaction_hash: str, day: str | None = None) -> str:
    return f"{redis_key(address, day)}:tx:{transaction_hash}"


def _get_redis():
    global _redis_client, _redis_init_attempted
    if _redis_init_attempted:
        return _redis_client
    _redis_init_attempted = True
    url = os.environ.get("REDIS_URL")
    if not url:
        logger.warning(
            "MAX_CONTRACT_SNAPSHOT_BYTES_PER_DAY is set but REDIS_URL is empty; "
            "storage quota check skipped"
        )
        return None
    try:
        import redis

        _redis_client = redis.from_url(url, decode_responses=True)
    except Exception:
        logger.exception("Failed to connect Redis for contract storage quota")
        _redis_client = None
    return _redis_client


def live_state_column_size(session, address: str) -> int | None:
    """Return pg_column_size(data) without hydrating JSONB. None if missing."""
    try:
        normalized = to_checksum_address(address)
    except Exception:
        normalized = address
    return session.execute(
        text("SELECT pg_column_size(data) FROM current_state WHERE id = :addr"),
        {"addr": normalized},
    ).scalar()


def try_consume_daily_bytes(
    redis_client,
    address: str,
    cost: int,
    limit: int,
    transaction_hash: str,
) -> tuple[bool, int, StorageQuotaReservation | None]:
    """Atomically reserve `cost` against `limit`.

    First write of the UTC day always succeeds. The transaction marker makes
    concurrent submissions of the same hash idempotent.
    """
    quota_key = redis_key(address)
    tx_key = reservation_key(address, transaction_hash)
    ok, used, owned = redis_client.eval(
        _CONSUME_SCRIPT,
        2,
        quota_key,
        tx_key,
        cost,
        limit,
        _REDIS_KEY_TTL_SECONDS,
    )
    reservation = StorageQuotaReservation(redis_client, quota_key, tx_key, bool(owned))
    return bool(ok), int(used), reservation if ok else None


def enforce_contract_storage_quota(
    session, to_address: str | None, transaction_hash: str
) -> StorageQuotaReservation | None:
    """Raise StorageQuotaExceeded when the contract is over its daily budget."""
    limit = daily_byte_limit()
    if limit is None or to_address is None:
        return

    try:
        to_address = to_checksum_address(to_address)
    except Exception:
        pass

    size = live_state_column_size(session, to_address)
    cost = snapshot_cost_bytes(size)
    if cost <= 0:
        return

    redis_client = _get_redis()
    if redis_client is None:
        return

    try:
        ok, used, reservation = try_consume_daily_bytes(
            redis_client, to_address, cost, limit, transaction_hash
        )
    except Exception:
        logger.exception(
            "Contract storage quota Redis error; failing open for %s", to_address
        )
        return

    if ok:
        return reservation

    raise StorageQuotaExceeded(
        message=(
            f"Contract {to_address} exceeded the daily snapshot storage "
            f"budget ({used} of {limit} bytes used; this write costs {cost}). "
            f"{_STORAGE_HELP}"
        ),
        data={
            "scope": "contract_storage",
            "address": to_address,
            "used": used,
            "limit": limit,
            "cost": cost,
        },
    )
