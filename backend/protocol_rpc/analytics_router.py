"""Analytics-only HTTP endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.protocol_rpc.client_ip import ClientIPResolver
from backend.protocol_rpc.dependencies import get_db_session
from backend.services.wallet_connection_analytics_service import (
    WalletConnectionMetadata,
    record_wallet_connection,
)

analytics_router = APIRouter(prefix="/api/analytics", tags=["analytics"])
_client_ip_resolver = ClientIPResolver()


class WalletConnectionRequest(BaseModel):
    wallet_address: str


class WalletConnectionResponse(BaseModel):
    wallet_address: str
    recorded: bool


@analytics_router.post(
    "/wallet-connections",
    response_model=WalletConnectionResponse,
    status_code=status.HTTP_200_OK,
)
def record_wallet_connection_endpoint(
    payload: WalletConnectionRequest,
    request: Request,
    session: Annotated[Session, Depends(get_db_session)],
):
    metadata = WalletConnectionMetadata(
        observed_ip=_client_ip_resolver.client_ip(request),
        user_agent=request.headers.get("User-Agent"),
        origin=request.headers.get("Origin"),
    )
    try:
        record = record_wallet_connection(
            session=session,
            wallet_address=payload.wallet_address,
            metadata=metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return WalletConnectionResponse(wallet_address=record.wallet_address, recorded=True)
