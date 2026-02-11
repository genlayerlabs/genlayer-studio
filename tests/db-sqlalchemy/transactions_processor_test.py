from sqlalchemy.orm import Session
from sqlalchemy import text
import pytest
from unittest.mock import patch, MagicMock
import os
import math
from datetime import datetime
from web3 import Web3
from web3.providers import BaseProvider

from backend.database_handler.chain_snapshot import ChainSnapshot
from backend.database_handler.models import Transactions
from backend.database_handler.transactions_processor import (
    TransactionStatus,
    TransactionsProcessor,
    TransactionAddressFilter,
)
from backend.consensus.types import ConsensusRound


_tx_counter = 0


def _make_tx(tp, **overrides):
    """Create a transaction with sensible defaults and unique hash."""
    global _tx_counter
    _tx_counter += 1
    defaults = dict(
        from_address="0x9F0e84243496AcFB3Cd99D02eA59673c05901501",
        to_address="0xAcec3A6d871C25F591aBd4fC24054e524BBbF794",
        data={"key": "value"},
        value=1.0,
        type=1,
        nonce=0,
        leader_only=True,
        config_rotation_rounds=3,
        triggered_by_hash=None,
        transaction_hash=f"0x{_tx_counter:064x}",
    )
    defaults.update(overrides)
    tx_hash = tp.insert_transaction(**{k: v for k, v in defaults.items()})
    tp.session.commit()
    return tx_hash


def _create_mock_web3_instance(is_connected: bool):
    """Helper function to create a mock Web3 instance with specified connection status."""
    web3_instance = Web3(MagicMock(spec=BaseProvider))
    web3_instance.eth = MagicMock()
    web3_instance.eth.accounts = ["0x0000000000000000000000000000000000000000"]

    call_count = {"count": 0}

    def mock_get_transaction_count(address, block_identifier="latest"):
        result = call_count["count"]
        call_count["count"] += 1
        return result

    web3_instance.eth.get_transaction_count = mock_get_transaction_count
    web3_instance.is_connected = MagicMock(return_value=is_connected)

    return web3_instance


@pytest.fixture
def mock_env_and_web3_connected():
    with patch.dict(
        os.environ,
        {
            "HARDHAT_PORT": "8545",
            "HARDHAT_URL": "http://localhost",
            "HARDHAT_PRIVATE_KEY": "0x0123456789",
        },
    ), patch("web3.Web3.HTTPProvider"):
        web3_instance = _create_mock_web3_instance(is_connected=True)

        with patch(
            "backend.database_handler.transactions_processor.Web3",
            return_value=web3_instance,
        ):
            yield web3_instance


@pytest.fixture
def mock_env_and_web3_disconnected():
    with patch.dict(
        os.environ,
        {
            "HARDHAT_PORT": "8545",
            "HARDHAT_URL": "http://localhost",
            "HARDHAT_PRIVATE_KEY": "0x0123456789",
        },
    ), patch("web3.Web3.HTTPProvider"):
        web3_instance = _create_mock_web3_instance(is_connected=False)

        with patch(
            "backend.database_handler.transactions_processor.Web3",
            return_value=web3_instance,
        ):
            yield web3_instance


