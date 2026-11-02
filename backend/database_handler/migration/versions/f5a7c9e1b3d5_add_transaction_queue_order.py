"""add durable transaction queue issuance order

Revision ID: f5a7c9e1b3d5
Revises: e4f6a8c0d2b4
Create Date: 2026-08-28 15:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "f5a7c9e1b3d5"
down_revision: Union[str, None] = "e4f6a8c0d2b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE transactions_queue_order_seq")
    op.add_column(
        "transactions",
        sa.Column("queue_order", sa.BigInteger(), nullable=True),
    )
    # Preserve the best order available for historical rows. New rows receive
    # their sequence value at INSERT, after the recipient admission lock.
    op.execute(
        """
        WITH ordered AS (
            SELECT hash,
                   ROW_NUMBER() OVER (
                       ORDER BY created_at ASC,
                                nonce ASC NULLS LAST,
                                hash ASC
                   ) AS queue_order
            FROM transactions
        )
        UPDATE transactions AS t
        SET queue_order = ordered.queue_order
        FROM ordered
        WHERE t.hash = ordered.hash
        """
    )
    op.execute(
        """
        SELECT setval(
            'transactions_queue_order_seq',
            COALESCE((SELECT MAX(queue_order) + 1 FROM transactions), 1),
            false
        )
        """
    )
    op.alter_column(
        "transactions",
        "queue_order",
        existing_type=sa.BigInteger(),
        nullable=False,
        server_default=sa.text("nextval('transactions_queue_order_seq'::regclass)"),
    )
    op.create_index(
        "ix_transactions_to_address_queue_order",
        "transactions",
        ["to_address", "queue_order"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_transactions_to_address_queue_order",
        table_name="transactions",
    )
    op.drop_column("transactions", "queue_order")
    op.execute("DROP SEQUENCE transactions_queue_order_seq")
