"""wallet connection analytics

Revision ID: 8c2f0f0c9f1a
Revises: a7b8c9d0e1f2
Create Date: 2026-06-17 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "8c2f0f0c9f1a"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "wallet_connection_analytics",
        sa.Column("wallet_address", sa.String(length=42), nullable=False),
        sa.Column(
            "connect_count",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
        sa.Column("first_observed_ip", sa.String(length=45), nullable=True),
        sa.Column("last_observed_ip", sa.String(length=45), nullable=True),
        sa.Column("first_user_agent", sa.String(length=512), nullable=True),
        sa.Column("last_user_agent", sa.String(length=512), nullable=True),
        sa.Column("first_origin", sa.String(length=512), nullable=True),
        sa.Column("last_origin", sa.String(length=512), nullable=True),
        sa.Column(
            "first_connected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "last_connected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "wallet_address", name="wallet_connection_analytics_pkey"
        ),
    )


def downgrade() -> None:
    op.drop_table("wallet_connection_analytics")
