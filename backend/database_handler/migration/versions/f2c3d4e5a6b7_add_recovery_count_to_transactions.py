"""add recovery_count column to transactions

Tracks how many times `recover_stuck_transactions` has reset a tx back
to PENDING. Used to cap the recovery-reclaim cycle: after N resets on
the same tx, escalate to CANCELED instead of letting it keep blocking
the per-contract queue indefinitely.

Revision ID: f2c3d4e5a6b7
Revises: e1f2a3b4c5d6
Create Date: 2026-04-17 11:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f2c3d4e5a6b7"
down_revision: Union[str, None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ADD COLUMN with constant DEFAULT is metadata-only in PG 11+
    # (no table rewrite, no per-row scan). It still needs a brief
    # ACCESS EXCLUSIVE lock though, and on a hot table like `transactions`
    # a slow long-running query could queue the ALTER behind it — which
    # in turn queues every new read/write behind the ALTER.
    #
    # Belt + suspenders: cap how long we're willing to wait for the lock.
    # If we can't grab it in 5s, fail the migration fast and let it retry,
    # rather than pile up every consensus-worker commit behind us.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")

    op.add_column(
        "transactions",
        sa.Column(
            "recovery_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_column("transactions", "recovery_count")
