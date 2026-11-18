"""add restore epoch sequence used to fence stale worker writes

Each snapshot restore advances ``restore_epoch_seq``. Consensus workers tag
every processing task with the epoch read when its transaction was claimed and
refuse any DB transaction of that task once the epoch has moved on (see
backend/consensus/worker_control.py), so work that was in flight before a
restore can never write into the restored state.

Revision ID: a8d4c2e6f0b1
Revises: f5a7c9e1b3d5
Create Date: 2026-11-18 20:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "a8d4c2e6f0b1"
down_revision: Union[str, None] = "f5a7c9e1b3d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS restore_epoch_seq")


def downgrade() -> None:
    op.execute("DROP SEQUENCE IF EXISTS restore_epoch_seq")
