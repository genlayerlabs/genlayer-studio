"""add origin_address to transactions

Revision ID: a1b2c3d4e5f6
Revises: f7a1b3c5d9e2
Create Date: 2026-04-07 12:00:00.000000

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
        sa.Column("origin_address", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transactions", "origin_address")
