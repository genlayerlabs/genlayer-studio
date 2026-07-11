from datetime import datetime
import pytest
from sqlalchemy.orm import Session

from backend.database_handler.snapshot_manager import SnapshotManager
from backend.database_handler.models import Snapshot


@pytest.fixture
def snapshot_manager(session: Session):
    yield SnapshotManager(session)


def test_create_snapshot(snapshot_manager: SnapshotManager):
    snapshot = snapshot_manager.create_snapshot()
    assert isinstance(snapshot, Snapshot)
    assert snapshot.snapshot_id == 1
    assert snapshot.state_data is not None
    assert snapshot.transaction_data is not None
    assert isinstance(snapshot.created_at, datetime)


def test_get_snapshot(snapshot_manager: SnapshotManager):
    # Create a snapshot first
    created_snapshot = snapshot_manager.create_snapshot()

    # Get the snapshot
    snapshot = snapshot_manager.get_snapshot(created_snapshot.snapshot_id)
    assert snapshot is not None
    assert snapshot.snapshot_id == created_snapshot.snapshot_id

    # Try to get non-existent snapshot
    non_existent_snapshot = snapshot_manager.get_snapshot(999)
    assert non_existent_snapshot is None


def test_restore_snapshot(snapshot_manager: SnapshotManager):
    # Create a snapshot
    snapshot = snapshot_manager.create_snapshot()

    # Restore the snapshot
    snapshot_manager.restore_snapshot(snapshot.snapshot_id)

    # Try to restore non-existent snapshot
    restored = snapshot_manager.restore_snapshot(999)
    assert restored is False


def test_delete_all_snapshots(snapshot_manager: SnapshotManager):
    # Create multiple snapshots
    snapshot1 = snapshot_manager.create_snapshot()
    snapshot2 = snapshot_manager.create_snapshot()
    snapshot1_id = snapshot1.snapshot_id
    snapshot2_id = snapshot2.snapshot_id

    # Delete all snapshots
    deleted_count = snapshot_manager.delete_all_snapshots()
    assert deleted_count == 2

    # Verify deletion
    assert snapshot_manager.get_snapshot(snapshot1_id) is None
    assert snapshot_manager.get_snapshot(snapshot2_id) is None


def test_snapshot_data_compression(snapshot_manager: SnapshotManager):
    # Create test data
    test_data = {"key1": "value1", "key2": {"nested": "value"}, "key3": [1, 2, 3]}

    # Compress and decompress
    compressed = snapshot_manager._compress_data(test_data)
    decompressed = snapshot_manager._decompress_data(compressed)

    assert isinstance(compressed, bytes)
    assert decompressed == test_data


def test_snapshot_data_compression_empty(snapshot_manager: SnapshotManager):
    # Test with empty data
    empty_data = {}
    compressed = snapshot_manager._compress_data(empty_data)
    decompressed = snapshot_manager._decompress_data(compressed)

    assert isinstance(compressed, bytes)
    assert decompressed == empty_data


def test_snapshot_sequence_after_deletion(
    session: Session, snapshot_manager: SnapshotManager
):

    # Create snapshots and store their IDs
    snapshot_ids = []
    for _ in range(3):
        snapshot = snapshot_manager.create_snapshot()
        snapshot_ids.append(snapshot.snapshot_id)

    # Verify initial snapshot IDs are sequential
    assert snapshot_ids == [1, 2, 3]

    # Delete the middle snapshot (ID 2)
    session.query(Snapshot).filter_by(snapshot_id=2).delete()
    session.commit()

    # Create a new snapshot
    new_snapshot = snapshot_manager.create_snapshot()

    # Verify the new snapshot gets the next available ID (4)
    assert new_snapshot.snapshot_id == 4

    # Verify all snapshots in database
    snapshots = session.query(Snapshot).order_by(Snapshot.snapshot_id).all()
    assert len(snapshots) == 3  # Should have 3 snapshots (1, 3, 4)
    assert [s.snapshot_id for s in snapshots] == [1, 3, 4]  # Verify IDs are correct


def test_restore_snapshot_preserves_mode_origin_trigger_and_sim_config(
    snapshot_manager: SnapshotManager, session: Session
):
    """Regression: create_snapshot omits execution_mode, origin_address,
    triggered_on and sim_config from the serialized transaction dict (restore
    even does tx_info.get("sim_config"), clearly expecting it). So
    sim_restoreSnapshot silently rewrites transactions: leader-only txs come
    back as NORMAL (losing their finality-window bypass), fee-settle refunds go
    to from_address instead of origin_address, per-tx sim config is lost, and
    triggered_on is dropped.
    """
    import json

    from sqlalchemy import text

    from backend.database_handler.models import Transactions
    from backend.database_handler.transactions_processor import TransactionsProcessor

    tx_hash = "0x" + "ab" * 32
    origin = "0x" + "33" * 20
    sim_config = {"validators": [{"provider": "openai", "model": "gpt-4.1"}]}

    TransactionsProcessor(session).insert_transaction(
        from_address="0x" + "11" * 20,
        to_address="0x" + "22" * 20,
        data={},
        value=0,
        type=1,
        nonce=0,
        leader_only=True,
        config_rotation_rounds=3,
        transaction_hash=tx_hash,
    )
    session.execute(
        text(
            """
            UPDATE transactions
            SET execution_mode = 'LEADER_ONLY',
                origin_address = :origin,
                triggered_on = 'accepted',
                sim_config = CAST(:sim_config AS jsonb)
            WHERE hash = :hash
            """
        ),
        {"origin": origin, "sim_config": json.dumps(sim_config), "hash": tx_hash},
    )
    session.commit()

    snap = snapshot_manager.create_snapshot()
    snapshot_manager.restore_snapshot(snap.snapshot_id)
    session.expire_all()

    restored = session.query(Transactions).filter_by(hash=tx_hash).one()
    assert restored.execution_mode == "LEADER_ONLY"
    assert restored.origin_address == origin
    assert restored.triggered_on == "accepted"
    assert restored.sim_config == sim_config
