"""add_leader_timeout_status

Revision ID: c0244b1d49b0
Revises: 99015c5b5b78
Create Date: 2025-06-11 18:49:58.631336

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "c0244b1d49b0"
down_revision: Union[str, None] = "99015c5b5b78"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Check if LEADER_TIMEOUT exists in the enum
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM pg_enum WHERE enumlabel = 'LEADER_TIMEOUT' AND enumtypid = (SELECT oid FROM pg_type WHERE typname = 'transaction_status'))"
        )
    ).scalar()

    if not result:
        op.execute(
            sa.text(
                "ALTER TYPE transaction_status ADD VALUE 'LEADER_TIMEOUT' AFTER 'UNDETERMINED'"
            )
        )

    op.add_column(
        "transactions", sa.Column("appeal_leader_timeout", sa.Boolean(), nullable=True)
    )
    op.execute(
        "UPDATE transactions SET appeal_leader_timeout = FALSE WHERE appeal_leader_timeout IS NULL"
    )
    op.alter_column("transactions", "appeal_leader_timeout", nullable=False)

    op.add_column(
        "transactions",
        sa.Column(
            "leader_timeout_validators",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    # Create new enum without LEADER_TIMEOUT
    op.execute(
        "CREATE TYPE transaction_status_new AS ENUM ('PENDING', 'ACTIVATED', 'CANCELED', 'PROPOSING', 'COMMITTING', 'REVEALING', 'ACCEPTED', 'FINALIZED', 'UNDETERMINED')"
    )

    # First remove the default
    op.execute("ALTER TABLE transactions ALTER COLUMN status DROP DEFAULT")

    # Convert existing LEADER_TIMEOUT values to PENDING
    op.execute(
        "UPDATE transactions SET status = 'PENDING' WHERE status = 'LEADER_TIMEOUT'"
    )

    # Change column type
    op.execute(
        "ALTER TABLE transactions ALTER COLUMN status TYPE transaction_status_new USING status::text::transaction_status_new"
    )

    # Add back the default
    op.execute("ALTER TABLE transactions ALTER COLUMN status SET DEFAULT 'PENDING'")

    # Drop old type
    op.execute("DROP TYPE transaction_status")

    # Rename new type to original name
    op.execute("ALTER TYPE transaction_status_new RENAME TO transaction_status")

    op.drop_column("transactions", "appeal_leader_timeout")

    op.drop_column("transactions", "leader_timeout_validators")
