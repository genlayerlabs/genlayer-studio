"""add value_credited column to transactions

Revision ID: a1b2c3d4e5f6
Revises: f7a1b3c5d9e2
Create Date: 2026-03-25 14:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f7a1b3c5d9e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column(
            "value_credited",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
    )
    # Backfill: mark historical valued transactions that already processed as credited
    op.execute(
        "UPDATE transactions SET value_credited = true "
        "WHERE value > 0 AND status NOT IN ('PENDING', 'ACTIVATED')"
    )


def downgrade() -> None:
    op.drop_column("transactions", "value_credited")
