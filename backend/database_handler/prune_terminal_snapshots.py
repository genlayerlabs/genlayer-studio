from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import logging
import os
import threading
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


class BatchBudget:
    def __init__(self, max_batches: int):
        self.max_batches = max_batches
        self.started_batches = 0
        self._lock = threading.Lock()

    def reserve(self) -> int | None:
        with self._lock:
            if self.max_batches and self.started_batches >= self.max_batches:
                return None
            self.started_batches += 1
            return self.started_batches


def _format_mib_per_second(byte_count: int, elapsed_seconds: float) -> str:
    if elapsed_seconds <= 0:
        return "n/a"
    return f"{byte_count / elapsed_seconds / (1024 * 1024):.2f} MiB/s"


def _empty_totals() -> dict:
    return {
        "batches": 0,
        "candidates": 0,
        "archived": 0,
        "verified": 0,
        "pruned": 0,
        "logical_bytes": 0,
        "compressed_bytes": 0,
    }


def _add_batch_result(totals: dict, result) -> None:
    totals["batches"] += 1
    totals["candidates"] += result.candidates
    totals["archived"] += result.archived
    totals["verified"] += result.verified
    totals["pruned"] += result.pruned
    totals["logical_bytes"] += result.logical_bytes
    totals["compressed_bytes"] += result.compressed_bytes


def _log_batch_result(
    *,
    phase: str,
    batch_number: int,
    worker_id: int,
    result,
    elapsed_seconds: float,
) -> None:
    logger.info(
        "Batch %s complete: phase=%s worker=%s candidates=%s archived=%s "
        "verified=%s pruned=%s logical_bytes=%s compressed_bytes=%s "
        "dry_run=%s elapsed=%.2fs logical_rate=%s compressed_rate=%s",
        batch_number,
        phase,
        worker_id,
        result.candidates,
        result.archived,
        result.verified,
        result.pruned,
        result.logical_bytes,
        result.compressed_bytes,
        result.dry_run,
        elapsed_seconds,
        _format_mib_per_second(result.logical_bytes, elapsed_seconds),
        _format_mib_per_second(result.compressed_bytes, elapsed_seconds),
    )


def _run_pruner_worker(
    *,
    worker_id: int,
    session_factory,
    config: TerminalSnapshotPrunerConfig,
    batch_budget: BatchBudget,
    sleep_seconds: float,
    totals: dict,
    totals_lock: threading.Lock,
    phase: str,
    verify_inline: bool,
) -> None:
    pruner = TerminalSnapshotPruner(session_factory, config)

    while True:
        batch_number = batch_budget.reserve()
        if batch_number is None:
            return

        batch_started_at = time.monotonic()
        if phase == "archive":
            result = pruner.archive_once(verify_inline=verify_inline)
        elif phase == "verify":
            result = pruner.verify_archives_once()
        elif phase == "prune":
            result = pruner.prune_verified_once()
        else:
            result = pruner.prune_once()
        batch_elapsed = time.monotonic() - batch_started_at
        if result.candidates == 0:
            logger.info(
                "Worker %s found no eligible terminal contract snapshots "
                "for phase %s",
                worker_id,
                phase,
            )
            return

        with totals_lock:
            _add_batch_result(totals, result)

        _log_batch_result(
            phase=phase,
            batch_number=batch_number,
            worker_id=worker_id,
            result=result,
            elapsed_seconds=batch_elapsed,
        )

        if sleep_seconds > 0:
            time.sleep(sleep_seconds)


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
        "--phase",
        choices=("full", "archive", "verify", "prune"),
        default="full",
        help=(
            "Pipeline phase to run. full preserves the original archive, verify, "
            "and prune behavior in one pass. archive writes archive rows without "
            "pruning. verify reads archived objects back and marks them verified. "
            "prune removes hot snapshots only for verified archive rows."
        ),
    )
    parser.add_argument(
        "--inline-verify",
        action="store_true",
        help=(
            "When --phase archive is used, read each object back immediately and "
            "mark it verified. Off by default so archive and verification can be "
            "scaled independently."
        ),
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
        help=(
            "Stop after this many claimed batch attempts across all workers. "
            "0 means run until no candidates remain."
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Parallel pruner workers. Each worker uses its own DB session.",
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
    if args.workers < 1:
        raise SystemExit("--workers must be at least 1")

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
    if args.phase == "archive":
        config.validate_for_archive()
    elif args.phase == "verify":
        config.validate_for_verify()
    elif args.phase == "full":
        config.validate_for_run()

    engine = create_engine(
        get_database_url(),
        pool_pre_ping=True,
        pool_recycle=3600,
        pool_size=args.workers,
        max_overflow=0,
    )
    SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=engine, expire_on_commit=False
    )

    totals = _empty_totals()
    totals_lock = threading.Lock()
    batch_budget = BatchBudget(args.max_batches)

    logger.info(
        "Starting terminal snapshot pruning "
        "(phase=%s batch_size=%s retention_hours=%s archive_enabled=%s "
        "archive_backend=%s dry_run=%s workers=%s max_batches=%s "
        "inline_verify=%s)",
        args.phase,
        config.batch_size,
        config.retention_hours,
        config.archive_enabled,
        config.archive_backend,
        config.dry_run,
        args.workers,
        args.max_batches,
        args.inline_verify,
    )

    started_at = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [
            executor.submit(
                _run_pruner_worker,
                worker_id=worker_id,
                session_factory=SessionLocal,
                config=config,
                batch_budget=batch_budget,
                sleep_seconds=args.sleep_seconds,
                totals=totals,
                totals_lock=totals_lock,
                phase=args.phase,
                verify_inline=args.inline_verify,
            )
            for worker_id in range(1, args.workers + 1)
        ]
        for future in futures:
            future.result()

    total_elapsed = time.monotonic() - started_at
    logger.info(
        "Finished terminal snapshot pruning: elapsed=%.2fs logical_rate=%s "
        "compressed_rate=%s attempted_batches=%s totals=%s",
        total_elapsed,
        _format_mib_per_second(totals["logical_bytes"], total_elapsed),
        _format_mib_per_second(totals["compressed_bytes"], total_elapsed),
        batch_budget.started_batches,
        totals,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