def test_transactions_processor(
    transactions_processor: TransactionsProcessor, mock_env_and_web3_connected
):
    # Override the web3 instance in the transactions_processor with our mock
    transactions_processor.web3 = mock_env_and_web3_connected

    import time

    from_address = "0x9F0e84243496AcFB3Cd99D02eA59673c05901501"
    to_address = "0xAcec3A6d871C25F591aBd4fC24054e524BBbF794"
    data = {"key": "value"}
    value = 2.0
    transaction_type = 1
    nonce = 0

    # Used to test the triggered_by field
    # Use explicit hash to avoid conflicts
    first_transaction_hash = transactions_processor.insert_transaction(
        from_address,
        to_address,
        data,
        value,
        transaction_type,
        nonce,
        True,
        3,
        None,
        transaction_hash=f"0x{'1' * 64}",  # Explicit unique hash
    )
    transactions_processor.session.commit()

    # Use different value to ensure different hash
    actual_transaction_hash = transactions_processor.insert_transaction(
        from_address,
        to_address,
        data,
        value * 2,  # Different value to get different hash
        transaction_type,
        nonce + 1,
        True,
        3,
        first_transaction_hash,
        transaction_hash=f"0x{'2' * 64}",  # Explicit unique hash
    )

    actual_transaction = transactions_processor.get_transaction_by_hash(
        actual_transaction_hash
    )

    assert actual_transaction["from_address"] == from_address
    assert actual_transaction["to_address"] == to_address
    assert actual_transaction["data"] == data
    assert math.isclose(actual_transaction["value"], value * 2)  # Updated value
    assert actual_transaction["type"] == transaction_type
    assert actual_transaction["status"] == TransactionStatus.PENDING.value
    assert actual_transaction["hash"] == actual_transaction_hash
    created_at = actual_transaction["created_at"]
    assert datetime.fromisoformat(created_at)
    assert actual_transaction["leader_only"] is True
    assert actual_transaction["triggered_by"] == first_transaction_hash
    new_status = TransactionStatus.ACCEPTED
    transactions_processor.update_transaction_status(
        actual_transaction_hash, new_status
    )

    actual_transaction = transactions_processor.get_transaction_by_hash(
        actual_transaction_hash
    )

    assert actual_transaction["status"] == new_status.value
    assert actual_transaction["hash"] == actual_transaction_hash
    assert actual_transaction["from_address"] == from_address
    assert actual_transaction["to_address"] == to_address
    assert actual_transaction["data"] == data
    assert math.isclose(actual_transaction["value"], value * 2)  # Updated value
    assert actual_transaction["type"] == transaction_type
    assert actual_transaction["created_at"] == created_at
    assert actual_transaction["leader_only"] is True

    consensus_data = {"result": "success"}
    transactions_processor.set_transaction_result(
        actual_transaction_hash, consensus_data
    )

    new_status = TransactionStatus.FINALIZED
    transactions_processor.update_transaction_status(
        actual_transaction_hash, new_status
    )

    actual_transaction = transactions_processor.get_transaction_by_hash(
        actual_transaction_hash
    )

    assert actual_transaction["status"] == TransactionStatus.FINALIZED.value
    assert actual_transaction["consensus_data"] == consensus_data
    assert actual_transaction["hash"] == actual_transaction_hash
    assert actual_transaction["from_address"] == from_address
    assert actual_transaction["to_address"] == to_address
    assert actual_transaction["data"] == data
    assert math.isclose(actual_transaction["value"], value * 2)  # Updated value
    assert actual_transaction["type"] == transaction_type
    assert actual_transaction["created_at"] == created_at


def test_get_highest_timestamp(transactions_processor: TransactionsProcessor):
    import time

    # Initially should return 0 when no transactions exist
    assert transactions_processor.get_highest_timestamp() == 0

    # Create some transactions with different timestamps
    from_address = "0x9F0e84243496AcFB3Cd99D02eA59673c05901501"
    to_address = "0xAcec3A6d871C25F591aBd4fC24054e524BBbF794"
    data = {"key": "value"}

    # First transaction with timestamp 1000
    tx1_hash = transactions_processor.insert_transaction(
        from_address,
        to_address,
        {"key": "value1"},
        1.0,
        1,
        0,
        True,
        3,
        transaction_hash=f"0x{'3' * 64}",
    )
    transactions_processor.session.commit()
    assert transactions_processor.get_highest_timestamp() == 0
    transactions_processor.set_transaction_timestamp_awaiting_finalization(
        tx1_hash, 1000
    )

    # Second transaction with timestamp 2000
    tx2_hash = transactions_processor.insert_transaction(
        from_address,
        to_address,
        {"key": "value2"},
        2.0,
        1,
        1,
        True,
        3,
        transaction_hash=f"0x{'4' * 64}",
    )
    transactions_processor.set_transaction_timestamp_awaiting_finalization(
        tx2_hash, 2000
    )

    # Third transaction with no timestamp (should be ignored)
    transactions_processor.insert_transaction(
        from_address,
        to_address,
        {"key": "value3"},
        3.0,
        1,
        2,
        True,
        3,
        transaction_hash=f"0x{'5' * 64}",
    )

    transactions_processor.session.commit()

    # Should return the highest timestamp (2000)
    assert transactions_processor.get_highest_timestamp() == 2000


