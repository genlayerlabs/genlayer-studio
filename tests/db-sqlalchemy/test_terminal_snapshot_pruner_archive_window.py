"""Regression test for the split-phase archive scan window.

The standalone ``--phase archive`` pipeline (``archive_once``) writes archive
rows but does NOT prune the hot ``contract_snapshot`` column. The candidate
query in ``_fetch_archive_candidates`` selects the oldest ``batch_size * 5``
terminal rows into a scan window *before* excluding rows that are already
archived. Because archived-but-unpruned rows keep ``contract_snapshot NOT
NULL`` and stay the oldest rows, they permanently occupy the scan window. Once
the oldest ``batch_size * 5`` rows are archived, the anti-join drains the
selection to empty and the archive phase reports "no eligible candidates" even
though many un-archived terminal snapshots still exist just past the window.

This test seeds ``batch_size * 5 + 1`` eligible terminal snapshots and drives
the archive phase to completion. A correct implementation archives every
eligible row; the current implementation stalls after ``batch_size * 5``.
"""

import uuid

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from backend.database_handler.terminal_snapshot_pruner import (
    TerminalSnapshotPruner,
    TerminalSnapshotPrunerConfig,
)
from backend.database_handler.transactions_processor import TransactionsProcessor


def _seed_terminal_snapshot(SessionLocal, *, index: int, age_days: int) -> str:
    tx_hash = f"0x{uuid.uuid4().hex}{uuid.uuid4().hex}"
    session = SessionLocal()
    try:
        tp = TransactionsProcessor(session)
        tp.insert_transaction(
            from_address="0x9F0e84243496AcFB3Cd99D02eA59673c05901501",
            to_address="0xAcec3A6d871C25F591aBd4fC24054e524BBbF794",
            data={"key": f"value-{index}"},
            value=1.0,
            type=1,
            nonce=index,
            leader_only=True,
            config_rotation_rounds=3,
            transaction_hash=tx_hash,
        )
        tp.set_transaction_contract_snapshot(
            tx_hash,
            {"state": {"finalized": {"counter": index}}},
        )
        # Make each row's created_at strictly ordered and older than the
        # retention cutoff so ORDER BY created_at ASC is deterministic.
        session.execute(
            text(
                """
                UPDATE transactions
                SET status = 'FINALIZED'::transaction_status,
                    created_at = CURRENT_TIMESTAMP
                        - make_interval(days => :age_days)
                        - make_interval(secs => :secs)
                WHERE hash = :hash
                """
            ),
            {"hash": tx_hash, "age_days": age_days, "secs": index},
        )
        session.commit()
    finally:
        session.close()
    return tx_hash


def test_archive_phase_archives_beyond_scan_window(engine, tmp_path):
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    batch_size = 2
    scan_window = batch_size * 5  # matches _fetch_archive_candidates
    total_rows = scan_window + 1  # one row lives just past the window

    # Seed oldest-first so the scan window is deterministic.
    seeded = [
        _seed_terminal_snapshot(SessionLocal, index=i, age_days=2)
        for i in range(total_rows)
    ]
    assert len(seeded) == total_rows

    config = TerminalSnapshotPrunerConfig(
        enabled=True,
        archive_backend="file",
        file_dir=str(tmp_path),
        retention_hours=24,
        batch_size=batch_size,
    )
    pruner = TerminalSnapshotPruner(SessionLocal, config)

    # Drive the standalone archive phase to completion, exactly like the CLI
    # worker loop (stop when a batch reports no candidates).
    total_archived = 0
    for _ in range(total_rows * 5):  # generous upper bound to avoid hanging
        result = pruner.archive_once()
        if result.candidates == 0:
            break
        total_archived += result.archived

    read_session = SessionLocal()
    try:
        archived_count = read_session.execute(
            text(
                "SELECT COUNT(*) FROM transaction_snapshot_archives "
                "WHERE archive_status = 'archived'"
            )
        ).scalar_one()
    finally:
        read_session.close()

    # Every eligible terminal snapshot must eventually get an archive row.
    assert archived_count == total_rows, (
        f"archive phase stalled: only {archived_count} of {total_rows} "
        "eligible snapshots were archived (scan-window anti-join bug)"
    )
    assert total_archived == total_rows
