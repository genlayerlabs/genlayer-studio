"""add_default_to_llm_providers

Revision ID: 6fd3e2cea05b
Revises: 0a4312659782
Create Date: 2025-04-13 11:54:32.608423

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.orm import Session
from sqlalchemy import table, column
from backend.node.create_nodes.providers import get_default_providers


# revision identifiers, used by Alembic.
revision: str = "6fd3e2cea05b"
down_revision: Union[str, None] = "0a4312659782"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add is_default column with default value false
    op.add_column(
        "llm_provider",
        sa.Column(
            "is_default", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )

    # Create a SQLAlchemy query constructor for the existing llm_provider table
    llm_provider_query = table(
        "llm_provider",
        column("provider", sa.String),
        column("model", sa.String),
        column("plugin", sa.String),
        column("is_default", sa.Boolean),
    )

    # Get default providers
    default_providers = get_default_providers()
    default_provider_keys = [(p.provider, p.model, p.plugin) for p in default_providers]

    # For each default provider, update matching records in the existing table
    for provider, model, plugin in default_provider_keys:
        op.execute(
            llm_provider_query.update()
            .where(
                sa.and_(
                    llm_provider_query.c.provider == provider,
                    llm_provider_query.c.model == model,
                    llm_provider_query.c.plugin == plugin,
                )
            )
            .values(is_default=sa.true())
        )


def downgrade() -> None:
    op.drop_column("llm_provider", "is_default")
