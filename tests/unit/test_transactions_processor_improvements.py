"""Unit tests for improved methods in transactions_processor.py"""

from unittest.mock import Mock, patch
from backend.database_handler.transactions_processor import TransactionsProcessor
from backend.database_handler.models import Transactions
from sqlalchemy.orm import Session


class TestGetTransactionCount:
    """Test the improved get_transaction_count method"""

    def setup_method(self, method):
        """Set up test fixtures"""
        self.mock_session = Mock(spec=Session)
        self.mock_web3 = Mock()
        self.processor = TransactionsProcessor(Mock())
        self.processor.web3 = self.mock_web3
        self.processor.session = self.mock_session

    def test_get_transaction_count_with_checksum_address(self):
        """Test get_transaction_count with address normalization"""
        # Setup
        test_address = "0xabcdef1234567890abcdef1234567890abcdef12"
        checksum_address = "0xABcdEF1234567890aBcDef1234567890AbCdEf12"

        self.mock_web3.to_checksum_address.return_value = checksum_address
        
        # Mock database query
        mock_query = Mock()
        mock_query.filter.return_value = mock_query
        mock_query.count.return_value = 5
        self.mock_session.query.return_value = mock_query

        # Execute
        result = self.processor.get_transaction_count(test_address)

        # Verify
        self.mock_web3.to_checksum_address.assert_called_once_with(test_address)
        self.mock_session.query.assert_called_once_with(Transactions)
        mock_query.filter.assert_called_once()
        # Verify the filter checks from_address with checksum address
        filter_call = mock_query.filter.call_args[0][0]
        assert str(filter_call.left) == "transactions.from_address"
        assert str(filter_call.right.value) == checksum_address
        assert result == 5

    def test_get_transaction_count_with_invalid_address(self):
        """Test get_transaction_count with invalid address that can't be checksummed"""
        # Setup
        test_address = "invalid_address"

        self.mock_web3.to_checksum_address.side_effect = Exception("Invalid address")
        
        # Mock database query with original address
        mock_query = Mock()
        mock_query.filter.return_value = mock_query
        mock_query.count.return_value = 3
        self.mock_session.query.return_value = mock_query

        # Execute
        result = self.processor.get_transaction_count(test_address)

        # Verify - should use original address after checksum fails
        self.mock_web3.to_checksum_address.assert_called_once_with(test_address)
        self.mock_session.query.assert_called_once_with(Transactions)
        mock_query.filter.assert_called_once()
        # Verify the filter uses the original address since checksum failed
        filter_call = mock_query.filter.call_args[0][0]
        assert str(filter_call.left) == "transactions.from_address"
        assert str(filter_call.right.value) == test_address
        assert result == 3

    def test_get_transaction_count_returns_zero_when_no_transactions(self):
        """Test get_transaction_count returns 0 when no transactions exist"""
        # Setup
        test_address = "0xABcdEF1234567890aBcDef1234567890AbCdEf12"

        self.mock_web3.to_checksum_address.return_value = test_address
        
        # Mock database query returning 0
        mock_query = Mock()
        mock_query.filter.return_value = mock_query
        mock_query.count.return_value = 0
        self.mock_session.query.return_value = mock_query

        # Execute
        result = self.processor.get_transaction_count(test_address)

        # Verify
        assert result == 0
        self.mock_session.query.assert_called_once_with(Transactions)
        mock_query.filter.assert_called_once()
        mock_query.count.assert_called_once()

    def test_get_transaction_count_with_multiple_transactions(self):
        """Test get_transaction_count correctly counts multiple transactions"""
        # Setup
        test_address = "0xABcdEF1234567890aBcDef1234567890AbCdEf12"

        self.mock_web3.to_checksum_address.return_value = test_address

        # Mock database query returning count of 7
        mock_query = Mock()
        mock_query.filter.return_value = mock_query
        mock_query.count.return_value = 7
        self.mock_session.query.return_value = mock_query

        # Execute
        result = self.processor.get_transaction_count(test_address)

        # Verify database is queried and correct count returned
        assert result == 7
        self.mock_session.query.assert_called_once_with(Transactions)
        mock_query.filter.assert_called_once()
        mock_query.count.assert_called_once()


    def test_get_transaction_count_database_query_structure(self):
        """Test that get_transaction_count queries database with correct structure"""
        # Setup
        test_address = "0xABcdEF1234567890aBcDef1234567890AbCdEf12"

        self.mock_web3.to_checksum_address.return_value = test_address

        # Mock database query
        mock_query = Mock()
        mock_filter = Mock()
        mock_query.filter.return_value = mock_filter
        mock_filter.count.return_value = 8
        self.mock_session.query.return_value = mock_query

        # Execute
        result = self.processor.get_transaction_count(test_address)
        
        # Verify correct database query structure
        self.mock_session.query.assert_called_once_with(Transactions)
        mock_query.filter.assert_called_once()
        mock_filter.count.assert_called_once()
        assert result == 8


class TestSetTransactionAppealProcessingTime:
    """Test the improved set_transaction_appeal_processing_time method"""

    def setup_method(self, method):
        """Set up test fixtures"""
        self.mock_session = Mock(spec=Session)
        self.processor = TransactionsProcessor(Mock())
        self.processor.session = self.mock_session

    def test_appeal_processing_time_with_none_timestamp(self):
        """Test set_transaction_appeal_processing_time with None timestamp_appeal"""
        # Setup
        mock_transaction = Mock(spec=Transactions)
        mock_transaction.timestamp_appeal = None
        mock_transaction.appeal_processing_time = 0

        mock_query = Mock()
        mock_query.filter_by.return_value = mock_query
        mock_query.first.return_value = mock_transaction
        self.mock_session.query.return_value = mock_query

        # Execute
        with patch("builtins.print") as mock_print:
            self.processor.set_transaction_appeal_processing_time("test_hash")

        # Verify - should not update and should print message
        assert mock_transaction.appeal_processing_time == 0  # Unchanged
        self.mock_session.commit.assert_not_called()
        mock_print.assert_called_once()
        assert "has no timestamp_appeal" in str(mock_print.call_args)

    def test_appeal_processing_time_transaction_not_found(self):
        """Test set_transaction_appeal_processing_time when transaction doesn't exist"""
        # Setup
        mock_query = Mock()
        mock_query.filter_by.return_value = mock_query
        mock_query.first.return_value = None
        self.mock_session.query.return_value = mock_query

        # Execute
        with patch("builtins.print") as mock_print:
            self.processor.set_transaction_appeal_processing_time("nonexistent_hash")

        # Verify
        self.mock_session.commit.assert_not_called()
        mock_print.assert_called_once()
        assert "not found" in str(mock_print.call_args)
