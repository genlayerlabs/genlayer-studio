"""SQLAlchemy queries for the explorer API."""

import base64
import math
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import asc, desc, func, or_, select, union_all
from sqlalchemy.orm import Session, defer

from backend.database_handler.models import (
    CurrentState,
    LLMProviderDBModel,
    Transactions,
    TransactionStatus,
    Validators,
)


def _serialize_tx(
    tx: Transactions,
    triggered_count: int | None = None,
) -> dict:
    """Serialize a Transactions ORM object to a dict for the explorer API."""
    d = {
        "hash": tx.hash,
        "status": tx.status.value if tx.status else None,
        "from_address": tx.from_address,
        "to_address": tx.to_address,
        "input_data": tx.input_data,
        "data": tx.data,
        "consensus_data": tx.consensus_data,
        "nonce": tx.nonce,
        "value": tx.value,
        "type": tx.type,
        "gaslimit": tx.gaslimit,
        "created_at": tx.created_at.isoformat() if tx.created_at else None,
        "leader_only": tx.leader_only,
        "execution_mode": tx.execution_mode,
        "r": tx.r,
        "s": tx.s,
        "v": tx.v,
        "appeal_failed": tx.appeal_failed,
        "consensus_history": tx.consensus_history,
        "timestamp_appeal": tx.timestamp_appeal,
        "appeal_processing_time": tx.appeal_processing_time,
        "config_rotation_rounds": tx.config_rotation_rounds,
        "num_of_initial_validators": tx.num_of_initial_validators,
        "last_vote_timestamp": tx.last_vote_timestamp,
        "rotation_count": tx.rotation_count,
        "leader_timeout_validators": tx.leader_timeout_validators,
        "sim_config": tx.sim_config,
        "triggered_by_hash": tx.triggered_by_hash,
        "triggered_on": tx.triggered_on,
        "appealed": tx.appealed,
        "appeal_undetermined": tx.appeal_undetermined,
        "appeal_leader_timeout": tx.appeal_leader_timeout,
        "appeal_validators_timeout": tx.appeal_validators_timeout,
        "timestamp_awaiting_finalization": tx.timestamp_awaiting_finalization,
        "blocked_at": tx.blocked_at.isoformat() if tx.blocked_at else None,
        "worker_id": tx.worker_id,
    }
    if triggered_count is not None:
        d["triggered_count"] = triggered_count
    return d


def _serialize_state(
    state: CurrentState,
    *,
    tx_count: int | None = None,
    include_data: bool = True,
) -> dict:
    d = {
        "id": state.id,
        "balance": state.balance,
        "updated_at": state.updated_at.isoformat() if state.updated_at else None,
    }
    if include_data:
        d["data"] = state.data
    if tx_count is not None:
        d["tx_count"] = tx_count
    return d


def _serialize_validator(v: Validators) -> dict:
    return {
        "id": v.id,
        "stake": v.stake,
        "config": v.config,
        "address": v.address,
        "provider": v.provider,
        "model": v.model,
        "plugin": v.plugin,
        "plugin_config": v.plugin_config,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }


