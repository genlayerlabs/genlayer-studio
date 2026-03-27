"""upgrade balance and value columns to bigint for wei-scale amounts

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-27 16:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "current_state",
        "balance",
        type_=sa.BigInteger(),
        existing_type=sa.Integer(),
    )
    op.alter_column(
        "transactions",
        "value",
        type_=sa.BigInteger(),
        existing_type=sa.Integer(),
    )


def downgrade() -> None:
    op.alter_column(
        "transactions",
        "value",
        type_=sa.Integer(),
        existing_type=sa.BigInteger(),
    )
    op.alter_column(
        "current_state",
        "balance",
        type_=sa.Integer(),
        existing_type=sa.BigInteger(),
    )
