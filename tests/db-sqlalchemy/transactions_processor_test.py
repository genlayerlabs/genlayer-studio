from sqlalchemy.orm import Session
import pytest
from unittest.mock import patch, MagicMock
import os
import math
from datetime import datetime
from web3 import Web3
from web3.providers import BaseProvider

from backend.database_handler.chain_snapshot import ChainSnapshot
from backend.database_handler.models import Transactions
from backend.database_handler.transactions_processor import TransactionStatus
from backend.database_handler.transactions_processor import TransactionsProcessor


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