def _serialize_provider(p: LLMProviderDBModel) -> dict:
    return {
        "id": p.id,
        "provider": p.provider,
        "model": p.model,
        "config": p.config,
        "plugin": p.plugin,
        "plugin_config": p.plugin_config,
        "is_default": p.is_default,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


# Columns to defer when loading transaction lists (large JSONB blobs that are
# only needed in the detail view).
_HEAVY_TX_COLUMNS = (defer(Transactions.contract_snapshot),)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def _count_deployed_contracts(session: Session) -> int:
    """Count contract states that actually have a deploy transaction."""
    deploy_addresses = (
        session.query(Transactions.to_address)
        .filter(Transactions.type == 1)
        .distinct()
        .subquery()
    )
    return (
        session.query(func.count())
        .select_from(CurrentState)
        .filter(CurrentState.id.in_(select(deploy_addresses.c.to_address)))
        .scalar()
        or 0
    )


def get_stats_counts(session: Session) -> dict:
    """Lightweight counts for the stats bar (no heavy queries)."""
    total_tx = session.query(func.count()).select_from(Transactions).scalar() or 0
    total_validators = session.query(func.count()).select_from(Validators).scalar() or 0
    total_contracts = _count_deployed_contracts(session)
    return {
        "totalTransactions": total_tx,
        "totalValidators": total_validators,
        "totalContracts": total_contracts,
    }


def get_stats(session: Session) -> dict:
    # Status breakdown — summing gives total, so one fewer query.
    status_rows = (
        session.query(Transactions.status, func.count())
        .group_by(Transactions.status)
        .all()
    )
    by_status: dict[str, int] = {}
    total = 0
    for status_enum, cnt in status_rows:
        by_status[status_enum.value] = cnt
        total += cnt

    # Parallel lightweight counts
    total_validators = session.query(func.count()).select_from(Validators).scalar() or 0
    total_contracts = _count_deployed_contracts(session)

    # type 1 = DEPLOY_CONTRACT (see domain/types.py TransactionType)
    deploy_count = (
        session.query(func.count())
        .select_from(Transactions)
        .filter(Transactions.type == 1)
        .scalar()
        or 0
    )

    appealed = (
        session.query(func.count())
        .select_from(Transactions)
        .filter(Transactions.appealed.is_(True))
        .scalar()
        or 0
    )

    # Recent transactions — skip the heavy contract_snapshot column.
    recent = (
        session.query(Transactions)
        .options(*_HEAVY_TX_COLUMNS)
        .order_by(Transactions.created_at.desc())
        .limit(10)
        .all()
    )

    # Finalized count from the status breakdown
    finalized_count = by_status.get("FINALIZED", 0)

    # Average TPS over the last 24 hours
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(hours=24)
    tx_last_24h = (
        session.query(func.count())
        .select_from(Transactions)
        .filter(Transactions.created_at >= day_ago)
        .scalar()
        or 0
    )
    avg_tps_24h = round(tx_last_24h / 86400, 4)

    # 14-day transaction volume (grouped by date, filled to today)
    today = now.date()
    fourteen_days_ago = now - timedelta(days=13)  # 14 days including today
    volume_rows = (
        session.query(
            func.date(Transactions.created_at).label("date"),
            func.count().label("count"),
        )
        .filter(Transactions.created_at >= fourteen_days_ago)
        .group_by(func.date(Transactions.created_at))
        .order_by(func.date(Transactions.created_at))
        .all()
    )
    counts_by_date = {row.date: row.count for row in volume_rows}
    tx_volume_14d = [
        {
            "date": (today - timedelta(days=13 - i)).isoformat(),
            "count": counts_by_date.get(today - timedelta(days=13 - i), 0),
        }
        for i in range(14)
    ]

    return {
        "totalTransactions": total,
        "transactionsByStatus": by_status,
        "transactionsByType": {
            "deploy": deploy_count,
            "call": total - deploy_count,
        },
        "totalValidators": total_validators,
        "totalContracts": total_contracts,
        "appealedTransactions": appealed,
        "finalizedTransactions": finalized_count,
        "avgTps24h": avg_tps_24h,
        "txVolume14d": tx_volume_14d,
        "recentTransactions": [
            _serialize_tx(tx, ) for tx in recent
        ],
    }


# ---------------------------------------------------------------------------
# Transactions (paginated list)
# ---------------------------------------------------------------------------


def get_all_transactions_paginated(
    session: Session,
    page: int = 1,
    limit: int = 20,
    status: Optional[str] = None,
    search: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> dict:
    filters = []
    if status:
        # Support comma-separated status values for multi-status filtering
        status_values = [s.strip() for s in status.split(",") if s.strip()]
        try:
            parsed = [TransactionStatus(s) for s in status_values]
            if len(parsed) == 1:
                filters.append(Transactions.status == parsed[0])
            else:
                filters.append(Transactions.status.in_(parsed))
        except ValueError:
            # Invalid status value — return empty results
            return {
                "transactions": [],
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "total": 0,
                    "totalPages": 0,
                },
            }
    if search:
        like = f"%{search}%"
        filters.append(
            or_(
                Transactions.hash.ilike(like),
                Transactions.from_address.ilike(like),
                Transactions.to_address.ilike(like),
            )
        )
    if from_date:
        try:
            dt = datetime.fromisoformat(from_date)
            filters.append(Transactions.created_at >= dt)
        except ValueError:
            pass
    if to_date:
        try:
            dt = datetime.fromisoformat(to_date)
            filters.append(Transactions.created_at <= dt)
        except ValueError:
            pass

    count_q = session.query(func.count()).select_from(Transactions)
    if filters:
        count_q = count_q.filter(*filters)
    total = count_q.scalar() or 0

    offset = (page - 1) * limit
    q = (
        session.query(Transactions)
        .options(*_HEAVY_TX_COLUMNS)
        .order_by(Transactions.created_at.desc())
    )
    if filters:
        q = q.filter(*filters)
    txs = q.offset(offset).limit(limit).all()

    # Batch-fetch triggered counts for this page
    hashes = [tx.hash for tx in txs]
    triggered_counts: dict[str, int] = {}
    if hashes:
        rows = (
            session.query(Transactions.triggered_by_hash, func.count())
            .filter(Transactions.triggered_by_hash.in_(hashes))
            .group_by(Transactions.triggered_by_hash)
            .all()
        )
        triggered_counts = {row[0]: row[1] for row in rows}

    return {
        "transactions": [
            _serialize_tx(tx, triggered_counts.get(tx.hash, 0), )
            for tx in txs
        ],
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "totalPages": math.ceil(total / limit) if limit > 0 else 0,
        },
    }


