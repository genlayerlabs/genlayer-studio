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
from datetime import datetime, timezone
from typing import Any

from eth_utils import to_checksum_address
from sqlalchemy import text

from backend.protocol_rpc.exceptions import StorageQuotaExceeded

logger = logging.getLogger(__name__)

# Snapshot stores accepted + finalized slots; live current_state is one slot.
_SNAPSHOT_SLOT_FACTOR = 2
_REDIS_KEY_TTL_SECONDS = 48 * 3600
_STORAGE_HELP = (
    "The public Studio is a shared sandbox with a per-contract daily "
    "snapshot storage budget. For production-volume workloads, run a "
    "self-hosted instance or use Rally."
)

_redis_client: Any = None
_redis_init_attempted = False


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
    if not live_state_column_size:
        return 0
    return live_state_column_size * _SNAPSHOT_SLOT_FACTOR


def redis_key(address: str, day: str | None = None) -> str:
    day_token = day or datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"studio:contract-storage:{address}:{day_token}"


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
) -> tuple[bool, int]:
    """Atomically-enough consume `cost` against `limit`. Returns (ok, used).

    First write of the UTC day always succeeds. Concurrent submits can
    overshoot by a few; that is accepted.
    """
    key = redis_key(address)
    used = int(redis_client.get(key) or 0)
    if used > 0 and used + cost > limit:
        return False, used
    new_used = int(redis_client.incrby(key, cost))
    redis_client.expire(key, _REDIS_KEY_TTL_SECONDS)
    return True, new_used


def enforce_contract_storage_quota(session, to_address: str | None) -> None:
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
        ok, used = try_consume_daily_bytes(redis_client, to_address, cost, limit)
    except Exception:
        logger.exception(
            "Contract storage quota Redis error; failing open for %s", to_address
        )
        return

    if ok:
        return

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
