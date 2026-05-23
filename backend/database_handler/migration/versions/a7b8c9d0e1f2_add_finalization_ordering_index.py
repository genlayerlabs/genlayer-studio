"""add partial index supporting claim_next_finalization's ordering NOT EXISTS

Revision ID: a7b8c9d0e1f2
Revises: f2c3d4e5a6b7
Create Date: 2026-05-21 09:30:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "f2c3d4e5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PR #1630 added a per-contract finalization ordering invariant to
    # claim_next_finalization: a tx can only be claimed once every older
    # tx on the same contract has reached a terminal state. The check
    # is a NOT EXISTS:
    #
    #     SELECT 1 FROM transactions earlier
    #     WHERE earlier.to_address IS NOT DISTINCT FROM t.to_address
    #         AND earlier.created_at < t.created_at
    #         AND earlier.status NOT IN ('FINALIZED', 'CANCELED')
    #         AND earlier.hash != t.hash
    #
    # The negated `status NOT IN (...)` predicate cannot use the existing
    # `idx_transactions_status` btree, so Postgres falls back to a parallel
    # sequential scan of the full transactions table on every poll.
    # Measured on Studio Prod (~80k rows): 110ms / 75,888 buffers per
    # query, executed by every worker every 5s. That continuous scanning
    # drove the consensus-worker CPU to 98%+ under modest load and
    # tripped the stuck_finalizations alert via finalization head-of-
    # line slowdown.
    #
    # Partial btree on (to_address, created_at) WHERE status NOT IN
    # (...) is small (only non-terminal rows — typically <500 of the
    # ~80k total) and exactly matches the access pattern. Same query
    # dropped to 0.2ms / 28 buffers in EXPLAIN — ~570x improvement.
    #
    # CONCURRENTLY so the build doesn't lock writes on prod. Alembic's
    # implicit transaction has to be committed first since CONCURRENTLY
    # can't run inside a transaction block.
    op.execute("COMMIT")
    op.execute(
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS
            idx_transactions_nonterminal_by_contract
        ON transactions (to_address, created_at)
        WHERE status NOT IN ('FINALIZED', 'CANCELED')
        """
    )


def downgrade() -> None:
    op.execute("COMMIT")
    op.execute(
        "DROP INDEX CONCURRENTLY IF EXISTS idx_transactions_nonterminal_by_contract"
    )
