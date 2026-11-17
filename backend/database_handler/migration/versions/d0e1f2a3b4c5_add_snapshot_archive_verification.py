"""add snapshot archive verification marker

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-06-24 15:55:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:  # pragma: no cover
    op.add_column(
        "transaction_snapshot_archives",
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_transaction_snapshot_archives_verify_queue",
        "transaction_snapshot_archives",
        ["archive_status", "verified_at", "archived_at"],
    )
    op.create_index(
        "idx_transaction_snapshot_archives_prune_queue",
        "transaction_snapshot_archives",
        ["archive_status", "verified_at"],
    )


def downgrade() -> None:  # pragma: no cover
    op.drop_index(
        "idx_transaction_snapshot_archives_prune_queue",
        table_name="transaction_snapshot_archives",
    )
    op.drop_index(
        "idx_transaction_snapshot_archives_verify_queue",
        table_name="transaction_snapshot_archives",
    )
    op.drop_column("transaction_snapshot_archives", "verified_at")
