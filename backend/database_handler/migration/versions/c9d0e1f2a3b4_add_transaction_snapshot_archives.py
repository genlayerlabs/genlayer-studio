"""add transaction snapshot archive index

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-06-17 12:05:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "transaction_snapshot_archives",
        sa.Column(
            "tx_hash",
            sa.String(length=66),
            sa.ForeignKey("transactions.hash", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("backend", sa.String(length=20), nullable=False),
        sa.Column("bucket", sa.String(length=255), nullable=True),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("uri", sa.String(length=2048), nullable=False),
        sa.Column(
            "format",
            sa.String(length=64),
            nullable=False,
            server_default="full-json-gzip-v1",
        ),
        sa.Column("snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("compressed_sha256", sa.String(length=64), nullable=False),
        sa.Column("snapshot_bytes", sa.BigInteger(), nullable=False),
        sa.Column("compressed_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "archive_status",
            sa.String(length=20),
            nullable=False,
            server_default="archived",
        ),
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("pruned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "object_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.CheckConstraint(
            "backend IN ('file', 'gcs', 's3')",
            name="transaction_snapshot_archives_backend_check",
        ),
        sa.CheckConstraint(
            "archive_status IN ('archived', 'pruned')",
            name="transaction_snapshot_archives_status_check",
        ),
    )
    op.create_index(
        "idx_transaction_snapshot_archives_status_archived_at",
        "transaction_snapshot_archives",
        ["archive_status", "archived_at"],
    )
    op.create_index(
        "idx_transaction_snapshot_archives_backend",
        "transaction_snapshot_archives",
        ["backend"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_transaction_snapshot_archives_backend",
        table_name="transaction_snapshot_archives",
    )
    op.drop_index(
        "idx_transaction_snapshot_archives_status_archived_at",
        table_name="transaction_snapshot_archives",
    )
    op.drop_table("transaction_snapshot_archives")