def test_insert_transaction_duplicate_hash_returns_existing(
    transactions_processor: TransactionsProcessor,
):
    """Test that inserting a transaction with duplicate hash returns existing hash
    instead of raising UniqueViolation error (fixes GENLAYER-STUDIO-12).
    """
    from_address = "0x9F0e84243496AcFB3Cd99D02eA59673c05901501"
    to_address = "0xAcec3A6d871C25F591aBd4fC24054e524BBbF794"
    data = {"key": "value"}
    duplicate_hash = f"0x{'d' * 64}"

    # Insert first transaction
    first_result = transactions_processor.insert_transaction(
        from_address,
        to_address,
        data,
        1.0,
        1,
        0,
        True,
        3,
        transaction_hash=duplicate_hash,
    )
    transactions_processor.session.commit()

    assert first_result == duplicate_hash

    # Insert second transaction with same hash - should return existing hash, not error
    second_result = transactions_processor.insert_transaction(
        from_address,
        to_address,
        {"key": "different_value"},  # Different data
        2.0,  # Different value
        1,
        1,  # Different nonce
        True,
        3,
        transaction_hash=duplicate_hash,  # Same hash
    )

    # Should return the same hash without raising an error
    assert second_result == duplicate_hash

    # Session should still be usable (not in pending rollback state)
    transactions_processor.session.commit()

    # Verify only one transaction exists with this hash
    tx = transactions_processor.get_transaction_by_hash(duplicate_hash)
    assert tx is not None
    assert tx["data"] == data  # Original data, not the second attempt's data


def _insert_tx_with_consensus_history(session, tp, consensus_history_sql, suffix):
    """Insert a transaction then force consensus_history to a specific SQL value."""
    tx_hash = tp.insert_transaction(
        "0x9F0e84243496AcFB3Cd99D02eA59673c05901501",
        "0xAcec3A6d871C25F591aBd4fC24054e524BBbF794",
        {"key": "value"},
        1.0,
        1,
        0,
        True,
        3,
        transaction_hash=f"0x{suffix * 64}",
    )
    session.commit()
    session.execute(
        text(
            f"UPDATE transactions SET consensus_history = {consensus_history_sql} WHERE hash = :hash"
        ),
        {"hash": tx_hash},
    )
    session.commit()
    return tx_hash


