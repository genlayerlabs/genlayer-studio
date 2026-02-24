"""add snapshot table

Revision ID: 99015c5b5b78
Revises: 6fd3e2cea05b
Create Date: 2025-04-29 02:20:50.368584

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "99015c5b5b78"
down_revision: Union[str, None] = "6fd3e2cea05b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create sequence first
    op.execute(
        """
        CREATE SEQUENCE snapshot_id_seq
        START WITH 1
        INCREMENT BY 1
        NO MINVALUE
        NO MAXVALUE
        CACHE 1;
    """
    )

    # Create table with sequence as default
    op.create_table(
        "snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "snapshot_id",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("nextval('snapshot_id_seq')"),
        ),
        sa.Column(
            "state_data", sa.LargeBinary(), nullable=False
        ),  # Stores compressed state data
        sa.Column(
            "transaction_data", sa.LargeBinary(), nullable=False
        ),  # Stores compressed transaction data
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="snapshots_pkey"),
        sa.UniqueConstraint("snapshot_id", name="snapshots_snapshot_id_key"),
    )

    # Set sequence ownership
    op.execute("ALTER SEQUENCE snapshot_id_seq OWNED BY snapshots.snapshot_id")


def downgrade() -> None:
    op.drop_table("snapshots")
    op.execute("DROP SEQUENCE IF EXISTS snapshot_id_seq CASCADE")
