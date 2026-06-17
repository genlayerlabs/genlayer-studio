import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database_handler.models import WalletConnectionAnalytics
from backend.services.wallet_connection_analytics_service import (
    WalletConnectionMetadata,
    normalize_wallet_address,
    record_wallet_connection,
)


def _same_timestamp(left: datetime.datetime, right: datetime.datetime) -> bool:
    return left.replace(tzinfo=None) == right.replace(tzinfo=None)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    WalletConnectionAnalytics.__table__.create(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    session = maker()
    yield session
    session.close()
    engine.dispose()


def test_normalize_wallet_address_lowercases_valid_address():
    assert (
        normalize_wallet_address("0xAABBcc0000000000000000000000000000000000")
        == "0xaabbcc0000000000000000000000000000000000"
    )


@pytest.mark.parametrize(
    "wallet_address",
    [
        "0x123",
        "1234567890123456789012345678901234567890",
        "0xzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",
    ],
)
def test_normalize_wallet_address_rejects_invalid_values(wallet_address):
    with pytest.raises(ValueError):
        normalize_wallet_address(wallet_address)


def test_record_wallet_connection_inserts_analytics_metadata(session):
    now = datetime.datetime(2026, 6, 17, 12, 0, tzinfo=datetime.UTC)

    record = record_wallet_connection(
        session,
        "0xAABBcc0000000000000000000000000000000000",
        WalletConnectionMetadata(
            observed_ip="198.51.100.7",
            user_agent="Mozilla/5.0",
            origin="https://studio.example.com",
        ),
        connected_at=now,
    )

    assert record.wallet_address == "0xaabbcc0000000000000000000000000000000000"
    assert record.connect_count == 1
    assert record.first_observed_ip == "198.51.100.7"
    assert record.last_observed_ip == "198.51.100.7"
    assert record.first_user_agent == "Mozilla/5.0"
    assert record.last_user_agent == "Mozilla/5.0"
    assert record.first_origin == "https://studio.example.com"
    assert record.last_origin == "https://studio.example.com"
    assert _same_timestamp(record.first_connected_at, now)
    assert _same_timestamp(record.last_connected_at, now)


def test_record_wallet_connection_updates_last_metadata_only(session):
    first = datetime.datetime(2026, 6, 17, 12, 0, tzinfo=datetime.UTC)
    second = datetime.datetime(2026, 6, 17, 13, 0, tzinfo=datetime.UTC)

    record_wallet_connection(
        session,
        "0xAABBcc0000000000000000000000000000000000",
        WalletConnectionMetadata(
            observed_ip="198.51.100.7",
            user_agent="First UA",
            origin="https://first.example.com",
        ),
        connected_at=first,
    )
    record = record_wallet_connection(
        session,
        "0xaabbcc0000000000000000000000000000000000",
        WalletConnectionMetadata(
            observed_ip="203.0.113.9",
            user_agent="Second UA",
            origin="https://second.example.com",
        ),
        connected_at=second,
    )

    assert record.connect_count == 2
    assert record.first_observed_ip == "198.51.100.7"
    assert record.last_observed_ip == "203.0.113.9"
    assert record.first_user_agent == "First UA"
    assert record.last_user_agent == "Second UA"
    assert record.first_origin == "https://first.example.com"
    assert record.last_origin == "https://second.example.com"
    assert _same_timestamp(record.first_connected_at, first)
    assert _same_timestamp(record.last_connected_at, second)


def test_record_wallet_connection_truncates_request_metadata(session):
    record = record_wallet_connection(
        session,
        "0xAABBcc0000000000000000000000000000000000",
        WalletConnectionMetadata(
            observed_ip="1" * 60,
            user_agent="u" * 600,
            origin="o" * 600,
        ),
    )

    assert len(record.first_observed_ip) == 45
    assert len(record.first_user_agent) == 512
    assert len(record.first_origin) == 512
