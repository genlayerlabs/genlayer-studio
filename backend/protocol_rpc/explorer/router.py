"""FastAPI router for the explorer API."""

from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from . import queries
from .query_runner import ExplorerQueryRunner

explorer_router = APIRouter(prefix="/api/explorer", tags=["explorer"])


def get_query_runner(request: Request) -> ExplorerQueryRunner:
    runner = getattr(request.app.state, "explorer_query_runner", None)
    if runner is None:
        raise HTTPException(status_code=503, detail="Explorer not initialized")
    return runner


QueryRunner = Annotated[ExplorerQueryRunner, Depends(get_query_runner)]


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@explorer_router.get("/stats")
def get_stats(runner: QueryRunner):
    return runner.run(queries.get_stats)


@explorer_router.get("/stats/counts")
def get_stats_counts(runner: QueryRunner):
    return runner.counts()


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------


@explorer_router.get("/transactions")
def get_transactions(
    runner: QueryRunner,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
    search: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    address: Optional[str] = None,
):
    return runner.run(
        queries.get_all_transactions_paginated,
        page,
        limit,
        status,
        search,
        from_date,
        to_date,
        address,
    )


@explorer_router.get("/transactions/{tx_hash}")
def get_transaction(
    tx_hash: str,
    runner: QueryRunner,
):
    result = runner.run(queries.get_transaction_with_relations, tx_hash)
    if result is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return result


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


@explorer_router.get("/validators")
def get_validators(
    runner: QueryRunner,
    search: Optional[str] = None,
    limit: Optional[int] = Query(None, ge=1, le=100),
):
    return runner.run(queries.get_all_validators, search=search, limit=limit)


# ---------------------------------------------------------------------------
# Address (unified lookup)
# ---------------------------------------------------------------------------


@explorer_router.get("/address/{address}")
def get_address(
    address: str,
    runner: QueryRunner,
):
    result = runner.run(queries.get_address_info, address)
    if result is None:
        raise HTTPException(status_code=404, detail="Address not found")
    return result


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------


@explorer_router.get("/contracts")
def get_contracts(
    runner: QueryRunner,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    sort_by: Optional[Literal["tx_count", "created_at", "updated_at"]] = None,
    sort_order: Literal["asc", "desc"] = "desc",
):
    return runner.run(queries.get_all_states, search, page, limit, sort_by, sort_order)


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


@explorer_router.get("/providers")
def get_providers(runner: QueryRunner):
    return runner.run(queries.get_all_providers)
