"""add terminal snapshot archive candidate index

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-06-17 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The transactions table can be multi-TB in prod. Build the candidate
    # index concurrently so enabling the worker does not block writes.
    op.execute("COMMIT")
    op.execute(
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS
            idx_transactions_terminal_snapshot_archive_candidates
        ON transactions (created_at, hash)
        WHERE contract_snapshot IS NOT NULL
          AND status IN ('FINALIZED', 'CANCELED')
        """
    )


def downgrade() -> None:
    op.execute("COMMIT")
    op.execute(
        """
        DROP INDEX CONCURRENTLY IF EXISTS
            idx_transactions_terminal_snapshot_archive_candidates
        """
    )
