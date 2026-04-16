import os
from typing import Iterable
from urllib.parse import urlparse

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from backend.database_handler.transactions_processor import TransactionsProcessor


def _alembic_config(postgres_url: str) -> Config:
    """Build an Alembic Config pointing at the real backend/alembic.ini.

    env.py reads DBUSER/DBPASSWORD/DBHOST/DBPORT/DBNAME from the environment,
    so parse the test Postgres URL and set those so Alembic connects to the
    same database the tests use.
    """
    parsed = urlparse(postgres_url)
    os.environ["DBUSER"] = parsed.username or "postgres"
    os.environ["DBPASSWORD"] = parsed.password or ""
    os.environ["DBHOST"] = parsed.hostname or "localhost"
    os.environ["DBPORT"] = str(parsed.port or 5432)
    db_name = (parsed.path or "/postgres").lstrip("/") or "postgres"
    os.environ["DBNAME"] = db_name

    # Walk up from this file looking for the backend/database_handler/alembic.ini.
    # Local layout: tests/db-sqlalchemy/conftest.py → ../../backend/database_handler.
    # Container layout (docker/db-sqlalchemy/Dockerfile): /app/conftest.py →
    # /app/backend/database_handler. Both work with the same search.
    search_dir = os.path.dirname(os.path.abspath(__file__))
    ini_path = None
    for _ in range(6):
        candidate = os.path.join(
            search_dir, "backend", "database_handler", "alembic.ini"
        )
        if os.path.isfile(candidate):
            ini_path = candidate
            break
        parent = os.path.dirname(search_dir)
        if parent == search_dir:
            break
        search_dir = parent
    if ini_path is None:
        raise RuntimeError(
            "Could not locate backend/database_handler/alembic.ini relative "
            f"to {__file__}"
        )
    cfg = Config(ini_path)
    # script_location in alembic.ini is relative to the ini's dir; make absolute
    cfg.set_main_option(
        "script_location",
        os.path.abspath(os.path.join(os.path.dirname(ini_path), "migration")),
    )
    return cfg


@pytest.fixture(scope="session")
def migrated_engine() -> Iterable[Engine]:
    """Session-wide engine whose schema is built via `alembic upgrade head`.

    Runs the real migration chain against the test database — the same thing
    production does at deploy time. Any migration bug (bad SQL, wrong Alembic
    API, broken down_revision chain) fails the test suite instead of
    surfacing at deploy.
    """
    postgres_url = os.environ["POSTGRES_URL"]
    engine = create_engine(
        postgres_url,
        pool_pre_ping=True,
        pool_recycle=3600,
    )

    with engine.connect() as conn:
        conn.execute(
            text(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = current_database() AND pid <> pg_backend_pid()
                """
            )
        )
        conn.commit()

    # Start from a clean schema, then migrate end-to-end.
    with engine.connect() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.commit()

    command.upgrade(_alembic_config(postgres_url), "head")

    yield engine

    engine.dispose()


@pytest.fixture
def engine(migrated_engine: Engine) -> Iterable[Engine]:
    """Per-test view of the migrated engine. Truncates data between tests
    but keeps the migrated schema + alembic_version intact."""
    yield migrated_engine

    with migrated_engine.connect() as conn:
        conn.execute(text("SET session_replication_role = 'replica';"))
        result = conn.execute(
            text(
                """
                SELECT tablename FROM pg_tables
                WHERE schemaname = 'public' AND tablename != 'alembic_version'
                """
            )
        )
        tables = [row[0] for row in result]
        for table in tables:
            conn.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE;"))
        conn.execute(text("ALTER SEQUENCE IF EXISTS snapshot_id_seq RESTART WITH 1;"))
        conn.execute(text("SET session_replication_role = 'origin';"))
        conn.commit()


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


@pytest.fixture
def tp(transactions_processor: TransactionsProcessor) -> TransactionsProcessor:
    """Short alias for transactions_processor."""
    return transactions_processor
