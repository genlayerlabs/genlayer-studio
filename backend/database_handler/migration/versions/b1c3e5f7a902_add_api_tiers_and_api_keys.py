"""add api_tiers and api_keys tables

Revision ID: b1c3e5f7a902
Revises: a5f2c8e91d3b
Create Date: 2026-02-16 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b1c3e5f7a902"
down_revision: Union[str, None] = "a5f2c8e91d3b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_tiers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("rate_limit_minute", sa.Integer(), nullable=False),
        sa.Column("rate_limit_hour", sa.Integer(), nullable=False),
        sa.Column("rate_limit_day", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id", name="api_tiers_pkey"),
        sa.UniqueConstraint("name", name="api_tiers_name_key"),
    )

    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key_prefix", sa.String(length=8), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("tier_id", sa.Integer(), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tier_id"], ["api_tiers.id"]),
        sa.PrimaryKeyConstraint("id", name="api_keys_pkey"),
        sa.UniqueConstraint("key_hash", name="api_keys_key_hash_key"),
    )

    # Seed default tiers
    op.execute(
        """
        INSERT INTO api_tiers (name, rate_limit_minute, rate_limit_hour, rate_limit_day)
        VALUES
            ('free', 30, 500, 5000),
            ('pro', 120, 3000, 50000),
            ('unlimited', 999999, 999999, 999999)
        """
    )


def downgrade() -> None:
    op.drop_table("api_keys")
    op.drop_table("api_tiers")
