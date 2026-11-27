"""Per-API-key transaction queries behind the admin usage RPC methods."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Query, Session

from backend.database_handler.models import ApiKey, ApiTier, Transactions
from backend.domain.types import TransactionType
from backend.protocol_rpc.exceptions import InvalidParams, NotFoundError

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500


def get_usage(
    session: Session,
    api_key_id: Any = None,
    key_prefix: Any = None,
    from_timestamp: Any = None,
    to_timestamp: Any = None,
) -> dict:
    """Transaction counts per API key, split by status, type and origin."""
    since = _parse_timestamp(from_timestamp, "from_timestamp")
    until = _parse_timestamp(to_timestamp, "to_timestamp")
    keys = _select_api_keys(session, api_key_id, key_prefix)
    counts = {key.id: _empty_counts() for key in keys}

    if counts:
        is_triggered = Transactions.triggered_by_hash.isnot(None)
        query = session.query(
            Transactions.api_key_id,
            Transactions.status,
            Transactions.type,
            is_triggered,
            func.count(),
        ).filter(Transactions.api_key_id.in_(list(counts)))
        query = _in_time_range(query, since, until).group_by(
            Transactions.api_key_id,
            Transactions.status,
            Transactions.type,
            is_triggered,
        )
        for key_id, status, tx_type, triggered, count in query:
            entry = counts[key_id]
            entry["total"] += count
            entry["triggered" if triggered else "direct"] += count
            status_name = getattr(status, "value", str(status))
            entry["by_status"][status_name] = (
                entry["by_status"].get(status_name, 0) + count
            )
            type_name = _type_name(tx_type)
            entry["by_type"][type_name] = entry["by_type"].get(type_name, 0) + count

    tier_names = _tier_names(session, keys)
    return {
        "from": _iso(since),
        "to": _iso(until),
        "keys": [
            {**_describe_key(key, tier_names), "transactions": counts[key.id]}
            for key in keys
        ],
    }


def list_transactions(
    session: Session,
    api_key_id: Any = None,
    key_prefix: Any = None,
    from_timestamp: Any = None,
    to_timestamp: Any = None,
    limit: Any = DEFAULT_PAGE_SIZE,
    offset: Any = 0,
) -> dict:
    """Transactions attributed to an API key, newest first."""
    since = _parse_timestamp(from_timestamp, "from_timestamp")
    until = _parse_timestamp(to_timestamp, "to_timestamp")
    limit = _bounded_int(limit, "limit", 1, MAX_PAGE_SIZE)
    offset = _bounded_int(offset, "offset", 0, None)
    key_ids = [key.id for key in _select_api_keys(session, api_key_id, key_prefix)]

    rows = []
    if key_ids:
        query = session.query(
            Transactions.hash,
            Transactions.api_key_id,
            Transactions.status,
            Transactions.type,
            Transactions.from_address,
            Transactions.to_address,
            Transactions.created_at,
            Transactions.triggered_by_hash,
        ).filter(Transactions.api_key_id.in_(key_ids))
        rows = (
            _in_time_range(query, since, until)
            .order_by(Transactions.created_at.desc(), Transactions.hash)
            .limit(limit + 1)
            .offset(offset)
            .all()
        )

    return {
        "from": _iso(since),
        "to": _iso(until),
        "limit": limit,
        "offset": offset,
        "has_more": len(rows) > limit,
        "transactions": [
            {
                "hash": row.hash,
                "api_key_id": row.api_key_id,
                "status": getattr(row.status, "value", str(row.status)),
                "type": _type_name(row.type),
                "from_address": row.from_address,
                "to_address": row.to_address,
                "created_at": _iso(row.created_at),
                "triggered_by": row.triggered_by_hash,
            }
            for row in rows[:limit]
        ],
    }


def _select_api_keys(session: Session, api_key_id: Any, key_prefix: Any) -> list:
    query = session.query(ApiKey)
    filtered = False
    if api_key_id is not None:
        query = query.filter(ApiKey.id == _bounded_int(api_key_id, "api_key_id", 1))
        filtered = True
    if key_prefix is not None:
        if not isinstance(key_prefix, str) or not key_prefix:
            raise InvalidParams(message="key_prefix must be a non-empty string")
        query = query.filter(ApiKey.key_prefix == key_prefix)
        filtered = True

    keys = query.order_by(ApiKey.id).all()
    if filtered and not keys:
        raise NotFoundError(message="API key not found")
    return keys


def _tier_names(session: Session, keys: list) -> dict:
    tier_ids = {key.tier_id for key in keys}
    if not tier_ids:
        return {}
    tiers = session.query(ApiTier.id, ApiTier.name).filter(ApiTier.id.in_(tier_ids))
    return {tier_id: name for tier_id, name in tiers}


def _describe_key(key: ApiKey, tier_names: dict) -> dict:
    return {
        "id": key.id,
        "key_prefix": key.key_prefix,
        "description": key.description,
        "tier": tier_names.get(key.tier_id),
        "is_active": key.is_active,
        "created_at": _iso(key.created_at),
        "last_used_at": _iso(key.last_used_at),
    }


def _empty_counts() -> dict:
    return {"total": 0, "direct": 0, "triggered": 0, "by_status": {}, "by_type": {}}


def _in_time_range(
    query: Query, since: Optional[datetime], until: Optional[datetime]
) -> Query:
    if since is not None:
        query = query.filter(Transactions.created_at >= since)
    if until is not None:
        query = query.filter(Transactions.created_at < until)
    return query


def _type_name(tx_type: Any) -> str:
    try:
        return TransactionType(tx_type).name
    except ValueError:
        return str(tx_type)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _bounded_int(
    value: Any, name: str, minimum: int, maximum: Optional[int] = None
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidParams(message=f"{name} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        bounds = f">= {minimum}" if maximum is None else f"{minimum}..{maximum}"
        raise InvalidParams(message=f"{name} must be {bounds}")
    return value


def _parse_timestamp(value: Any, name: str) -> Optional[datetime]:
    if value is None:
        return None
    error = InvalidParams(
        message=f"{name} must be an ISO 8601 timestamp or epoch seconds"
    )
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise error
    try:
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            parsed = datetime.fromtimestamp(value, tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        raise error from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
