"""rename current_state.reroute_to to genvm_executor_selector

`reroute_to` named the manager's internal/debug override field, not what the
persisted value means: an exact executor version or a `re:` selector pinning
a contract to a GenVM executor line. `genvm_executor_selector` says that
directly. The external `sim_config.reroute_to` RPC field is unaffected.

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-07-30 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.alter_column(
        "current_state",
        "reroute_to",
        new_column_name="genvm_executor_selector",
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.alter_column(
        "current_state",
        "genvm_executor_selector",
        new_column_name="reroute_to",
    )
