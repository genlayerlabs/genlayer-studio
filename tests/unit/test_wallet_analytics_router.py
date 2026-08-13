from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.datastructures import Headers

from backend.database_handler.models import WalletConnectionAnalytics
from backend.protocol_rpc.analytics_router import (
    WalletConnectionRequest,
    record_wallet_connection_endpoint,
)


def _make_sqlite_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    WalletConnectionAnalytics.__table__.create(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    session = maker()
    return engine, session


def _make_request(headers: dict[str, str] | None = None, client_host="127.0.0.1"):
    return SimpleNamespace(
        headers=Headers(headers or {}),
        client=SimpleNamespace(host=client_host),
    )


def test_wallet_connection_endpoint_records_server_observed_metadata():
    engine, session = _make_sqlite_session()

    try:
        response = record_wallet_connection_endpoint(
            WalletConnectionRequest(
                wallet_address="0xAABBcc0000000000000000000000000000000000"
            ),
            _make_request(
                headers={
                    "Origin": "https://studio.example.com",
                    "User-Agent": "UnitTest/1.0",
                    "X-Forwarded-For": "198.51.100.7",
                }
            ),
            session,
        )
        session.commit()

        assert response.wallet_address == "0xaabbcc0000000000000000000000000000000000"
        assert response.recorded is True

        record = session.get(
            WalletConnectionAnalytics,
            "0xaabbcc0000000000000000000000000000000000",
        )
        assert record is not None
        assert record.first_observed_ip == "198.51.100.7"
        assert record.last_observed_ip == "198.51.100.7"
        assert record.first_origin == "https://studio.example.com"
        assert record.last_origin == "https://studio.example.com"
        assert record.first_user_agent == "UnitTest/1.0"
        assert record.last_user_agent == "UnitTest/1.0"
    finally:
        session.close()
        engine.dispose()


def test_wallet_connection_endpoint_rejects_invalid_wallet_address():
    engine, session = _make_sqlite_session()

    try:
        with pytest.raises(HTTPException) as exc_info:
            record_wallet_connection_endpoint(
                WalletConnectionRequest(wallet_address="0x123"),
                _make_request(),
                session,
            )

        assert exc_info.value.status_code == 422
        assert "wallet_address" in exc_info.value.detail
    finally:
        session.close()
        engine.dispose()
