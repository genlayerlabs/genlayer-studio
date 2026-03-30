"""Fixtures for consensus-level tests.

These tests exercise the consensus state machine with a real PostgreSQL
database but mock GenVM execution. This tests the balance accounting,
appeal flows, and message emission logic without needing validators or LLMs.
"""

import os
from typing import Iterable

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from backend.database_handler.models import Base
from backend.database_handler.transactions_processor import TransactionsProcessor
from backend.database_handler.accounts_manager import AccountsManager
from backend.database_handler.contract_processor import ContractProcessor


@pytest.fixture
def engine() -> Iterable[Engine]:
    postgres_url = os.getenv(
        "POSTGRES_URL", "postgresql://postgres:postgres@localhost:5432/genlayer_test"
    )
    engine = create_engine(postgres_url, pool_pre_ping=True, pool_recycle=3600)

    with engine.connect() as conn:
        conn.execute(
            text(
                """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = current_database()
            AND pid <> pg_backend_pid()
        """
            )
        )
        conn.commit()

    Base.metadata.create_all(engine)
    yield engine

    with engine.connect() as conn:
        conn.execute(text("SET session_replication_role = 'replica';"))
        result = conn.execute(
            text(
                """
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public'
            AND tablename != 'alembic_version'
        """
            )
        )
        tables = [row[0] for row in result]
        for table in tables:
            conn.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE;"))
        conn.execute(text("SET session_replication_role = 'origin';"))
        conn.commit()

    engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterable[Session]:
    session_maker = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_maker()
    yield session
    session.rollback()
    session.close()
    session.expunge_all()


@pytest.fixture
def transactions_processor(session: Session) -> TransactionsProcessor:
    return TransactionsProcessor(session)


@pytest.fixture
def accounts_manager(session: Session) -> AccountsManager:
    return AccountsManager(session)


@pytest.fixture
def contract_processor(session: Session) -> ContractProcessor:
    return ContractProcessor(session)
