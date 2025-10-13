import os
from typing import Iterable

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from backend.database_handler.models import Base
from backend.database_handler.transactions_processor import TransactionsProcessor

# import debugpy


@pytest.fixture
def engine() -> Iterable[Engine]:
    postgres_url = os.getenv("POSTGRES_URL")
    engine = create_engine(
        postgres_url,
        pool_pre_ping=True,  # Test connections before using them
        pool_recycle=3600,  # Recycle connections after 1 hour
        # echo=True # Uncomment this line to see the SQL queries
    )

    # Kill any existing connections to avoid locks
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

    # Clean up: truncate instead of drop to avoid lock issues
    with engine.connect() as conn:
        # Disable foreign key checks temporarily
        conn.execute(text("SET session_replication_role = 'replica';"))

        # Get all tables except alembic_version
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

        # Truncate all tables and reset identity
        for table in tables:
            conn.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE;"))

        # Reset the snapshot_id_seq sequence specifically
        conn.execute(text("ALTER SEQUENCE IF EXISTS snapshot_id_seq RESTART WITH 1;"))

        # Re-enable foreign key checks
        conn.execute(text("SET session_replication_role = 'origin';"))
        conn.commit()

    engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterable[Session]:
    session_maker = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_maker()
    yield session
    session.rollback()  # Rollback any uncommitted changes
    session.close()

    # Clear the session to avoid conflicts
    session.expunge_all()


@pytest.fixture
def transactions_processor(session: Session) -> Iterable[TransactionsProcessor]:
    yield TransactionsProcessor(session)