class TestConsensusHistoryJsonbEdgeCases:
    """Regression tests for jsonb_set on consensus_history with non-object values.

    In prod, consensus_history can be SQL NULL (no default in model) or JSONB
    null (a valid JSONB scalar). COALESCE only catches SQL NULL, so jsonb_set
    on JSONB null crashes with "cannot set path in scalar".
    """

    def test_update_transaction_status_with_sql_null(
        self, transactions_processor, session
    ):
        tx_hash = _insert_tx_with_consensus_history(
            session, transactions_processor, "NULL", "a"
        )
        transactions_processor.update_transaction_status(
            tx_hash, TransactionStatus.PROPOSING
        )
        tx = transactions_processor.get_transaction_by_hash(tx_hash)
        assert tx["status"] == TransactionStatus.PROPOSING.value

    def test_update_transaction_status_with_jsonb_null(
        self, transactions_processor, session
    ):
        tx_hash = _insert_tx_with_consensus_history(
            session, transactions_processor, "'null'::jsonb", "b"
        )
        transactions_processor.update_transaction_status(
            tx_hash, TransactionStatus.PROPOSING
        )
        tx = transactions_processor.get_transaction_by_hash(tx_hash)
        assert tx["status"] == TransactionStatus.PROPOSING.value

    def test_update_transaction_status_with_empty_object(
        self, transactions_processor, session
    ):
        tx_hash = _insert_tx_with_consensus_history(
            session, transactions_processor, "'{}'::jsonb", "c"
        )
        transactions_processor.update_transaction_status(
            tx_hash, TransactionStatus.PROPOSING
        )
        tx = transactions_processor.get_transaction_by_hash(tx_hash)
        assert tx["status"] == TransactionStatus.PROPOSING.value
        assert "PROPOSING" in tx["consensus_history"]["current_status_changes"]

    def test_add_state_timestamp_with_sql_null(self, transactions_processor, session):
        tx_hash = _insert_tx_with_consensus_history(
            session, transactions_processor, "NULL", "e"
        )
        transactions_processor.add_state_timestamp(tx_hash, "PENDING")
        tx = transactions_processor.get_transaction_by_hash(tx_hash)
        assert "PENDING" in tx["consensus_history"]["current_monitoring"]

    def test_add_state_timestamp_with_jsonb_null(self, transactions_processor, session):
        tx_hash = _insert_tx_with_consensus_history(
            session, transactions_processor, "'null'::jsonb", "f"
        )
        transactions_processor.add_state_timestamp(tx_hash, "PENDING")
        tx = transactions_processor.get_transaction_by_hash(tx_hash)
        assert "PENDING" in tx["consensus_history"]["current_monitoring"]

    def test_add_state_timestamp_with_empty_object(
        self, transactions_processor, session
    ):
        tx_hash = _insert_tx_with_consensus_history(
            session, transactions_processor, "'{}'::jsonb", "g"
        )
        transactions_processor.add_state_timestamp(tx_hash, "PROPOSING")
        tx = transactions_processor.get_transaction_by_hash(tx_hash)
        assert "PROPOSING" in tx["consensus_history"]["current_monitoring"]


# ---------------------------------------------------------------------------
# Mock Receipt for update_consensus_history tests
# ---------------------------------------------------------------------------


class _MockReceipt:
    def to_dict(self, strip_contract_state=False):
        return {"vote": "agree", "result": "ok"}


# ---------------------------------------------------------------------------
# New comprehensive tests
# ---------------------------------------------------------------------------


class TestTransactionStatusUpdates:
    def test_update_status_pending_to_proposing(self, tp):
        tx_hash = _make_tx(tp)
        tp.update_transaction_status(tx_hash, TransactionStatus.PROPOSING)
        tx = tp.get_transaction_by_hash(tx_hash)
        assert tx["status"] == TransactionStatus.PROPOSING.value

    def test_update_status_records_in_consensus_history(self, tp):
        tx_hash = _make_tx(tp)
        tp.update_transaction_status(tx_hash, TransactionStatus.PROPOSING)
        tp.session.expire_all()
        tx = tp.get_transaction_by_hash(tx_hash)
        changes = tx["consensus_history"]["current_status_changes"]
        assert changes == ["PENDING", "PROPOSING"]

    def test_update_status_appends_to_existing_status_changes(self, tp):
        tx_hash = _make_tx(tp)
        tp.update_transaction_status(tx_hash, TransactionStatus.PROPOSING)
        tp.update_transaction_status(tx_hash, TransactionStatus.COMMITTING)
        tp.session.expire_all()
        tx = tp.get_transaction_by_hash(tx_hash)
        changes = tx["consensus_history"]["current_status_changes"]
        assert changes == ["PENDING", "PROPOSING", "COMMITTING"]


