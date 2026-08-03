"""add reroute_to to current_state

Revision ID: c1d2e3f4a5b6
Revises: d0e1f2a3b4c5
Create Date: 2026-07-24 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, None] = "d0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


LEGACY_EXECUTOR_SELECTOR = r"re:^v0\.2\."
"""
Backfilled onto every pre-existing contract row on upgrade.

Studios being upgraded from before multi-version support only ever had a
single GenVM line (v0.2.x) to deploy against, so every genuine contract row
that isn't already pinned needs a legacy selector: without it, an unpinned
row means "resolve from the manifest" (the current/latest line), which would
silently move already-deployed v0.2 contracts onto an executor they were
never deployed or tested against.
"""


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")

    op.add_column(
        "current_state",
        sa.Column("reroute_to", sa.String(255), nullable=True),
    )

    # Only genuine contract rows: EOAs (and reset contracts) carry
    # `data = '{}'`, deployed contracts carry `data->'state'`. Excluding rows
    # that already have a `reroute_to` preserves anything set some other way
    # (e.g. by a data migration or manual fixup run ahead of this one).
    op.execute(
        sa.text(
            """
            UPDATE current_state
            SET reroute_to = :selector
            WHERE reroute_to IS NULL
              AND data ? 'state'
            """
        ).bindparams(selector=LEGACY_EXECUTOR_SELECTOR)
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_column("current_state", "reroute_to")
