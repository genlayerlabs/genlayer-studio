"""add_triggered_on_to_transactions

Revision ID: 7ba71445758a
Revises: 7dc284c1e53b
Create Date: 2026-01-12

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "7ba71445758a"
down_revision: Union[str, None] = "7dc284c1e53b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("triggered_on", sa.String(20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transactions", "triggered_on")
