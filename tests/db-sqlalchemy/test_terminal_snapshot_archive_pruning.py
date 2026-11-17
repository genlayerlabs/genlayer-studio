import uuid

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from backend.database_handler.terminal_snapshot_pruner import (
    SnapshotArchiveReader,
    TerminalSnapshotPruner,
    TerminalSnapshotPrunerConfig,
)
from backend.database_handler.transactions_processor import TransactionsProcessor


def test_pruner_archives_verifies_prunes_and_read_through_hydrates_snapshot(
    engine, tmp_path
):
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    tx_hash = f"0x{uuid.uuid4().hex}{uuid.uuid4().hex}"
    snapshot = {
        "state": {
            "accepted": {"counter": 1, "owner": "0xabc"},
            "finalized": {"counter": 1, "owner": "0xabc"},
        },
        "metadata": {"source": "db-integration-test"},
    }

    setup_session = SessionLocal()
    try:
        tp = TransactionsProcessor(setup_session)
        tp.insert_transaction(
            from_address="0x9F0e84243496AcFB3Cd99D02eA59673c05901501",
            to_address="0xAcec3A6d871C25F591aBd4fC24054e524BBbF794",
            data={"key": "value"},
            value=1.0,
            type=1,
            nonce=0,
            leader_only=True,
            config_rotation_rounds=3,
            transaction_hash=tx_hash,
        )
        tp.set_transaction_contract_snapshot(tx_hash, snapshot)
        setup_session.execute(
            text(
                """
                UPDATE transactions
                SET status = 'FINALIZED'::transaction_status,
                    created_at = CURRENT_TIMESTAMP - INTERVAL '2 days'
                WHERE hash = :hash
                """
            ),
            {"hash": tx_hash},
        )
        setup_session.commit()
    finally:
        setup_session.close()

    config = TerminalSnapshotPrunerConfig(
        enabled=True,
        archive_backend="file",
        file_dir=str(tmp_path),
        retention_hours=24,
        batch_size=10,
    )
    result = TerminalSnapshotPruner(SessionLocal, config).prune_once()

    assert result.candidates == 1
    assert result.archived == 1
    assert result.pruned == 1

    read_session = SessionLocal()
    try:
        hot_snapshot = read_session.execute(
            text("SELECT contract_snapshot FROM transactions WHERE hash = :hash"),
            {"hash": tx_hash},
        ).scalar_one()
        archive_row = (
            read_session.execute(
                text(
                    """
                    SELECT backend, uri, archive_status, snapshot_sha256,
                           compressed_sha256, verified_at
                    FROM transaction_snapshot_archives
                    WHERE tx_hash = :hash
                    """
                ),
                {"hash": tx_hash},
            )
            .mappings()
            .one()
        )

        assert hot_snapshot is None
        assert archive_row["backend"] == "file"
        assert archive_row["archive_status"] == "pruned"
        assert archive_row["uri"].startswith("file://")
        assert archive_row["snapshot_sha256"]
        assert archive_row["compressed_sha256"]
        assert archive_row["verified_at"] is not None

        tp = TransactionsProcessor(
            read_session,
            snapshot_archive=SnapshotArchiveReader(file_dir=tmp_path),
        )
        tx = tp.get_transaction_by_hash(tx_hash)

        assert tx["contract_snapshot"] == snapshot
    finally:
        read_session.close()


def test_archive_verify_prune_phases_preserve_read_through(engine, tmp_path):
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    tx_hash = f"0x{uuid.uuid4().hex}{uuid.uuid4().hex}"
    snapshot = {
        "state": {
            "accepted": {"counter": 2},
            "finalized": {"counter": 2},
        },
    }

    setup_session = SessionLocal()
    try:
        tp = TransactionsProcessor(setup_session)
        tp.insert_transaction(
            from_address="0x9F0e84243496AcFB3Cd99D02eA59673c05901501",
            to_address="0xAcec3A6d871C25F591aBd4fC24054e524BBbF794",
            data={"key": "value"},
            value=1.0,
            type=1,
            nonce=0,
            leader_only=True,
            config_rotation_rounds=3,
            transaction_hash=tx_hash,
        )
        tp.set_transaction_contract_snapshot(tx_hash, snapshot)
        setup_session.execute(
            text(
                """
                UPDATE transactions
                SET status = 'FINALIZED'::transaction_status,
                    created_at = CURRENT_TIMESTAMP - INTERVAL '2 days'
                WHERE hash = :hash
                """
            ),
            {"hash": tx_hash},
        )
        setup_session.commit()
    finally:
        setup_session.close()

    config = TerminalSnapshotPrunerConfig(
        enabled=True,
        archive_backend="file",
        file_dir=str(tmp_path),
        retention_hours=24,
        batch_size=10,
    )
    pruner = TerminalSnapshotPruner(SessionLocal, config)

    archive_result = pruner.archive_once()
    assert archive_result.candidates == 1
    assert archive_result.archived == 1
    assert archive_result.verified == 0
    assert archive_result.pruned == 0

    inspect_session = SessionLocal()
    try:
        row = (
            inspect_session.execute(
                text(
                    """
                    SELECT transactions.contract_snapshot IS NOT NULL AS hot_present,
                           archives.archive_status,
                           archives.verified_at,
                           archives.pruned_at
                    FROM transactions
                    JOIN transaction_snapshot_archives archives
                      ON archives.tx_hash = transactions.hash
                    WHERE transactions.hash = :hash
                    """
                ),
                {"hash": tx_hash},
            )
            .mappings()
            .one()
        )
        assert row["hot_present"] is True
        assert row["archive_status"] == "archived"
        assert row["verified_at"] is None
        assert row["pruned_at"] is None
    finally:
        inspect_session.close()

    verify_result = pruner.verify_archives_once()
    assert verify_result.candidates == 1
    assert verify_result.verified == 1
    assert verify_result.pruned == 0

    prune_result = pruner.prune_verified_once()
    assert prune_result.candidates == 1
    assert prune_result.pruned == 1

    read_session = SessionLocal()
    try:
        row = (
            read_session.execute(
                text(
                    """
                    SELECT transactions.contract_snapshot IS NULL AS hot_null,
                           archives.archive_status,
                           archives.verified_at,
                           archives.pruned_at
                    FROM transactions
                    JOIN transaction_snapshot_archives archives
                      ON archives.tx_hash = transactions.hash
                    WHERE transactions.hash = :hash
                    """
                ),
                {"hash": tx_hash},
            )
            .mappings()
            .one()
        )
        assert row["hot_null"] is True
        assert row["archive_status"] == "pruned"
        assert row["verified_at"] is not None
        assert row["pruned_at"] is not None

        tp = TransactionsProcessor(
            read_session,
            snapshot_archive=SnapshotArchiveReader(file_dir=tmp_path),
        )
        tx = tp.get_transaction_by_hash(tx_hash)

        assert tx["contract_snapshot"] == snapshot
    finally:
        read_session.close()
