"""
Unit tests for send_raw_transaction endpoint functionality.
Tests the sim_config validation that raises non-allowed operation in hosted studio.
"""

import pytest
import os
from unittest.mock import MagicMock, patch
from flask_jsonrpc.exceptions import JSONRPCError
from backend.protocol_rpc.endpoints import (
    send_raw_transaction,
    raise_non_allowed_in_hosted_studio,
)


class TestSendRawTransaction:

    @pytest.fixture
    def mock_dependencies(self):
        """Mock all dependencies for send_raw_transaction"""
        return {
            "transactions_processor": MagicMock(),
            "msg_handler": MagicMock(),
            "accounts_manager": MagicMock(),
            "transactions_parser": MagicMock(),
            "consensus_service": MagicMock(),
        }

    @pytest.fixture
    def valid_signed_transaction(self):
        """Mock valid signed transaction"""
        return "0x123abc"

    def test_send_raw_transaction_with_sim_config_in_hosted_environment(
        self, mock_dependencies, valid_signed_transaction
    ):
        """Test that sim_config triggers raise_non_allowed_in_hosted_studio when VITE_IS_HOSTED=true"""
        with patch.dict(os.environ, {"VITE_IS_HOSTED": "true"}):
            with pytest.raises(JSONRPCError) as exc_info:
                send_raw_transaction(
                    transactions_processor=mock_dependencies["transactions_processor"],
                    msg_handler=mock_dependencies["msg_handler"],
                    accounts_manager=mock_dependencies["accounts_manager"],
                    transactions_parser=mock_dependencies["transactions_parser"],
                    consensus_service=mock_dependencies["consensus_service"],
                    signed_rollup_transaction=valid_signed_transaction,
                    sim_config={"some": "config"},
                )

            assert exc_info.value.code == -32000
            assert exc_info.value.message == "Non-allowed operation"
            assert exc_info.value.data == {}

    def test_send_raw_transaction_with_sim_config_in_non_hosted_environment(
        self, mock_dependencies, valid_signed_transaction
    ):
        """Test that sim_config does not raise error when VITE_IS_HOSTED is not true"""
        # Mock the transaction parser to return a valid decoded transaction
        mock_decoded_transaction = MagicMock()
        mock_decoded_transaction.from_address = "0x123"
        mock_decoded_transaction.value = 100
        mock_decoded_transaction.data = "test_data"
        mock_dependencies[
            "transactions_parser"
        ].decode_signed_transaction.return_value = mock_decoded_transaction
        mock_dependencies["accounts_manager"].is_valid_address.return_value = True
        mock_dependencies[
            "transactions_parser"
        ].transaction_has_valid_signature.return_value = True
        mock_dependencies["transactions_processor"].insert_transaction.return_value = (
            "tx_hash"
        )

        with patch.dict(os.environ, {"VITE_IS_HOSTED": "false"}):
            # Should not raise an exception
            result = send_raw_transaction(
                transactions_processor=mock_dependencies["transactions_processor"],
                msg_handler=mock_dependencies["msg_handler"],
                accounts_manager=mock_dependencies["accounts_manager"],
                transactions_parser=mock_dependencies["transactions_parser"],
                consensus_service=mock_dependencies["consensus_service"],
                signed_rollup_transaction=valid_signed_transaction,
                sim_config={"some": "config"},
            )

            # Verify the function continues execution
            mock_dependencies[
                "transactions_parser"
            ].decode_signed_transaction.assert_called_once()

    def test_send_raw_transaction_without_sim_config_in_hosted_environment(
        self, mock_dependencies, valid_signed_transaction
    ):
        """Test that send_raw_transaction works without sim_config even in hosted environment"""
        # Mock the transaction parser to return a valid decoded transaction
        mock_decoded_transaction = MagicMock()
        mock_decoded_transaction.from_address = "0x123"
        mock_decoded_transaction.value = 100
        mock_decoded_transaction.data = "test_data"
        mock_dependencies[
            "transactions_parser"
        ].decode_signed_transaction.return_value = mock_decoded_transaction
        mock_dependencies["accounts_manager"].is_valid_address.return_value = True
        mock_dependencies[
            "transactions_parser"
        ].transaction_has_valid_signature.return_value = True
        mock_dependencies["transactions_processor"].insert_transaction.return_value = (
            "tx_hash"
        )

        with patch.dict(os.environ, {"VITE_IS_HOSTED": "true"}):
            # Should not raise an exception when sim_config is None
            result = send_raw_transaction(
                transactions_processor=mock_dependencies["transactions_processor"],
                msg_handler=mock_dependencies["msg_handler"],
                accounts_manager=mock_dependencies["accounts_manager"],
                transactions_parser=mock_dependencies["transactions_parser"],
                consensus_service=mock_dependencies["consensus_service"],
                signed_rollup_transaction=valid_signed_transaction,
                sim_config=None,
            )

            # Verify the function continues execution
            mock_dependencies[
                "transactions_parser"
            ].decode_signed_transaction.assert_called_once()

    def test_send_raw_transaction_with_empty_sim_config_in_hosted_environment(
        self, mock_dependencies, valid_signed_transaction
    ):
        """Test that an empty sim_config dict does NOT trigger the hosted studio check"""
        # Mock the transaction parser to return a valid decoded transaction
        mock_decoded_transaction = MagicMock()
        mock_decoded_transaction.from_address = "0x123"
        mock_decoded_transaction.value = 100
        mock_decoded_transaction.data = "test_data"
        mock_dependencies[
            "transactions_parser"
        ].decode_signed_transaction.return_value = mock_decoded_transaction
        mock_dependencies["accounts_manager"].is_valid_address.return_value = True
        mock_dependencies[
            "transactions_parser"
        ].transaction_has_valid_signature.return_value = True
        mock_dependencies["transactions_processor"].insert_transaction.return_value = (
            "tx_hash"
        )

        with patch.dict(os.environ, {"VITE_IS_HOSTED": "true"}):
            # Should NOT raise an exception for empty sim_config
            result = send_raw_transaction(
                transactions_processor=mock_dependencies["transactions_processor"],
                msg_handler=mock_dependencies["msg_handler"],
                accounts_manager=mock_dependencies["accounts_manager"],
                transactions_parser=mock_dependencies["transactions_parser"],
                consensus_service=mock_dependencies["consensus_service"],
                signed_rollup_transaction=valid_signed_transaction,
                sim_config={},
            )

            # Verify the function continues execution
            mock_dependencies[
                "transactions_parser"
            ].decode_signed_transaction.assert_called_once()

    def test_send_raw_transaction_with_non_empty_sim_config_in_hosted_environment(
        self, mock_dependencies, valid_signed_transaction
    ):
        """Test that a non-empty sim_config dict DOES trigger the hosted studio check"""
        with patch.dict(os.environ, {"VITE_IS_HOSTED": "true"}):
            with pytest.raises(JSONRPCError) as exc_info:
                send_raw_transaction(
                    transactions_processor=mock_dependencies["transactions_processor"],
                    msg_handler=mock_dependencies["msg_handler"],
                    accounts_manager=mock_dependencies["accounts_manager"],
                    transactions_parser=mock_dependencies["transactions_parser"],
                    consensus_service=mock_dependencies["consensus_service"],
                    signed_rollup_transaction=valid_signed_transaction,
                    sim_config={"key": "value"},
                )

            assert exc_info.value.code == -32000
            assert exc_info.value.message == "Non-allowed operation"


