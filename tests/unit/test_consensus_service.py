"""Unit tests for consensus_service.py"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from backend.rollup.consensus_service import (
    ConsensusService,
    NonceError,
    NonceTooLowError,
    NonceTooHighError,
)


class TestNonceExceptions:
    """Test custom nonce exception classes"""

    def test_nonce_too_low_error_initialization(self):
        """Test NonceTooLowError initialization and attributes"""
        error = NonceTooLowError(expected_nonce=5, actual_nonce=3)

        assert error.expected_nonce == 5
        assert error.actual_nonce == 3
        assert str(error) == "Nonce too low: expected 5, got 3"
        assert isinstance(error, NonceError)
        assert isinstance(error, Exception)

    def test_nonce_too_high_error_initialization(self):
        """Test NonceTooHighError initialization and attributes"""
        error = NonceTooHighError(expected_nonce=3, actual_nonce=5)

        assert error.expected_nonce == 3
        assert error.actual_nonce == 5
        assert str(error) == "Nonce too high: expected 3, got 5"
        assert isinstance(error, NonceError)
        assert isinstance(error, Exception)

    def test_nonce_error_with_chaining(self):
        """Test exception chaining with 'from' clause"""
        original_error = ValueError("Original error")

        try:
            raise NonceTooLowError(5, 3) from original_error
        except NonceTooLowError as e:
            assert e.__cause__ == original_error
            assert e.expected_nonce == 5
            assert e.actual_nonce == 3

    def test_nonce_error_inheritance(self):
        """Test that NonceError is properly inherited"""
        assert issubclass(NonceTooLowError, NonceError)
        assert issubclass(NonceTooHighError, NonceError)
        assert issubclass(NonceError, Exception)


class TestConsensusService:
    """Test ConsensusService class methods"""

    @patch("backend.rollup.consensus_service.Web3")
    @patch.dict(
        "os.environ", {"HARDHAT_PORT": "8545", "HARDHAT_URL": "http://localhost"}
    )
    def test_consensus_service_initialization(self, mock_web3):
        """Test ConsensusService initialization"""
        mock_web3_instance = Mock()
        mock_web3_instance.is_connected.return_value = True
        mock_web3.return_value = mock_web3_instance

        service = ConsensusService()

        assert service.web3 == mock_web3_instance
        assert service.web3_connected is True
        mock_web3.assert_called_once()
        mock_web3_instance.is_connected.assert_called_once()

    @patch("backend.rollup.consensus_service.Web3")
    @patch.dict(
        "os.environ", {"HARDHAT_PORT": "8545", "HARDHAT_URL": "http://localhost"}
    )
    def test_add_transaction_nonce_too_low(self, mock_web3):
        """Test add_transaction with nonce too low error"""
        mock_web3_instance = Mock()
        mock_web3_instance.is_connected.return_value = True
        mock_web3.return_value = mock_web3_instance

        service = ConsensusService()

        # Mock forward_transaction to raise an error
        with patch.object(service, "forward_transaction") as mock_forward:
            mock_forward.side_effect = Exception(
                "Expected nonce to be 5 but got 3. Nonce too low"
            )

            with pytest.raises(NonceTooLowError) as exc_info:
                service.add_transaction({"data": "test"}, "0x123")

            assert exc_info.value.expected_nonce == 5
            assert exc_info.value.actual_nonce == 3

    @patch("backend.rollup.consensus_service.Web3")
    @patch.dict(
        "os.environ", {"HARDHAT_PORT": "8545", "HARDHAT_URL": "http://localhost"}
    )
    def test_add_transaction_nonce_too_high(self, mock_web3):
        """Test add_transaction with nonce too high error"""
        mock_web3_instance = Mock()
        mock_web3_instance.is_connected.return_value = True
        mock_web3.return_value = mock_web3_instance

        service = ConsensusService()

        # Mock forward_transaction to raise an error
        with patch.object(service, "forward_transaction") as mock_forward:
            mock_forward.side_effect = Exception(
                "Expected nonce to be 3 but got 5. Nonce too high"
            )

            with pytest.raises(NonceTooHighError) as exc_info:
                service.add_transaction({"data": "test"}, "0x123")

            assert exc_info.value.expected_nonce == 3
            assert exc_info.value.actual_nonce == 5

    @patch("backend.rollup.consensus_service.Web3")
    @patch.dict(
        "os.environ", {"HARDHAT_PORT": "8545", "HARDHAT_URL": "http://localhost"}
    )
    def test_add_transaction_generic_error(self, mock_web3):
        """Test add_transaction with generic error"""
        mock_web3_instance = Mock()
        mock_web3_instance.is_connected.return_value = True
        mock_web3.return_value = mock_web3_instance

        service = ConsensusService()

        # Mock forward_transaction to raise a generic error
        with patch.object(service, "forward_transaction") as mock_forward:
            mock_forward.side_effect = Exception("Some other error")

            with pytest.raises(Exception) as exc_info:
                service.add_transaction({"data": "test"}, "0x123")

            assert "Transaction failed: Some other error" in str(exc_info.value)

    @patch("backend.rollup.consensus_service.Web3")
    @patch.dict(
        "os.environ", {"HARDHAT_PORT": "8545", "HARDHAT_URL": "http://localhost"}
    )
    def test_add_transaction_not_connected(self, mock_web3):
        """Test add_transaction when not connected"""
        mock_web3_instance = Mock()
        mock_web3_instance.is_connected.return_value = False
        mock_web3.return_value = mock_web3_instance

        service = ConsensusService()

        result = service.add_transaction({"data": "test"}, "0x123")

        assert result is None