class TestConsensusHistory:
    def test_update_consensus_history_empty_initial(self, tp, session):
        tx_hash = _make_tx(tp)
        tp.update_consensus_history(
            tx_hash,
            ConsensusRound.ACCEPTED,
            None,
            [],
        )
        tp.session.expire_all()
        tx = tp.get_transaction_by_hash(tx_hash)
        results = tx["consensus_history"]["consensus_results"]
        assert len(results) == 1
        assert results[0]["consensus_round"] == ConsensusRound.ACCEPTED.value
        assert results[0]["leader_result"] is None
        assert results[0]["validator_results"] == []

    def test_update_consensus_history_appends_rounds(self, tp, session):
        tx_hash = _make_tx(tp)
        tp.update_consensus_history(tx_hash, ConsensusRound.ACCEPTED, None, [])
        tp.update_consensus_history(
            tx_hash, ConsensusRound.LEADER_ROTATION, [_MockReceipt()], [_MockReceipt()]
        )
        tp.session.expire_all()
        tx = tp.get_transaction_by_hash(tx_hash)
        results = tx["consensus_history"]["consensus_results"]
        assert len(results) == 2
        assert results[1]["consensus_round"] == ConsensusRound.LEADER_ROTATION.value

    def test_reset_consensus_history(self, tp):
        tx_hash = _make_tx(tp)
        tp.update_transaction_status(tx_hash, TransactionStatus.PROPOSING)
        tp.reset_consensus_history(tx_hash)
        tp.session.expire_all()
        tx = tp.get_transaction_by_hash(tx_hash)
        assert tx["consensus_history"] == {}


class TestTransactionResult:
    def test_set_transaction_result(self, tp):
        tx_hash = _make_tx(tp)
        tp.set_transaction_result(tx_hash, {"result": "success"})
        tp.session.expire_all()
        tx = tp.get_transaction_by_hash(tx_hash)
        assert tx["consensus_data"]["result"] == "success"

    def test_set_transaction_result_overwrites(self, tp):
        tx_hash = _make_tx(tp)
        tp.set_transaction_result(tx_hash, {"result": "first"})
        tp.set_transaction_result(tx_hash, {"result": "second"})
        tp.session.expire_all()
        tx = tp.get_transaction_by_hash(tx_hash)
        assert tx["consensus_data"]["result"] == "second"


class TestTransactionTimestamps:
    def test_set_timestamp_awaiting_finalization(self, tp):
        tx_hash = _make_tx(tp)
        tp.set_transaction_timestamp_awaiting_finalization(tx_hash, 12345)
        tp.session.commit()
        tp.session.expire_all()
        tx = tp.get_transaction_by_hash(tx_hash)
        assert tx["timestamp_awaiting_finalization"] == 12345

    def test_get_highest_timestamp_ignores_null(self, tp):
        tx1 = _make_tx(tp)
        tx2 = _make_tx(tp)
        tp.set_transaction_timestamp_awaiting_finalization(tx1, 9999)
        tp.session.commit()
        # tx2 has no timestamp set
        assert tp.get_highest_timestamp() == 9999


class TestTransactionAppeal:
    def test_set_transaction_appeal_fields(self, tp):
        tx_hash = _make_tx(tp)
        # Must be in appealable status for appeal=True to take effect
        tp.update_transaction_status(tx_hash, TransactionStatus.ACCEPTED)
        tp.set_transaction_appeal(tx_hash, True)
        tp.session.expire_all()
        tx = tp.get_transaction_by_hash(tx_hash)
        assert tx["appealed"] is True
        assert tx["timestamp_appeal"] is not None

    def test_set_transaction_appeal_failed(self, tp):
        tx_hash = _make_tx(tp)
        tp.set_transaction_appeal_failed(tx_hash, 2)
        tp.session.expire_all()
        tx = tp.get_transaction_by_hash(tx_hash)
        assert tx["appeal_failed"] == 2

    def test_set_transaction_appeal_validators_timeout(self, tp):
        tx_hash = _make_tx(tp)
        tp.set_transaction_appeal_validators_timeout(tx_hash, True)
        tp.session.expire_all()
        tx = tp.get_transaction_by_hash(tx_hash)
        assert tx["appeal_validators_timeout"] is True