class TestRaiseNonAllowedInHostedStudio:
    """Test the raise_non_allowed_in_hosted_studio helper function"""

    def test_raises_error_when_hosted_is_true(self):
        """Test that the function raises JSONRPCError when VITE_IS_HOSTED=true"""
        with patch.dict(os.environ, {"VITE_IS_HOSTED": "true"}):
            with pytest.raises(JSONRPCError) as exc_info:
                raise_non_allowed_in_hosted_studio()

            assert exc_info.value.code == -32000
            assert exc_info.value.message == "Non-allowed operation"
            assert exc_info.value.data == {}

    def test_does_not_raise_when_hosted_is_false(self):
        """Test that the function does not raise error when VITE_IS_HOSTED=false"""
        with patch.dict(os.environ, {"VITE_IS_HOSTED": "false"}):
            # Should not raise an exception
            raise_non_allowed_in_hosted_studio()

    def test_does_not_raise_when_hosted_not_set(self):
        """Test that the function does not raise error when VITE_IS_HOSTED is not set"""
        with patch.dict(os.environ, {}, clear=True):
            # Should not raise an exception
            raise_non_allowed_in_hosted_studio()

    def test_does_not_raise_when_hosted_is_other_value(self):
        """Test that the function does not raise error when VITE_IS_HOSTED has other values"""
        test_values = ["TRUE", "True", "1", "yes", "on", ""]

        for value in test_values:
            with patch.dict(os.environ, {"VITE_IS_HOSTED": value}):
                # Should not raise an exception for any value other than exactly "true"
                raise_non_allowed_in_hosted_studio()