# ---------------------------------------------------------------------------
# Single transaction with relations
# ---------------------------------------------------------------------------


def get_transaction_with_relations(session: Session, tx_hash: str) -> Optional[dict]:
    tx = (
        session.query(Transactions)
        .options(*_HEAVY_TX_COLUMNS)
        .filter(Transactions.hash == tx_hash)
        .first()
    )
    if not tx:
        return None
    triggered = (
        session.query(Transactions)
        .options(*_HEAVY_TX_COLUMNS)
        .filter(Transactions.triggered_by_hash == tx_hash)
        .order_by(Transactions.created_at)
        .all()
    )

    parent = None
    if tx.triggered_by_hash:
        parent = (
            session.query(Transactions)
            .options(*_HEAVY_TX_COLUMNS)
            .filter(Transactions.hash == tx.triggered_by_hash)
            .first()
        )

    return {
        "transaction": _serialize_tx(tx),
        "triggeredTransactions": [
            _serialize_tx(t, ) for t in triggered
        ],
        "parentTransaction": (
            _serialize_tx(parent, ) if parent else None
        ),
    }


# ---------------------------------------------------------------------------
# Delete transaction
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


def get_all_states(
    session: Session,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 20,
    sort_by: Optional[str] = None,
    sort_order: Optional[str] = "desc",
) -> dict:
    # Only show addresses that have a deploy transaction (type 1) targeting them
    deploy_addresses = (
        session.query(Transactions.to_address)
        .filter(Transactions.type == 1)
        .distinct()
        .subquery()
    )
    base_filter = CurrentState.id.in_(select(deploy_addresses.c.to_address))

    # --- Total count (lightweight, no correlated subqueries) ---
    count_q = (
        session.query(func.count())
        .select_from(CurrentState)
        .filter(base_filter)
    )
    if search:
        count_q = count_q.filter(CurrentState.id.ilike(f"%{search}%"))
    total = count_q.scalar() or 0

    if total == 0:
        return _empty_page(page, limit)

    order_dir = asc if sort_order == "asc" else desc

    if sort_by in ("tx_count", "created_at"):
        # Pre-aggregate tx stats per contract in one pass (no correlated subqueries).
        # Count to_address and from_address matches separately, then combine.
        to_stats = (
            session.query(
                Transactions.to_address.label("addr"),
                func.count().label("cnt"),
                func.min(Transactions.created_at).label("min_ts"),
            )
            .group_by(Transactions.to_address)
            .subquery()
        )
        from_stats = (
            session.query(
                Transactions.from_address.label("addr"),
                func.count().label("cnt"),
                func.min(Transactions.created_at).label("min_ts"),
            )
            .group_by(Transactions.from_address)
            .subquery()
        )

        tx_count_col = (
            func.coalesce(to_stats.c.cnt, 0) + func.coalesce(from_stats.c.cnt, 0)
        )
        created_at_col = func.least(
            func.coalesce(to_stats.c.min_ts, from_stats.c.min_ts),
            func.coalesce(from_stats.c.min_ts, to_stats.c.min_ts),
        )

        q = (
            session.query(
                CurrentState,
                tx_count_col.label("tx_count"),
                created_at_col.label("created_at"),
            )
            .outerjoin(to_stats, CurrentState.id == to_stats.c.addr)
            .outerjoin(from_stats, CurrentState.id == from_stats.c.addr)
            .filter(base_filter)
        )
        if search:
            q = q.filter(CurrentState.id.ilike(f"%{search}%"))

        sort_col = tx_count_col if sort_by == "tx_count" else created_at_col
        q = q.order_by(order_dir(sort_col))
        q = q.offset((page - 1) * limit).limit(limit)
        rows = q.all()

        return {
            "states": [
                {
                    **_serialize_state(state, tx_count=tx_count, include_data=False),
                    "created_at": created_at.isoformat() if created_at else None,
                }
                for state, tx_count, created_at in rows
            ],
            "pagination": _pagination(page, limit, total),
        }

    # Default: sort by updated_at — paginate first (fast), then batch-fetch stats.
    q = session.query(CurrentState).filter(base_filter)
    if search:
        q = q.filter(CurrentState.id.ilike(f"%{search}%"))
    q = q.order_by(order_dir(CurrentState.updated_at))
    q = q.offset((page - 1) * limit).limit(limit)
    states = q.all()

    if not states:
        return _empty_page(page, limit, total)

    # Batch-fetch tx stats for just this page of contracts.
    page_ids = [s.id for s in states]
    stats_map = _batch_contract_stats(session, page_ids)

    return {
        "states": [
            {
                **_serialize_state(state, tx_count=stats_map.get(state.id, (0, None))[0], include_data=False),
                "created_at": (
                    stats_map.get(state.id, (0, None))[1].isoformat()
                    if stats_map.get(state.id, (0, None))[1]
                    else None
                ),
            }
            for state in states
        ],
        "pagination": _pagination(page, limit, total),
    }


