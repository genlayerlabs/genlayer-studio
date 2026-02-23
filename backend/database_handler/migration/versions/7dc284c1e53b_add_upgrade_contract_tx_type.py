"""add_upgrade_contract_tx_type

Revision ID: 7dc284c1e53b
Revises: 96840ab9133a
Create Date: 2025-12-17

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "7dc284c1e53b"
down_revision: Union[str, None] = "96840ab9133a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add type=3 (UPGRADE_CONTRACT) to the transactions type constraint
    op.drop_constraint("transactions_type_check", "transactions", type_="check")
    op.create_check_constraint(
        "transactions_type_check",
        "transactions",
        "type = ANY (ARRAY[0, 1, 2, 3])",
    )


def downgrade() -> None:
    # Revert to original constraint (type 0, 1, 2 only)
    op.drop_constraint("transactions_type_check", "transactions", type_="check")
    op.create_check_constraint(
        "transactions_type_check",
        "transactions",
        "type = ANY (ARRAY[0, 1, 2])",
    )
