"""double free tier rate limits

Revision ID: c3d7f2a8b104
Revises: b1c3e5f7a902
Create Date: 2026-03-16 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c3d7f2a8b104"
down_revision: Union[str, None] = "b1c3e5f7a902"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE api_tiers
        SET rate_limit_minute = 60,
            rate_limit_hour = 1000,
            rate_limit_day = 10000
        WHERE name = 'free'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE api_tiers
        SET rate_limit_minute = 30,
            rate_limit_hour = 500,
            rate_limit_day = 5000
        WHERE name = 'free'
        """
    )
