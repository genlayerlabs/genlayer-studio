"""Migration-level integrity tests.

The per-session `migrated_engine` fixture in conftest.py runs
`alembic upgrade head` against the test database. That on its own would
have caught the CONCURRENTLY-inside-transaction bug that slipped to prd
on 2026-04-16.

These tests add two explicit checks on top:
- single migration head (no accidental branches from merge conflicts)
- no schema drift between Base.metadata (ORM models) and the migrated schema
"""

import os

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

from backend.database_handler.models import Base
from conftest import _alembic_config


def test_migrations_have_single_head(migrated_engine: Engine):
    """Alembic migration chain must have exactly one head.

    Multiple heads appear when two branches both add a migration pointing at
    the same down_revision (typical merge-conflict outcome). Prd can't apply
    such a chain without a manual `alembic merge`.
    """
    cfg = _alembic_config(os.environ["POSTGRES_URL"])
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1, (
        f"Migration chain has {len(heads)} heads: {heads}. "
        "Run `alembic merge` to reconcile."
    )


def _is_benign_drift(diff_entry) -> bool:
    """Filter out drift entries that are expected and safe.

    Migrations routinely add performance indexes that aren't declared on the
    ORM models (partial indexes for claim queries, hot-path indexes for the
    explorer API, etc.). Those are intentional, not drift.
    """
    if not isinstance(diff_entry, tuple):
        return False
    action = diff_entry[0]
    return action in ("add_index", "remove_index")


def test_no_schema_drift_between_orm_and_migrations(migrated_engine: Engine):
    """After running every migration, the database schema must match
    `Base.metadata` (the ORM models).

    If this fails, models.py has diverged from the migration chain. Either
    a new migration was missed, or the models were edited without a
    corresponding migration. Both cases would quietly ship a broken schema
    to prd — this test catches it in CI.
    """
    with migrated_engine.connect() as conn:
        mc = MigrationContext.configure(conn)
        diff = compare_metadata(mc, Base.metadata)

    significant = [d for d in diff if not _is_benign_drift(d)]
    if significant:
        pytest.fail(
            "Schema drift between models.py and migrations:\n  - "
            + "\n  - ".join(str(d) for d in significant)
            + "\n\nEither generate a new migration (`alembic revision "
            "--autogenerate`) or update models.py to match the intended "
            "schema."
        )
