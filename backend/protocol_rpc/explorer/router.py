"""FastAPI router for the explorer API."""

from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.protocol_rpc.dependencies import get_db_session

from . import queries

explorer_router = APIRouter(prefix="/api/explorer", tags=["explorer"])


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@explorer_router.get("/stats")
def get_stats(session: Annotated[Session, Depends(get_db_session)]):
    return queries.get_stats(session)


@explorer_router.get("/stats/counts")
def get_stats_counts(session: Annotated[Session, Depends(get_db_session)]):
    return queries.get_stats_counts(session)


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------


@explorer_router.get("/transactions")
def get_transactions(
    session: Annotated[Session, Depends(get_db_session)],
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
    search: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
):
    return queries.get_all_transactions_paginated(
        session, page, limit, status, search, from_date, to_date
    )


@explorer_router.get("/transactions/{tx_hash}")
def get_transaction(
    tx_hash: str,
    session: Annotated[Session, Depends(get_db_session)],
):
    result = queries.get_transaction_with_relations(session, tx_hash)
    if result is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return result


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


@explorer_router.get("/validators")
def get_validators(
    session: Annotated[Session, Depends(get_db_session)],
    search: Optional[str] = None,
    limit: Optional[int] = Query(None, ge=1, le=100),
):
    return queries.get_all_validators(session, search=search, limit=limit)


# ---------------------------------------------------------------------------
# Address (unified lookup)
# ---------------------------------------------------------------------------


@explorer_router.get("/address/{address}")
def get_address(
    address: str,
    session: Annotated[Session, Depends(get_db_session)],
):
    result = queries.get_address_info(session, address)
    if result is None:
        raise HTTPException(status_code=404, detail="Address not found")
    return result


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------


@explorer_router.get("/contracts")
def get_contracts(
    session: Annotated[Session, Depends(get_db_session)],
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    sort_by: Optional[Literal["tx_count", "created_at", "updated_at"]] = None,
    sort_order: Literal["asc", "desc"] = "desc",
):
    return queries.get_all_states(session, search, page, limit, sort_by, sort_order)


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


@explorer_router.get("/providers")
def get_providers(session: Annotated[Session, Depends(get_db_session)]):
    return queries.get_all_providers(session)
