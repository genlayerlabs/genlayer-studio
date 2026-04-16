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
    # Exit the implicit transaction so CREATE INDEX CONCURRENTLY can run.
    # CONCURRENTLY builds indexes without blocking concurrent writes,
    # which is critical for the transactions table (consensus workers
    # update it continuously).
    connection = op.get_bind()
    connection.execution_options(isolation_level="AUTOCOMMIT")

    # Index for GROUP BY status (used by /api/explorer/stats and /stats/counts)
    # Fixes GENLAYER-STUDIO-1SN (1259 events)
    op.execute(
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_transactions_status
        ON transactions (status)
        """
    )

    # Index for GROUP BY triggered_by_hash (used by /api/explorer/transactions batch fetch)
    # Fixes GENLAYER-STUDIO-1W8 (25 events)
    op.execute(
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_transactions_triggered_by_hash
        ON transactions (triggered_by_hash)
        WHERE triggered_by_hash IS NOT NULL
        """
    )

    # Index for WHERE type = 1 count (deploy count in /api/explorer/stats)
    # Part of GENLAYER-STUDIO-102 (196 events)
    op.execute(
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_transactions_type
        ON transactions (type)
        """
    )

    # Index for WHERE appealed = true count
    # Part of GENLAYER-STUDIO-102 (196 events)
    op.execute(
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_transactions_appealed
        ON transactions (appealed)
        WHERE appealed = true
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_transactions_appealed")
    op.execute("DROP INDEX IF EXISTS idx_transactions_type")
    op.execute("DROP INDEX IF EXISTS idx_transactions_triggered_by_hash")
    op.execute("DROP INDEX IF EXISTS idx_transactions_status")
