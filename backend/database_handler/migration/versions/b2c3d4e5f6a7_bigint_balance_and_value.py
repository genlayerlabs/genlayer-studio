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


def _column_is_numeric(table: str, column: str) -> bool:
    """Check if a column is already NUMERIC (idempotent guard)."""
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = :table AND column_name = :col"
        ),
        {"table": table, "col": column},
    ).scalar()
    return result == "numeric"


def upgrade() -> None:
    if not _column_is_numeric("current_state", "balance"):
        op.alter_column(
            "current_state",
            "balance",
            type_=sa.Numeric(precision=78, scale=0),
            existing_type=sa.Integer(),
        )
    if not _column_is_numeric("transactions", "value"):
        op.alter_column(
            "transactions",
            "value",
            type_=sa.Numeric(precision=78, scale=0),
            existing_type=sa.Integer(),
        )


def downgrade() -> None:
    op.alter_column(
        "transactions",
        "value",
        type_=sa.Integer(),
        existing_type=sa.Numeric(precision=78, scale=0),
    )
    op.alter_column(
        "current_state",
        "balance",
        type_=sa.Integer(),
        existing_type=sa.Numeric(precision=78, scale=0),
    )
