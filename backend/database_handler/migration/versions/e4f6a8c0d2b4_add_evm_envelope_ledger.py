"""add durable EVM envelope nonce and replay ledger

Revision ID: e4f6a8c0d2b4
Revises: d2e3f4a5b6c7
Create Date: 2026-08-28 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e4f6a8c0d2b4"
down_revision: Union[str, None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "evm_envelopes",
        sa.Column("hash", sa.String(length=66), nullable=False),
        sa.Column("from_address", sa.String(length=255), nullable=False),
        sa.Column("nonce", sa.Numeric(precision=78, scale=0), nullable=False),
        sa.Column("result", sa.String(length=66), nullable=False),
        sa.Column("to_address", sa.String(length=255), nullable=True),
        sa.Column(
            "success",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("error", sa.String(length=1024), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.CheckConstraint("nonce >= 0", name="evm_envelopes_nonce_unsigned"),
        sa.PrimaryKeyConstraint("hash", name="evm_envelopes_pkey"),
        sa.UniqueConstraint(
            "from_address",
            "nonce",
            name="evm_envelopes_from_address_nonce_key",
        ),
    )

    # Existing top-level transaction rows were admitted through signed EVM
    # envelopes. Preserve the highest observable nonce history while resolving
    # old Studio duplicates deterministically (latest row wins per nonce).
    op.execute(
        """
        INSERT INTO evm_envelopes
            (hash, from_address, nonce, result, success, created_at)
        SELECT DISTINCT ON (lower(from_address), nonce)
               hash, lower(from_address), nonce, hash, true, created_at
        FROM transactions
        WHERE triggered_by_hash IS NULL
          AND from_address IS NOT NULL
          AND nonce IS NOT NULL
          AND nonce >= 0
        ORDER BY lower(from_address), nonce, created_at DESC NULLS LAST, hash DESC
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("evm_envelopes")
