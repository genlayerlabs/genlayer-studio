"""add partial index for per-API-key transaction queries

Revision ID: b7d9f1a3c5e7
Revises: a6c8e0f2b4d6
Create Date: 2026-11-25 05:05:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b7d9f1a3c5e7"
down_revision: Union[str, None] = "a6c8e0f2b4d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = "idx_transactions_api_key_created_at"


def upgrade() -> None:
    # CONCURRENTLY cannot run inside Alembic's implicit transaction.
    op.execute("COMMIT")
    # A build that failed midway leaves an INVALID index that IF NOT EXISTS
    # would keep, so drop any leftover before building.
    op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")
    op.execute(
        f"""
        CREATE INDEX CONCURRENTLY {INDEX_NAME}
        ON transactions (api_key_id, created_at)
        WHERE api_key_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("COMMIT")
    op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")
