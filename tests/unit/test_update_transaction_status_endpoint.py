import pytest
from unittest.mock import Mock, patch

from backend.protocol_rpc.exceptions import JSONRPCError

from backend.protocol_rpc.endpoints import update_transaction_status
from backend.database_handler.models import TransactionStatus
from backend.database_handler.transactions_processor import TransactionsProcessor


class TestUpdateTransactionStatusEndpoint:
    """Test cases for the update_transaction_status RPC endpoint."""

    def setup_method(self):
        """Set up test fixtures."""
        self.session: Mock = Mock(name="Session")
        self.mock_transactions_processor = Mock(spec=TransactionsProcessor)
        self.transactions_processor_patch = patch(
            "backend.protocol_rpc.endpoints.TransactionsProcessor",
            return_value=self.mock_transactions_processor,
        )
        self.transactions_processor_patch.start()
        self.valid_tx_hash = (
            "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
        )
        self.valid_status = TransactionStatus.FINALIZED.value
        self.mock_transaction_data = {
            "hash": self.valid_tx_hash,
            "status": self.valid_status,
            "from_address": "0x123",
            "to_address": "0x456",
            "data": {"test": "data"},
            "value": 1.0,
            "type": 1,
            "created_at": "2023-01-01T00:00:00",
        }

    def teardown_method(self):
        self.transactions_processor_patch.stop()

    def test_valid_transaction_hash_and_status(self):
        """Test successful update with valid inputs."""
        self.mock_transactions_processor.update_transaction_status.return_value = None
        self.mock_transactions_processor.get_transaction_by_hash.return_value = (
            self.mock_transaction_data
        )

        result = update_transaction_status(
            self.session, self.valid_tx_hash, self.valid_status
        )

        self.mock_transactions_processor.update_transaction_status.assert_called_once_with(
            transaction_hash=self.valid_tx_hash,
            new_status=TransactionStatus.FINALIZED,
            update_current_status_changes=True,
        )
        self.mock_transactions_processor.get_transaction_by_hash.assert_called_once_with(
            self.valid_tx_hash
        )

        assert result == self.mock_transaction_data

    def test_transaction_not_found_after_update(self):
        """Test error when transaction is not found after update."""
        self.mock_transactions_processor.update_transaction_status.return_value = None
        self.mock_transactions_processor.get_transaction_by_hash.return_value = None

        with pytest.raises(JSONRPCError) as exc_info:
            update_transaction_status(
                self.session, self.valid_tx_hash, self.valid_status
            )

        assert exc_info.value.code == -32602
        assert "Transaction not found" in exc_info.value.message
        assert self.valid_tx_hash in exc_info.value.message

    def test_invalid_transaction_hash_empty_string(self):
        """Test validation error for empty transaction hash."""
        with pytest.raises(JSONRPCError) as exc_info:
            update_transaction_status(self.session, "", self.valid_status)

        assert exc_info.value.code == -32602
        assert (
            "Invalid transaction hash: must be a non-empty string"
            in exc_info.value.message
        )

    def test_invalid_transaction_hash_none(self):
        """Test validation error for None transaction hash."""
        with pytest.raises(JSONRPCError) as exc_info:
            update_transaction_status(self.session, None, self.valid_status)

        assert exc_info.value.code == -32602
        assert (
            "Invalid transaction hash: must be a non-empty string"
            in exc_info.value.message
        )

    def test_invalid_transaction_hash_wrong_length(self):
        """Test validation error for transaction hash with wrong length."""
        short_hash = "0x123"
        with pytest.raises(JSONRPCError) as exc_info:
            update_transaction_status(self.session, short_hash, self.valid_status)

        assert exc_info.value.code == -32602
        assert (
            "Invalid transaction hash format: must be a 66-character hex string starting with '0x'"
            in exc_info.value.message
        )

    def test_invalid_transaction_hash_no_prefix(self):
        """Test validation error for transaction hash without 0x prefix."""
        no_prefix_hash = (
            "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
        )
        with pytest.raises(JSONRPCError) as exc_info:
            update_transaction_status(self.session, no_prefix_hash, self.valid_status)

        assert exc_info.value.code == -32602
        assert (
            "Invalid transaction hash format: must be a 66-character hex string starting with '0x'"
            in exc_info.value.message
        )

    def test_invalid_transaction_hash_non_hex_characters(self):
        """Test validation error for transaction hash with non-hex characters."""
        invalid_hash = (
            "0x123456789gabcdef1234567890abcdef1234567890abcdef1234567890abcdef"
        )
        with pytest.raises(JSONRPCError) as exc_info:
            update_transaction_status(self.session, invalid_hash, self.valid_status)

        assert exc_info.value.code == -32602
        assert (
            "Invalid transaction hash format: contains non-hexadecimal characters"
            in exc_info.value.message
        )

    def test_invalid_status_empty_string(self):
        """Test validation error for empty status."""
        with pytest.raises(JSONRPCError) as exc_info:
            update_transaction_status(self.session, self.valid_tx_hash, "")

        assert exc_info.value.code == -32602
        assert "Invalid status: must be a non-empty string" in exc_info.value.message

    def test_invalid_status_none(self):
        """Test validation error for None status."""
        with pytest.raises(JSONRPCError) as exc_info:
            update_transaction_status(self.session, self.valid_tx_hash, None)

        assert exc_info.value.code == -32602
        assert "Invalid status: must be a non-empty string" in exc_info.value.message

    def test_invalid_status_not_in_enum(self):
        """Test validation error for status not in TransactionStatus enum."""
        invalid_status = "INVALID_STATUS"
        with pytest.raises(JSONRPCError) as exc_info:
            update_transaction_status(self.session, self.valid_tx_hash, invalid_status)

        assert exc_info.value.code == -32602
        assert f"Invalid status '{invalid_status}'" in exc_info.value.message
        assert "must be one of" in exc_info.value.message

    def test_all_valid_transaction_statuses(self):
        """Test that all valid TransactionStatus enum values are accepted."""
        self.mock_transactions_processor.update_transaction_status.return_value = None
        self.mock_transactions_processor.get_transaction_by_hash.return_value = (
            self.mock_transaction_data
        )

        for status in TransactionStatus:
            self.mock_transactions_processor.reset_mock()
            self.mock_transactions_processor.get_transaction_by_hash.return_value = (
                self.mock_transaction_data
            )

            result = update_transaction_status(
                self.session, self.valid_tx_hash, status.value
            )

            self.mock_transactions_processor.update_transaction_status.assert_called_once_with(
                transaction_hash=self.valid_tx_hash,
                new_status=status,
                update_current_status_changes=True,
            )
            assert result == self.mock_transaction_data

    def test_decorator_blocks_calls_in_hosted_mode(self, monkeypatch):
        """Ensure the hosted-studio guard rejects update attempts when enabled."""

        monkeypatch.setenv("VITE_IS_HOSTED", "true")

        with pytest.raises(JSONRPCError) as exc_info:
            update_transaction_status(
                self.session, self.valid_tx_hash, self.valid_status
            )

        assert exc_info.value.code == -32000
        assert exc_info.value.message == "Non-allowed operation"
        self.mock_transactions_processor.update_transaction_status.assert_not_called()

    def test_edge_case_exactly_66_characters(self):
        """Test that exactly 66-character hex string is valid."""
        valid_66_char_hash = "0x" + "a" * 64  # 0x + 64 hex chars = 66 total
        self.mock_transactions_processor.update_transaction_status.return_value = None
        self.mock_transactions_processor.get_transaction_by_hash.return_value = (
            self.mock_transaction_data
        )

        result = update_transaction_status(
            self.session, valid_66_char_hash, self.valid_status
        )

        assert result == self.mock_transaction_data

    def test_edge_case_65_characters(self):
        """Test that 65-character string is invalid."""
        invalid_65_char_hash = "0x" + "a" * 63  # 0x + 63 hex chars = 65 total
        with pytest.raises(JSONRPCError) as exc_info:
            update_transaction_status(
                self.session,
                invalid_65_char_hash,
                self.valid_status,
            )

        assert exc_info.value.code == -32602
        assert "must be a 66-character hex string" in exc_info.value.message

    def test_edge_case_67_characters(self):
        """Test that 67-character string is invalid."""
        invalid_67_char_hash = "0x" + "a" * 65  # 0x + 65 hex chars = 67 total
        with pytest.raises(JSONRPCError) as exc_info:
            update_transaction_status(
                self.session,
                invalid_67_char_hash,
                self.valid_status,
            )

        assert exc_info.value.code == -32602
        assert "must be a 66-character hex string" in exc_info.value.message
