"""add_activated_status

Revision ID: 67943badcbe9
Revises: a4a32d27dde2
Create Date: 2025-02-10 17:26:49.703226

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "67943badcbe9"
down_revision: Union[str, None] = "a4a32d27dde2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Check if ACTIVATED exists in the enum
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM pg_enum WHERE enumlabel = 'ACTIVATED' AND enumtypid = (SELECT oid FROM pg_type WHERE typname = 'transaction_status'))"
        )
    ).scalar()

    if not result:
        op.execute(
            sa.text(
                "ALTER TYPE transaction_status ADD VALUE 'ACTIVATED' AFTER 'PENDING'"
            )
        )


def downgrade() -> None:
    # Create new enum without ACTIVATED
    op.execute(
        "CREATE TYPE transaction_status_new AS ENUM ('PENDING', 'CANCELED', 'PROPOSING', 'COMMITTING', 'REVEALING', 'ACCEPTED', 'FINALIZED', 'UNDETERMINED')"
    )

    # First remove the default
    op.execute("ALTER TABLE transactions ALTER COLUMN status DROP DEFAULT")

    # Convert existing ACTIVATED values to PENDING
    op.execute("UPDATE transactions SET status = 'PENDING' WHERE status = 'ACTIVATED'")

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
