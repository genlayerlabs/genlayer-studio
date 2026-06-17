from collections.abc import Generator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database_handler.models import WalletConnectionAnalytics
from backend.protocol_rpc.analytics_router import analytics_router
from backend.protocol_rpc.dependencies import get_db_session


def test_wallet_connection_endpoint_records_server_observed_metadata():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    WalletConnectionAnalytics.__table__.create(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)

    app = FastAPI()

    def override_session() -> Generator[Session, None, None]:
        session = maker()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = override_session
    app.include_router(analytics_router)

    client = TestClient(app)
    response = client.post(
        "/api/analytics/wallet-connections",
        json={"wallet_address": "0xAABBcc0000000000000000000000000000000000"},
        headers={
            "Origin": "https://studio.example.com",
            "User-Agent": "UnitTest/1.0",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "wallet_address": "0xaabbcc0000000000000000000000000000000000",
        "recorded": True,
    }

    session = maker()
    try:
        record = session.get(
            WalletConnectionAnalytics,
            "0xaabbcc0000000000000000000000000000000000",
        )
        assert record is not None
        assert record.first_origin == "https://studio.example.com"
        assert record.last_origin == "https://studio.example.com"
        assert record.first_user_agent == "UnitTest/1.0"
        assert record.last_user_agent == "UnitTest/1.0"
    finally:
        session.close()
        engine.dispose()


def test_wallet_connection_endpoint_rejects_invalid_wallet_address():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    WalletConnectionAnalytics.__table__.create(engine)
    maker = sessionmaker(bind=engine)

    app = FastAPI()

    def override_session() -> Generator[Session, None, None]:
        session = maker()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = override_session
    app.include_router(analytics_router)

    client = TestClient(app)
    response = client.post(
        "/api/analytics/wallet-connections",
        json={"wallet_address": "0x123"},
    )

    assert response.status_code == 422
    assert "wallet_address" in response.json()["detail"]
    engine.dispose()
