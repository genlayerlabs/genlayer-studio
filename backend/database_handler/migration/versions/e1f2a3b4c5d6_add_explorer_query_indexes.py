"""add explorer query indexes concurrently

Revision ID: e1f2a3b4c5d6
Revises: d4e5f6a7b8c9
Create Date: 2026-04-16 16:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # CREATE INDEX CONCURRENTLY cannot run inside a transaction block.
    # Each index gets its own autocommit block so the builds run without
    # blocking concurrent writes — critical for the transactions table
    # (consensus workers update it continuously).

    # Index for GROUP BY status (used by /api/explorer/stats and /stats/counts)
    # Fixes GENLAYER-STUDIO-1SN (1259 events)
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_transactions_status
            ON transactions (status)
            """
        )

    # Index for GROUP BY triggered_by_hash (used by /api/explorer/transactions batch fetch)
    # Fixes GENLAYER-STUDIO-1W8 (25 events)
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_transactions_triggered_by_hash
            ON transactions (triggered_by_hash)
            WHERE triggered_by_hash IS NOT NULL
            """
        )

    # Index for WHERE type = 1 count (deploy count in /api/explorer/stats)
    # Part of GENLAYER-STUDIO-102 (196 events)
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_transactions_type
            ON transactions (type)
            """
        )

    # Index for WHERE appealed = true count
    # Part of GENLAYER-STUDIO-102 (196 events)
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_transactions_appealed
            ON transactions (appealed)
            WHERE appealed = true
            """
        )


def downgrade() -> None:
    # DROP INDEX CONCURRENTLY for symmetry (no write lock on downgrade)
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_transactions_appealed")
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_transactions_type")
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS idx_transactions_triggered_by_hash"
        )
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_transactions_status")
