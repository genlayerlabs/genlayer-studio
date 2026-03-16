"""add execution_mode to transactions

Revision ID: a5f2c8e91d3b
Revises: 7ba71445758a
Create Date: 2025-01-23 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a5f2c8e91d3b"
down_revision: Union[str, None] = "7ba71445758a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add execution_mode column (nullable initially)
    op.add_column(
        "transactions",
        sa.Column("execution_mode", sa.String(length=30), nullable=True),
    )

    # 2. Migrate existing data:
    #    - leader_only=True  -> 'LEADER_ONLY' (no validation, immediate finalization)
    #    - leader_only=False -> 'NORMAL'
    op.execute(
        """
        UPDATE transactions
        SET execution_mode = CASE
            WHEN leader_only = true THEN 'LEADER_ONLY'
            ELSE 'NORMAL'
        END
        """
    )

    # 3. Make non-nullable with default
    op.alter_column(
        "transactions",
        "execution_mode",
        nullable=False,
        server_default="NORMAL",
    )


def downgrade() -> None:
    op.drop_column("transactions", "execution_mode")
