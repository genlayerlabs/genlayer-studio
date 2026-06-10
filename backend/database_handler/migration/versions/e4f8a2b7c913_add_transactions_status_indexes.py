"""add transactions status indexes

Revision ID: e4f8a2b7c913
Revises: c3d7f2a8b104
Create Date: 2026-03-17 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "e4f8a2b7c913"
down_revision: Union[str, None] = "c3d7f2a8b104"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "idx_transactions_status",
        "transactions",
        ["status"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_transactions_status_to_address",
        "transactions",
        ["status", "to_address"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("idx_transactions_status_to_address", table_name="transactions")
    op.drop_index("idx_transactions_status", table_name="transactions")
