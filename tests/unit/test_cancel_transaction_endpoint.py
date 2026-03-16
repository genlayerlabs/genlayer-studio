import os
import pytest
from unittest.mock import Mock, MagicMock, patch
from backend.protocol_rpc.endpoints import cancel_transaction
from backend.protocol_rpc.exceptions import JSONRPCError, NotFoundError


class TestCancelTransactionEndpoint:
    """Test cases for the cancel_transaction RPC endpoint."""

    def setup_method(self):
        """Set up test fixtures."""
        self.session = Mock(name="Session")
        self.mock_msg_handler = MagicMock()
        self.mock_msg_handler.send_transaction_status_update = MagicMock()
        self.valid_tx_hash = (
            "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
        )
        self.sender_address = "0x9F0e84243496AcFB3Cd99D02eA59673c05901501"

    def _setup_transaction(self, from_address=None, status_value="PENDING"):
        """Helper to mock the transaction lookup query chain."""
        mock_tx = Mock()
        mock_tx.from_address = from_address
        mock_tx.status = Mock()
        mock_tx.status.value = status_value
        self.session.query.return_value.filter_by.return_value.one_or_none.return_value = (
            mock_tx
        )
        return mock_tx

    def _setup_execute(self, rowcount):
        """Helper to mock the atomic UPDATE result."""
        mock_result = Mock()
        mock_result.rowcount = rowcount
        self.session.execute.return_value = mock_result

    # ---- Hash validation tests ----

    def test_invalid_hash_empty_string(self):
        """Test validation error for empty transaction hash."""
        with pytest.raises(JSONRPCError) as exc_info:
            cancel_transaction(self.session, "", self.mock_msg_handler)
        assert exc_info.value.code == -32602
        assert "Invalid transaction hash format" in exc_info.value.message

    def test_invalid_hash_none(self):
        """Test validation error for None transaction hash."""
        with pytest.raises(JSONRPCError) as exc_info:
            cancel_transaction(self.session, None, self.mock_msg_handler)
        assert exc_info.value.code == -32602
        assert "Invalid transaction hash format" in exc_info.value.message

    def test_invalid_hash_no_0x_prefix(self):
        """Test validation error for hash without 0x prefix."""
        no_prefix = "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
        with pytest.raises(JSONRPCError) as exc_info:
            cancel_transaction(self.session, no_prefix, self.mock_msg_handler)
        assert exc_info.value.code == -32602

    def test_invalid_hash_wrong_length(self):
        """Test validation error for hash with wrong length."""
        short_hash = "0x123"
        with pytest.raises(JSONRPCError) as exc_info:
            cancel_transaction(self.session, short_hash, self.mock_msg_handler)
        assert exc_info.value.code == -32602

    # ---- Transaction not found ----

    def test_transaction_not_found(self):
        """Test error when transaction does not exist."""
        self.session.query.return_value.filter_by.return_value.one_or_none.return_value = (
            None
        )

        with pytest.raises(NotFoundError) as exc_info:
            cancel_transaction(self.session, self.valid_tx_hash, self.mock_msg_handler)
        assert "Transaction not found" in exc_info.value.message

    # ---- Local mode (no auth needed) ----

    def test_local_mode_successful_cancel(self):
        """Test successful cancel in local dev mode (no env vars)."""
        self._setup_transaction(from_address=self.sender_address)
        self._setup_execute(rowcount=1)

        with patch.dict(os.environ, {}, clear=True):
            result = cancel_transaction(
                self.session, self.valid_tx_hash, self.mock_msg_handler
            )

        assert result["transaction_hash"] == self.valid_tx_hash
        assert result["status"] == "CANCELED"
        self.session.commit.assert_called()
        self.mock_msg_handler.send_transaction_status_update.assert_called_once_with(
            self.valid_tx_hash, "CANCELED"
        )

    def test_local_mode_no_signature_needed(self):
        """Test that local mode doesn't require signature or admin_key."""
        self._setup_transaction(from_address=self.sender_address)
        self._setup_execute(rowcount=1)

        with patch.dict(os.environ, {}, clear=True):
            result = cancel_transaction(
                self.session,
                self.valid_tx_hash,
                self.mock_msg_handler,
                signature=None,
                admin_key=None,
            )

        assert result["status"] == "CANCELED"

    # ---- Hosted mode auth tests ----

    def test_hosted_mode_admin_key_succeeds(self):
        """Test cancel with valid admin key in hosted mode."""
        self._setup_transaction(from_address=self.sender_address)
        self._setup_execute(rowcount=1)

        with patch.dict(
            os.environ, {"VITE_IS_HOSTED": "true", "ADMIN_API_KEY": "secret123"}
        ):
            result = cancel_transaction(
                self.session,
                self.valid_tx_hash,
                self.mock_msg_handler,
                admin_key="secret123",
            )

        assert result["status"] == "CANCELED"

    def test_hosted_mode_wrong_admin_key_no_signature(self):
        """Test that wrong admin key without signature is rejected."""
        self._setup_transaction(from_address=self.sender_address)

        with patch.dict(
            os.environ, {"VITE_IS_HOSTED": "true", "ADMIN_API_KEY": "secret123"}
        ):
            with pytest.raises(JSONRPCError) as exc_info:
                cancel_transaction(
                    self.session,
                    self.valid_tx_hash,
                    self.mock_msg_handler,
                    admin_key="wrong_key",
                )
        assert exc_info.value.code == -32000
        assert "Cancel requires admin key or sender signature" in exc_info.value.message

    def test_hosted_mode_no_auth_provided(self):
        """Test that hosted mode rejects calls without auth."""
        self._setup_transaction(from_address=self.sender_address)

        with patch.dict(
            os.environ, {"VITE_IS_HOSTED": "true", "ADMIN_API_KEY": "secret123"}
        ):
            with pytest.raises(JSONRPCError) as exc_info:
                cancel_transaction(
                    self.session,
                    self.valid_tx_hash,
                    self.mock_msg_handler,
                )
        assert exc_info.value.code == -32000
        assert "Cancel requires admin key or sender signature" in exc_info.value.message

    @patch("eth_account.Account.recover_message")
    @patch("eth_account.messages.encode_defunct")
    @patch("web3.Web3")
    def test_hosted_mode_valid_signature_from_sender(
        self, mock_web3_class, mock_encode, mock_recover
    ):
        """Test cancel with valid signature from the transaction sender."""
        self._setup_transaction(from_address=self.sender_address)
        self._setup_execute(rowcount=1)
        mock_recover.return_value = self.sender_address
        mock_web3_class.keccak.return_value = b"\x00" * 32
        mock_web3_class.to_bytes.return_value = b"\x00" * 32

        with patch.dict(os.environ, {"VITE_IS_HOSTED": "true"}):
            result = cancel_transaction(
                self.session,
                self.valid_tx_hash,
                self.mock_msg_handler,
                signature="0xvalidsignature",
            )

        assert result["status"] == "CANCELED"
        mock_recover.assert_called_once()

    @patch("eth_account.Account.recover_message")
    @patch("eth_account.messages.encode_defunct")
    @patch("web3.Web3")
    def test_hosted_mode_signature_from_wrong_address(
        self, mock_web3_class, mock_encode, mock_recover
    ):
        """Test that signature from non-sender is rejected."""
        self._setup_transaction(from_address=self.sender_address)
        mock_recover.return_value = "0xDifferentAddress"
        mock_web3_class.keccak.return_value = b"\x00" * 32
        mock_web3_class.to_bytes.return_value = b"\x00" * 32

        with patch.dict(os.environ, {"VITE_IS_HOSTED": "true"}):
            with pytest.raises(JSONRPCError) as exc_info:
                cancel_transaction(
                    self.session,
                    self.valid_tx_hash,
                    self.mock_msg_handler,
                    signature="0xwrongsignature",
                )

        assert exc_info.value.code == -32000
        assert "Only transaction sender can cancel" in exc_info.value.message

    def test_hosted_mode_no_sender_with_signature(self):
        """Test that signature auth is rejected when tx has no from_address."""
        self._setup_transaction(from_address=None)

        with patch.dict(os.environ, {"VITE_IS_HOSTED": "true"}):
            with pytest.raises(JSONRPCError) as exc_info:
                cancel_transaction(
                    self.session,
                    self.valid_tx_hash,
                    self.mock_msg_handler,
                    signature="0xsomesignature",
                )

        assert exc_info.value.code == -32000
        assert "Transaction has no sender" in exc_info.value.message

    def test_hosted_mode_admin_key_works_for_no_sender_tx(self):
        """Test that admin key can cancel a tx with no from_address."""
        self._setup_transaction(from_address=None)
        self._setup_execute(rowcount=1)

        with patch.dict(
            os.environ, {"VITE_IS_HOSTED": "true", "ADMIN_API_KEY": "secret123"}
        ):
            result = cancel_transaction(
                self.session,
                self.valid_tx_hash,
                self.mock_msg_handler,
                admin_key="secret123",
            )

        assert result["status"] == "CANCELED"

    @patch("eth_account.Account.recover_message")
    @patch("eth_account.messages.encode_defunct")
    @patch("web3.Web3")
    def test_hosted_mode_invalid_signature_format(
        self, mock_web3_class, mock_encode, mock_recover
    ):
        """Test that a malformed signature raises an error."""
        self._setup_transaction(from_address=self.sender_address)
        mock_recover.side_effect = Exception("Bad signature format")
        mock_web3_class.keccak.return_value = b"\x00" * 32
        mock_web3_class.to_bytes.return_value = b"\x00" * 32

        with patch.dict(os.environ, {"VITE_IS_HOSTED": "true"}):
            with pytest.raises(JSONRPCError) as exc_info:
                cancel_transaction(
                    self.session,
                    self.valid_tx_hash,
                    self.mock_msg_handler,
                    signature="0xbadsig",
                )

        assert exc_info.value.code == -32000
        assert "Invalid signature" in exc_info.value.message

    def test_admin_api_key_env_only(self):
        """Test auth when only ADMIN_API_KEY is set (not hosted mode)."""
        self._setup_transaction(from_address=self.sender_address)
        self._setup_execute(rowcount=1)

        with patch.dict(os.environ, {"ADMIN_API_KEY": "mykey"}, clear=True):
            result = cancel_transaction(
                self.session,
                self.valid_tx_hash,
                self.mock_msg_handler,
                admin_key="mykey",
            )

        assert result["status"] == "CANCELED"

    def test_admin_api_key_env_requires_auth(self):
        """Test that ADMIN_API_KEY env var triggers auth requirement."""
        self._setup_transaction(from_address=self.sender_address)

        with patch.dict(os.environ, {"ADMIN_API_KEY": "mykey"}, clear=True):
            with pytest.raises(JSONRPCError) as exc_info:
                cancel_transaction(
                    self.session,
                    self.valid_tx_hash,
                    self.mock_msg_handler,
                )

        assert exc_info.value.code == -32000
        assert "Cancel requires admin key or sender signature" in exc_info.value.message

    # ---- Atomic cancel / race condition tests ----

    def test_cancel_fails_when_already_processing(self):
        """Test that cancel fails when worker has already claimed the tx."""
        self._setup_transaction(from_address=self.sender_address)
        self._setup_execute(rowcount=0)  # UPDATE matched 0 rows

        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(JSONRPCError) as exc_info:
                cancel_transaction(
                    self.session,
                    self.valid_tx_hash,
                    self.mock_msg_handler,
                )

        assert exc_info.value.code == -32000
        assert "Transaction cannot be cancelled" in exc_info.value.message
        self.mock_msg_handler.send_transaction_status_update.assert_not_called()

    def test_cancel_fails_for_finalized_tx(self):
        """Test that cancel fails for a tx in terminal state."""
        self._setup_transaction(
            from_address=self.sender_address, status_value="FINALIZED"
        )
        self._setup_execute(rowcount=0)

        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(JSONRPCError) as exc_info:
                cancel_transaction(
                    self.session,
                    self.valid_tx_hash,
                    self.mock_msg_handler,
                )

        assert exc_info.value.code == -32000
        assert "Transaction cannot be cancelled" in exc_info.value.message

    def test_cancel_fails_for_already_canceled_tx(self):
        """Test that cancel fails for an already canceled tx."""
        self._setup_transaction(
            from_address=self.sender_address, status_value="CANCELED"
        )
        self._setup_execute(rowcount=0)

        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(JSONRPCError) as exc_info:
                cancel_transaction(
                    self.session,
                    self.valid_tx_hash,
                    self.mock_msg_handler,
                )

        assert exc_info.value.code == -32000

    # ---- Successful cancel behavior ----

    def test_successful_cancel_returns_correct_response(self):
        """Test the response structure of a successful cancel."""
        self._setup_transaction(from_address=self.sender_address)
        self._setup_execute(rowcount=1)

        with patch.dict(os.environ, {}, clear=True):
            result = cancel_transaction(
                self.session, self.valid_tx_hash, self.mock_msg_handler
            )

        assert result == {
            "transaction_hash": self.valid_tx_hash,
            "status": "CANCELED",
        }

    def test_successful_cancel_commits_session(self):
        """Test that session.commit() is called on successful cancel."""
        self._setup_transaction(from_address=self.sender_address)
        self._setup_execute(rowcount=1)

        with patch.dict(os.environ, {}, clear=True):
            cancel_transaction(self.session, self.valid_tx_hash, self.mock_msg_handler)

        self.session.commit.assert_called()

    def test_successful_cancel_sends_websocket_notification(self):
        """Test that WebSocket notification is sent on successful cancel."""
        self._setup_transaction(from_address=self.sender_address)
        self._setup_execute(rowcount=1)

        with patch.dict(os.environ, {}, clear=True):
            cancel_transaction(self.session, self.valid_tx_hash, self.mock_msg_handler)

        self.mock_msg_handler.send_transaction_status_update.assert_called_once_with(
            self.valid_tx_hash, "CANCELED"
        )

    def test_cancel_activated_transaction(self):
        """Test that ACTIVATED transactions can also be cancelled."""
        self._setup_transaction(
            from_address=self.sender_address, status_value="ACTIVATED"
        )
        self._setup_execute(rowcount=1)

        with patch.dict(os.environ, {}, clear=True):
            result = cancel_transaction(
                self.session, self.valid_tx_hash, self.mock_msg_handler
            )

        assert result["status"] == "CANCELED"

    def test_session_execute_called_with_correct_sql(self):
        """Test that the atomic UPDATE is called with the expected parameters."""
        self._setup_transaction(from_address=self.sender_address)
        self._setup_execute(rowcount=1)

        with patch.dict(os.environ, {}, clear=True):
            cancel_transaction(self.session, self.valid_tx_hash, self.mock_msg_handler)

        # Verify execute was called with our tx hash
        call_args = self.session.execute.call_args
        assert call_args is not None
        params = call_args[1] if call_args[1] else call_args[0][1]
        assert params["hash"] == self.valid_tx_hash
