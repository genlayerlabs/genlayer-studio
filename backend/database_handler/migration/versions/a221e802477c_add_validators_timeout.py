"""add_validators_timeout

Revision ID: a221e802477c
Revises: 22319ddb113d
Create Date: 2025-06-16 16:13:46.841359

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a221e802477c"
down_revision: Union[str, None] = "22319ddb113d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Check if VALIDATORS_TIMEOUT exists in the enum
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM pg_enum WHERE enumlabel = 'VALIDATORS_TIMEOUT' AND enumtypid = (SELECT oid FROM pg_type WHERE typname = 'transaction_status'))"
        )
    ).scalar()

    if not result:
        op.execute(
            sa.text(
                "ALTER TYPE transaction_status ADD VALUE 'VALIDATORS_TIMEOUT' AFTER 'LEADER_TIMEOUT'"
            )
        )

    op.add_column(
        "transactions",
        sa.Column("appeal_validators_timeout", sa.Boolean(), nullable=True),
    )
    op.execute(
        "UPDATE transactions SET appeal_validators_timeout = FALSE WHERE appeal_validators_timeout IS NULL"
    )
    op.alter_column("transactions", "appeal_validators_timeout", nullable=False)


def downgrade() -> None:
    # Create new enum without VALIDATORS_TIMEOUT
    op.execute(
        "CREATE TYPE transaction_status_new AS ENUM ('PENDING', 'ACTIVATED', 'CANCELED', 'PROPOSING', 'COMMITTING', 'REVEALING', 'ACCEPTED', 'FINALIZED', 'UNDETERMINED', 'LEADER_TIMEOUT')"
    )

    # First remove the default
    op.execute("ALTER TABLE transactions ALTER COLUMN status DROP DEFAULT")

    # Convert existing VALIDATORS_TIMEOUT values to PENDING
    op.execute(
        "UPDATE transactions SET status = 'PENDING' WHERE status = 'VALIDATORS_TIMEOUT'"
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

    op.drop_column("transactions", "appeal_validators_timeout")
