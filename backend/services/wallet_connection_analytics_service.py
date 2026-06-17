"""Analytics-only wallet connection recording."""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from backend.database_handler.models import WalletConnectionAnalytics

WALLET_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


@dataclass(frozen=True)
class WalletConnectionMetadata:
    observed_ip: Optional[str] = None
    user_agent: Optional[str] = None
    origin: Optional[str] = None


def normalize_wallet_address(wallet_address: str) -> str:
    value = wallet_address.strip()
    if not WALLET_ADDRESS_RE.fullmatch(value):
        raise ValueError("wallet_address must be a 0x-prefixed 20-byte hex address")
    return value.lower()


def record_wallet_connection(
    session: Session,
    wallet_address: str,
    metadata: WalletConnectionMetadata,
    connected_at: Optional[datetime.datetime] = None,
) -> WalletConnectionAnalytics:
    normalized_address = normalize_wallet_address(wallet_address)
    connected_at = connected_at or datetime.datetime.now(datetime.UTC)
    observed_ip = _truncate(metadata.observed_ip, 45)
    user_agent = _truncate(metadata.user_agent, 512)
    origin = _truncate(metadata.origin, 512)

    if session.bind is not None and session.bind.dialect.name == "postgresql":
        return _record_wallet_connection_postgres(
            session,
            normalized_address,
            observed_ip,
            user_agent,
            origin,
            connected_at,
        )

    return _record_wallet_connection_generic(
        session,
        normalized_address,
        observed_ip,
        user_agent,
        origin,
        connected_at,
    )


def _record_wallet_connection_postgres(
    session: Session,
    wallet_address: str,
    observed_ip: Optional[str],
    user_agent: Optional[str],
    origin: Optional[str],
    connected_at: datetime.datetime,
) -> WalletConnectionAnalytics:
    stmt = insert(WalletConnectionAnalytics).values(
        wallet_address=wallet_address,
        connect_count=1,
        first_observed_ip=observed_ip,
        last_observed_ip=observed_ip,
        first_user_agent=user_agent,
        last_user_agent=user_agent,
        first_origin=origin,
        last_origin=origin,
        first_connected_at=connected_at,
        last_connected_at=connected_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[WalletConnectionAnalytics.wallet_address],
        set_={
            "connect_count": WalletConnectionAnalytics.connect_count + 1,
            "last_observed_ip": observed_ip,
            "last_user_agent": user_agent,
            "last_origin": origin,
            "last_connected_at": connected_at,
        },
    ).returning(WalletConnectionAnalytics)
    return session.execute(stmt).scalar_one()


def _record_wallet_connection_generic(
    session: Session,
    wallet_address: str,
    observed_ip: Optional[str],
    user_agent: Optional[str],
    origin: Optional[str],
    connected_at: datetime.datetime,
) -> WalletConnectionAnalytics:
    existing = session.get(WalletConnectionAnalytics, wallet_address)
    if existing is not None:
        existing.connect_count += 1
        existing.last_observed_ip = observed_ip
        existing.last_user_agent = user_agent
        existing.last_origin = origin
        existing.last_connected_at = connected_at
        session.flush()
        return existing

    record = WalletConnectionAnalytics(
        wallet_address=wallet_address,
        connect_count=1,
        first_observed_ip=observed_ip,
        last_observed_ip=observed_ip,
        first_user_agent=user_agent,
        last_user_agent=user_agent,
        first_origin=origin,
        last_origin=origin,
    )
    record.first_connected_at = connected_at
    record.last_connected_at = connected_at
    session.add(record)
    session.flush()
    return record


def _truncate(value: Optional[str], max_length: int) -> Optional[str]:
    if value is None:
        return None
    return value[:max_length]