def _batch_contract_stats(
    session: Session, contract_ids: list[str]
) -> dict[str, tuple[int, Optional[datetime]]]:
    """Fetch tx_count and earliest created_at for a batch of contract addresses.

    Returns a dict mapping contract_id -> (tx_count, created_at).
    """
    to_q = (
        session.query(
            Transactions.to_address.label("addr"),
            func.count().label("cnt"),
            func.min(Transactions.created_at).label("min_ts"),
        )
        .filter(Transactions.to_address.in_(contract_ids))
        .group_by(Transactions.to_address)
    )
    from_q = (
        session.query(
            Transactions.from_address.label("addr"),
            func.count().label("cnt"),
            func.min(Transactions.created_at).label("min_ts"),
        )
        .filter(Transactions.from_address.in_(contract_ids))
        .group_by(Transactions.from_address)
    )

    combined = union_all(to_q, from_q).subquery()
    rows = (
        session.query(
            combined.c.addr,
            func.sum(combined.c.cnt).label("tx_count"),
            func.min(combined.c.min_ts).label("created_at"),
        )
        .group_by(combined.c.addr)
        .all()
    )
    return {row.addr: (int(row.tx_count), row.created_at) for row in rows}


def _pagination(page: int, limit: int, total: int) -> dict:
    return {
        "page": page,
        "limit": limit,
        "total": total,
        "totalPages": (total + limit - 1) // limit if total > 0 else 0,
    }


def _empty_page(page: int, limit: int, total: int = 0) -> dict:
    return {"states": [], "pagination": _pagination(page, limit, total)}


def _extract_contract_code(session: Session, state_id: str) -> Optional[str]:
    """Find the contract source code for a given contract address.

    Looks at the ``data`` JSONB column of the deploy transaction (type 0) for
    this contract.  The ``contract_code`` field is stored as a base64-encoded
    string; we decode it to return the Python source code.
    """
    row = (
        session.query(Transactions.data)
        .filter(
            Transactions.to_address == state_id,
            Transactions.data["contract_code"].as_string().isnot(None),
        )
        .order_by(Transactions.created_at.desc())
        .first()
    )
    if not row or not row[0]:
        return None
    code_b64 = row[0].get("contract_code")
    if not code_b64:
        return None
    try:
        return base64.b64decode(code_b64).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        # If it's already plain text, return as-is
        return code_b64 if isinstance(code_b64, str) else None


