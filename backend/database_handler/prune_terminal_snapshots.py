from __future__ import annotations

import argparse
import logging
import os
import time
from dataclasses import replace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database_handler.terminal_snapshot_pruner import (
    TerminalSnapshotPruner,
    TerminalSnapshotPrunerConfig,
)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _get_db_name(database: str) -> str:
    return "genlayer_state" if database == "genlayer" else database


def get_database_url() -> str:
    explicit_url = os.getenv("DB_URL") or os.getenv("POSTGRES_URL")
    if explicit_url:
        return explicit_url

    db_user = os.getenv("DBUSER", "postgres")
    db_password = os.getenv("DBPASSWORD", "postgres")  # NOSONAR - local dev fallback
    db_host = os.getenv("DBHOST", "localhost")
    db_port = os.getenv("DBPORT", "5432")
    db_name = os.getenv("DBNAME") or _get_db_name("genlayer")
    return (
        f"postgresql+psycopg2://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Archive terminal transaction contract snapshots and prune them from "
            "the hot transactions table."
        )
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Rows to process per transaction. Defaults to env/config value.",
    )
    parser.add_argument(
        "--retention-hours",
        type=int,
        default=None,
        help="Only prune terminal snapshots older than this many hours.",
    )
    parser.add_argument(
        "--max-batches",
        type=int,
        default=0,
        help="Stop after this many batches. 0 means run until no candidates remain.",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.25,
        help="Pause between batches to reduce database pressure.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count candidates in batches without writing archive objects or pruning rows.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = TerminalSnapshotPrunerConfig.from_environment()
    config = replace(
        config,
        enabled=True,
        dry_run=args.dry_run or config.dry_run,
        batch_size=args.batch_size or config.batch_size,
        retention_hours=(
            args.retention_hours
            if args.retention_hours is not None
            else config.retention_hours
        ),
    )
    config.validate_for_run()

    engine = create_engine(get_database_url(), pool_pre_ping=True, pool_recycle=3600)
    SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=engine, expire_on_commit=False
    )

    pruner = TerminalSnapshotPruner(SessionLocal, config)
    totals = {
        "batches": 0,
        "candidates": 0,
        "archived": 0,
        "pruned": 0,
        "logical_bytes": 0,
        "compressed_bytes": 0,
    }

    logger.info(
        "Starting terminal snapshot pruning "
        "(batch_size=%s retention_hours=%s archive_enabled=%s "
        "archive_backend=%s dry_run=%s)",
        config.batch_size,
        config.retention_hours,
        config.archive_enabled,
        config.archive_backend,
        config.dry_run,
    )

    while True:
        result = pruner.prune_once()
        if result.candidates == 0:
            logger.info("No more eligible terminal contract snapshots found")
            break

        totals["batches"] += 1
        totals["candidates"] += result.candidates
        totals["archived"] += result.archived
        totals["pruned"] += result.pruned
        totals["logical_bytes"] += result.logical_bytes
        totals["compressed_bytes"] += result.compressed_bytes

        logger.info(
            "Batch %s complete: candidates=%s archived=%s pruned=%s "
            "logical_bytes=%s compressed_bytes=%s dry_run=%s",
            totals["batches"],
            result.candidates,
            result.archived,
            result.pruned,
            result.logical_bytes,
            result.compressed_bytes,
            result.dry_run,
        )

        if args.max_batches and totals["batches"] >= args.max_batches:
            logger.info("Reached max batch limit: %s", args.max_batches)
            break

        if args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

    logger.info("Finished terminal snapshot pruning: %s", totals)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
