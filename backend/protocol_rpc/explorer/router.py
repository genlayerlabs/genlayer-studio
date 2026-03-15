"""FastAPI router for the explorer API."""

from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.database_handler.models import Transactions, TransactionStatus, Validators
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


class _UpdateStatusBody(BaseModel):
    status: str


@explorer_router.patch("/transactions/{tx_hash}")
def update_transaction(
    tx_hash: str,
    body: _UpdateStatusBody,
    session: Annotated[Session, Depends(get_db_session)],
):
    # Validate status enum
    try:
        new_status = TransactionStatus(body.status)
    except ValueError:
        valid = [s.value for s in TransactionStatus]
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of: {', '.join(valid)}",
        ) from None

    # Simple status update (matches current explorer PATCH behaviour)
    result = session.execute(
        text(
            "UPDATE transactions SET status = CAST(:new_status AS transaction_status) WHERE hash = :hash"
        ),
        {"hash": tx_hash, "new_status": new_status.value},
    )

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Transaction not found")

    session.flush()

    # Return the updated transaction
    tx = session.query(Transactions).filter(Transactions.hash == tx_hash).first()
    return {
        "success": True,
        "message": "Transaction status updated successfully",
        "transaction": queries._serialize_tx(tx),
    }


@explorer_router.delete("/transactions/{tx_hash}")
def delete_transaction(
    tx_hash: str,
    session: Annotated[Session, Depends(get_db_session)],
):
    result = queries.delete_transaction(session, tx_hash)
    if result is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return result


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


@explorer_router.get("/validators")
def get_validators(session: Annotated[Session, Depends(get_db_session)]):
    validators = session.query(Validators).order_by(Validators.id).all()
    return {
        "validators": [queries._serialize_validator(v) for v in validators],
    }


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@explorer_router.get("/state")
def get_states(
    session: Annotated[Session, Depends(get_db_session)],
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    sort_by: Optional[Literal["tx_count", "created_at", "updated_at"]] = None,
    sort_order: Literal["asc", "desc"] = "desc",
):
    return queries.get_all_states(session, search, page, limit, sort_by, sort_order)


@explorer_router.get("/state/{state_id}")
def get_state(
    state_id: str,
    session: Annotated[Session, Depends(get_db_session)],
):
    result = queries.get_state_with_transactions(session, state_id)
    if result is None:
        raise HTTPException(status_code=404, detail="State not found")
    return result


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


@explorer_router.get("/providers")
def get_providers(session: Annotated[Session, Depends(get_db_session)]):
    return queries.get_all_providers(session)