class TestStateTimestamp:
    def test_add_state_timestamp_new_key(self, tp):
        tx_hash = _make_tx(tp)
        tp.add_state_timestamp(tx_hash, "PROPOSING")
        tp.session.expire_all()
        tx = tp.get_transaction_by_hash(tx_hash)
        monitoring = tx["consensus_history"]["current_monitoring"]
        assert "PROPOSING" in monitoring
        assert isinstance(monitoring["PROPOSING"], float)

    def test_add_state_timestamp_overwrites(self, tp):
        tx_hash = _make_tx(tp)
        tp.add_state_timestamp(tx_hash, "PROPOSING")
        tp.session.expire_all()
        tx1 = tp.get_transaction_by_hash(tx_hash)
        ts1 = tx1["consensus_history"]["current_monitoring"]["PROPOSING"]

        import time

        time.sleep(0.05)  # ensure different timestamp

        tp.add_state_timestamp(tx_hash, "PROPOSING")
        tp.session.expire_all()
        tx2 = tp.get_transaction_by_hash(tx_hash)
        ts2 = tx2["consensus_history"]["current_monitoring"]["PROPOSING"]
        assert ts2 >= ts1


class TestGetTransactions:
    def test_get_transaction_by_hash(self, tp):
        tx_hash = _make_tx(tp, value=42.0)
        tx = tp.get_transaction_by_hash(tx_hash)
        assert tx is not None
        assert tx["hash"] == tx_hash
        assert tx["from_address"] == "0x9F0e84243496AcFB3Cd99D02eA59673c05901501"
        assert tx["value"] == 42.0

    def test_get_transaction_by_hash_not_found(self, tp):
        result = tp.get_transaction_by_hash("0x" + "ff" * 32)
        assert result is None

    def test_get_transactions_for_address(self, tp):
        addr_a = "0xAcec3A6d871C25F591aBd4fC24054e524BBbF794"
        addr_b = "0x1111111111111111111111111111111111111111"
        _make_tx(tp, to_address=addr_a)
        _make_tx(tp, to_address=addr_a)
        _make_tx(tp, to_address=addr_b)
        txs = tp.get_transactions_for_address(addr_a, TransactionAddressFilter.TO)
        assert len(txs) == 2
        for t in txs:
            assert t["to_address"] == addr_a


class TestContractSnapshot:
    def test_set_transaction_contract_snapshot(self, tp):
        tx_hash = _make_tx(tp)
        snapshot = {"state": "deployed", "code": "abc"}
        tp.set_transaction_contract_snapshot(tx_hash, snapshot)
        tp.session.expire_all()
        tx = tp.get_transaction_by_hash(tx_hash)
        assert tx["contract_snapshot"] == snapshot


class TestRotationCount:
    def test_increase_transaction_rotation_count(self, tp, session):
        tx_hash = _make_tx(tp)
        tp.increase_transaction_rotation_count(tx_hash)
        tp.session.expire_all()
        row = session.execute(
            text("SELECT rotation_count FROM transactions WHERE hash = :h"),
            {"h": tx_hash},
        ).one()
        assert row[0] == 1

    def test_reset_transaction_rotation_count(self, tp, session):
        tx_hash = _make_tx(tp)
        tp.increase_transaction_rotation_count(tx_hash)
        tp.reset_transaction_rotation_count(tx_hash)
        tp.session.expire_all()
        row = session.execute(
            text("SELECT rotation_count FROM transactions WHERE hash = :h"),
            {"h": tx_hash},
        ).one()
        assert row[0] == 0
