"""add claim query partial indexes and explorer indexes

Revision ID: f7a1b3c5d9e2
Revises: e4f8a2b7c913
Create Date: 2026-03-17 16:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f7a1b3c5d9e2"
down_revision: Union[str, None] = "e4f8a2b7c913"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        # Partial index for claim_next_finalization:
        # Covers the WHERE clause filtering on status + appealed + timestamp_awaiting_finalization
        # Orders by created_at to support the ORDER BY in the query
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_transactions_pending_finalization
            ON transactions (created_at)
            WHERE status IN ('ACCEPTED', 'UNDETERMINED', 'LEADER_TIMEOUT', 'VALIDATORS_TIMEOUT')
              AND appealed = false
              AND timestamp_awaiting_finalization IS NOT NULL
            """
        )

        # Partial index for claim_next_appeal:
        # Covers WHERE appealed = true AND status IN (...)
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_transactions_pending_appeal
            ON transactions (created_at)
            WHERE appealed = true
              AND status IN ('ACCEPTED', 'UNDETERMINED', 'LEADER_TIMEOUT', 'VALIDATORS_TIMEOUT')
            """
        )

        # Partial index for claim_next_transaction:
        # Covers WHERE status IN ('PENDING', 'ACTIVATED'), ordered by type priority then created_at
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_transactions_pending_claim
            ON transactions (type, created_at)
            WHERE status IN ('PENDING', 'ACTIVATED')
            """
        )

        # Index for NOT EXISTS subquery used in all three claim queries:
        # Checks (to_address, blocked_at) for active blocks by other transactions
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_transactions_active_block
            ON transactions (to_address, blocked_at)
            WHERE blocked_at IS NOT NULL
            """
        )

        # Index for explorer queries that filter by to_address (e.g. get_state_with_transactions)
        # Also supports the NOT EXISTS subqueries in claim queries
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_transactions_to_address
            ON transactions (to_address, created_at DESC)
            """
        )

        # Index for explorer queries that filter by from_address (e.g. COUNT(DISTINCT from_address))
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_transactions_from_address
            ON transactions (from_address)
            """
        )

        # Index for created_at range queries (e.g. tx_last_24h, volume charts, recent transactions)
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_transactions_created_at
            ON transactions (created_at DESC)
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_transactions_created_at")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_transactions_from_address")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_transactions_to_address")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_transactions_active_block")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_transactions_pending_claim")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_transactions_pending_appeal")
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS idx_transactions_pending_finalization"
        )
