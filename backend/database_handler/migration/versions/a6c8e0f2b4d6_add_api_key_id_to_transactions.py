"""add api_key_id to transactions

Attributes a transaction to the API key of the request that submitted it;
triggered transactions inherit it from their parent.

Revision ID: a6c8e0f2b4d6
Revises: f5a7c9e1b3d5
Create Date: 2026-11-25 05:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a6c8e0f2b4d6"
down_revision: Union[str, None] = "f5a7c9e1b3d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable with no default: metadata-only, no table rewrite. No foreign
    # key either, which would scan the table to validate.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column(
        "transactions",
        sa.Column("api_key_id", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_column("transactions", "api_key_id")
