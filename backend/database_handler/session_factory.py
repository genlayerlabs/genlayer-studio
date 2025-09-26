"""Lightweight session manager used by the FastAPI stack."""

from __future__ import annotations

import os
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


class DatabaseSessionManager:
    """Minimal helper to create request-scoped SQLAlchemy sessions."""

    def __init__(self, database_url: str, **engine_kwargs):
        pool_size = int(
            engine_kwargs.pop("pool_size", os.environ.get("DATABASE_POOL_SIZE", 10))
        )
        max_overflow = int(
            engine_kwargs.pop(
                "max_overflow", os.environ.get("DATABASE_MAX_OVERFLOW", 10)
            )
        )

        default_kwargs = dict(
            pool_pre_ping=True,
            pool_recycle=3600,
            pool_timeout=30,
            pool_size=pool_size,
            max_overflow=max_overflow,
        )
        default_kwargs.update(engine_kwargs)

        self.engine: Engine = create_engine(database_url, **default_kwargs)
        self.SessionLocal = sessionmaker(
            bind=self.engine,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )

    def open_session(self) -> Session:
        return self.SessionLocal()


_db_manager: Optional[DatabaseSessionManager] = None


def init_database_manager(database_url: str, **engine_kwargs) -> DatabaseSessionManager:
    global _db_manager
    _db_manager = DatabaseSessionManager(database_url, **engine_kwargs)
    return _db_manager


def get_database_manager() -> DatabaseSessionManager:
    if _db_manager is None:
        raise RuntimeError("DatabaseSessionManager has not been initialised")
    return _db_manager


def set_database_manager(manager: DatabaseSessionManager) -> None:
    """Override the global database manager reference."""
    global _db_manager
    _db_manager = manager