def get_state_with_transactions(session: Session, state_id: str) -> Optional[dict]:
    state = session.query(CurrentState).filter(CurrentState.id == state_id).first()
    if not state:
        return None

    txs = (
        session.query(Transactions)
        .options(*_HEAVY_TX_COLUMNS)
        .filter(
            or_(
                Transactions.to_address == state_id,
                Transactions.from_address == state_id,
            )
        )
        .order_by(Transactions.created_at.desc())
        .limit(50)
        .all()
    )

    contract_code = _extract_contract_code(session, state_id)

    # Find the deploy transaction to get creator info
    creator_info = None
    deploy_tx = (
        session.query(Transactions)
        .filter(
            Transactions.to_address == state_id,
            Transactions.type == 1,
        )
        .order_by(Transactions.created_at.asc())
        .first()
    )
    if deploy_tx:
        creator_info = {
            "creator_address": deploy_tx.from_address,
            "deployment_tx_hash": deploy_tx.hash,
            "creation_timestamp": (
                deploy_tx.created_at.isoformat() if deploy_tx.created_at else None
            ),
        }

    return {
        "state": _serialize_state(state),
        "transactions": [_serialize_tx(tx, ) for tx in txs],
        "contract_code": contract_code,
        "creator_info": creator_info,
    }


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


def get_address_info(session: Session, address: str) -> Optional[dict]:
    """Resolve an address to its type (CONTRACT, VALIDATOR, or ACCOUNT) and return
    relevant data."""

    # 1. Check if it's a contract (exists in CurrentState with a deploy tx)
    state = session.query(CurrentState).filter(CurrentState.id == address).first()
    if state:
        deploy_tx = (
            session.query(Transactions)
            .filter(
                Transactions.to_address == address,
                Transactions.type == 1,
            )
            .order_by(Transactions.created_at.asc())
            .first()
        )
        if deploy_tx:
            # Return full contract detail inline
            contract_detail = get_state_with_transactions(session, address)
            return {
                "type": "CONTRACT",
                "address": address,
                **(contract_detail or {}),
            }

    # 2. Check if it's a validator
    validator = session.query(Validators).filter(Validators.address == address).first()
    if validator:
        return {
            "type": "VALIDATOR",
            "address": address,
            "validator": _serialize_validator(validator),
        }

    # 3. Check if there are any transactions involving this address
    tx_count = (
        session.query(func.count())
        .select_from(Transactions)
        .filter(
            or_(
                Transactions.from_address == address,
                Transactions.to_address == address,
            )
        )
        .scalar()
        or 0
    )
    if tx_count > 0:
        # Get the account's balance from CurrentState if it exists
        balance = state.balance if state else 0

        addr_filter = or_(
            Transactions.from_address == address,
            Transactions.to_address == address,
        )

        recent_txs = (
            session.query(Transactions)
            .options(*_HEAVY_TX_COLUMNS)
            .filter(addr_filter)
            .order_by(Transactions.created_at.desc())
            .limit(50)
            .all()
        )

        first_tx_time = (
            session.query(func.min(Transactions.created_at))
            .filter(addr_filter)
            .scalar()
        )
        last_tx_time = (
            session.query(func.max(Transactions.created_at))
            .filter(addr_filter)
            .scalar()
        )

        return {
            "type": "ACCOUNT",
            "address": address,
            "balance": balance,
            "tx_count": tx_count,
            "first_tx_time": first_tx_time.isoformat() if first_tx_time else None,
            "last_tx_time": last_tx_time.isoformat() if last_tx_time else None,
            "transactions": [
                _serialize_tx(tx, ) for tx in recent_txs
            ],
        }

    # Also check if it exists as a CurrentState entry without deploy tx (EOA with state)
    if state:
        return {
            "type": "ACCOUNT",
            "address": address,
            "balance": state.balance,
            "tx_count": 0,
            "first_tx_time": None,
            "last_tx_time": None,
            "transactions": [],
        }

    return None


def get_all_validators(
    session: Session,
    search: Optional[str] = None,
    limit: Optional[int] = None,
) -> dict:
    q = session.query(Validators).order_by(Validators.id)
    if search:
        like = f"%{search}%"
        q = q.filter(
            or_(
                Validators.address.ilike(like),
                Validators.provider.ilike(like),
                Validators.model.ilike(like),
            )
        )
    if limit:
        q = q.limit(limit)
    return {"validators": [_serialize_validator(v) for v in q.all()]}


def get_all_providers(session: Session) -> dict:
    providers = (
        session.query(LLMProviderDBModel)
        .order_by(LLMProviderDBModel.provider, LLMProviderDBModel.model)
        .all()
    )
    return {"providers": [_serialize_provider(p) for p in providers]}
