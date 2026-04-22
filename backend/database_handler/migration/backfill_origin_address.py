"""
Batched backfill script for origin_address column.

Run AFTER the schema migration (a1b2c3d4e5f6) has been applied.
Safe to re-run (idempotent) — only updates rows where origin_address IS NULL.

Usage:
    python -m backend.database_handler.migration.backfill_origin_address

Environment:
    DB_URL or POSTGRES_URL — database connection string
"""

import os
import time
import logging

from sqlalchemy import create_engine, text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BATCH_SIZE = 1000
SLEEP_BETWEEN_BATCHES = 0.1  # seconds


def get_db_url() -> str:
    url = os.getenv("DB_URL") or os.getenv("POSTGRES_URL")
    if not url:
        raise RuntimeError("Set DB_URL or POSTGRES_URL environment variable")
    return url


def backfill():
    engine = create_engine(get_db_url())

    with engine.connect() as conn:
        # Step 1: Backfill top-level transactions (no parent)
        logger.info("Backfilling top-level transactions (triggered_by_hash IS NULL)...")
        total_top = 0
        while True:
            result = conn.execute(
                text(
                    """
                    UPDATE transactions
                    SET origin_address = from_address
                    WHERE hash IN (
                        SELECT hash FROM transactions
                        WHERE origin_address IS NULL
                          AND triggered_by_hash IS NULL
                        LIMIT :batch_size
                    )
                """
                ),
                {"batch_size": BATCH_SIZE},
            )
            conn.commit()
            updated = result.rowcount
            total_top += updated
            if updated == 0:
                break
            logger.info(f"  Updated {total_top} top-level rows so far...")
            time.sleep(SLEEP_BETWEEN_BATCHES)

        logger.info(f"Top-level backfill complete: {total_top} rows updated.")

        # Step 2: Backfill sub-call transactions using recursive walk
        # Process level by level: children of already-backfilled parents
        logger.info(
            "Backfilling sub-call transactions (walking triggered_by_hash chain)..."
        )
        total_sub = 0
        while True:
            result = conn.execute(
                text(
                    """
                    UPDATE transactions
                    SET origin_address = parent.origin_address
                    FROM transactions parent
                    WHERE transactions.hash IN (
                        SELECT child.hash FROM transactions child
                        JOIN transactions p ON child.triggered_by_hash = p.hash
                        WHERE child.origin_address IS NULL
                          AND p.origin_address IS NOT NULL
                        LIMIT :batch_size
                    )
                    AND transactions.triggered_by_hash = parent.hash
                    AND parent.origin_address IS NOT NULL
                """
                ),
                {"batch_size": BATCH_SIZE},
            )
            conn.commit()
            updated = result.rowcount
            total_sub += updated
            if updated == 0:
                break
            logger.info(f"  Updated {total_sub} sub-call rows so far...")
            time.sleep(SLEEP_BETWEEN_BATCHES)

        logger.info(f"Sub-call backfill complete: {total_sub} rows updated.")

        # Verify: check for any remaining NULL origin_address
        remaining = conn.execute(
            text("SELECT COUNT(*) FROM transactions WHERE origin_address IS NULL")
        ).scalar()
        if remaining:
            logger.warning(
                f"{remaining} transactions still have NULL origin_address "
                "(orphaned triggered_by_hash chains?)."
            )
        else:
            logger.info("All transactions have origin_address set.")


if __name__ == "__main__":
    backfill()
