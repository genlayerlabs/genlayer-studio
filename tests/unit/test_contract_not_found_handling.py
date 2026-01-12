"""
Tests for ContractNotFoundError handling in the consensus worker.

This test verifies that when a transaction targets a non-existent contract,
the transaction is marked as ACCEPTED (with ERROR execution result) instead of being retried infinitely.
This allows the transaction to flow through finalization properly.
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from sqlalchemy.orm import Session

from backend.database_handler.errors import ContractNotFoundError
from backend.database_handler.models import Transactions, TransactionStatus


class TestContractNotFoundError:
    """Test ContractNotFoundError exception class"""

    def test_contract_not_found_error_with_address(self):
        """ContractNotFoundError stores address and generates message"""
        address = "0x1234567890abcdef"
        error = ContractNotFoundError(address)

        assert error.address == address
        assert error.message == f"Contract {address} not found"
        assert str(error) == f"Contract {address} not found"

    def test_contract_not_found_error_with_custom_message(self):
        """ContractNotFoundError accepts custom message"""
        address = "0x1234567890abcdef"
        custom_msg = "Custom error message"
        error = ContractNotFoundError(address, custom_msg)

        assert error.address == address
        assert error.message == custom_msg
        assert str(error) == custom_msg


class TestWorkerContractNotFoundHandling:
    """Test that worker correctly handles ContractNotFoundError"""

    @pytest.mark.asyncio
    async def test_contract_not_found_dispatches_accepted_status(self):
        """
        When ContractNotFoundError is raised during transaction processing,
        the dispatch_transaction_status_update should be called with ACCEPTED.

        This verifies the fix prevents infinite retry loops by checking that:
        1. The error is caught and handled
        2. The status update to ACCEPTED is dispatched (with ERROR execution result in consensus_data)
        3. The transaction can then flow through finalization properly
        """
        from backend.consensus.worker import ConsensusWorker

        # Create mock session
        mock_session = MagicMock(spec=Session)
        mock_session.rollback = MagicMock()
        mock_session.commit = MagicMock()

        # Create a context manager mock for get_session that returns a fresh mock each time
        # This simulates the real behavior where each `with get_session()` creates a new session
        def get_session_side_effect():
            ctx = MagicMock()
            inner_session = MagicMock(spec=Session)
            inner_session.commit = MagicMock()
            ctx.__enter__ = MagicMock(return_value=inner_session)
            ctx.__exit__ = MagicMock(return_value=None)
            return ctx

        mock_msg_handler = MagicMock()
        mock_msg_handler.send_message = MagicMock()

        mock_consensus_service = MagicMock()
        mock_validators_manager = MagicMock()
        mock_genvm_manager = MagicMock()

        worker = ConsensusWorker(
            get_session=get_session_side_effect,
            msg_handler=mock_msg_handler,
            consensus_service=mock_consensus_service,
            validators_manager=mock_validators_manager,
            genvm_manager=mock_genvm_manager,
            worker_id="test-worker",
        )

        # Prepare transaction data
        transaction_data = {
            "hash": "0xtest_transaction_hash",
            "from_address": "0xfrom",
            "to_address": "0xnonexistent_contract",
            "data": {"calldata": "test"},
            "value": 0,
            "type": 2,  # RUN_CONTRACT
            "status": "PENDING",
            "blocked_at": None,
        }

        # Mock exec_transaction to raise ContractNotFoundError
        contract_address = "0xnonexistent_contract"
        with patch.object(
            worker.consensus_algorithm,
            "exec_transaction",
            side_effect=ContractNotFoundError(contract_address),
        ):
            with patch.object(worker, "release_transaction"):
                # Mock dispatch_transaction_status_update to capture what it's called with
                with patch(
                    "backend.consensus.base.ConsensusAlgorithm.dispatch_transaction_status_update",
                    new_callable=AsyncMock,
                ) as mock_dispatch:
                    await worker.process_transaction(transaction_data, mock_session)

                    # Verify WebSocket notification was sent with ACCEPTED status
                    mock_dispatch.assert_called_once()
                    call_args = mock_dispatch.call_args

                    # Check that dispatch was called with the correct transaction hash
                    assert call_args.args[1] == "0xtest_transaction_hash"

                    # Check that dispatch was called with ACCEPTED status
                    assert call_args.args[2] == TransactionStatus.ACCEPTED

                    # Check that msg_handler was passed
                    assert call_args.args[3] == mock_msg_handler


class TestContractSnapshotRaisesCorrectError:
    """Test that ContractSnapshot raises ContractNotFoundError"""

    def test_contract_snapshot_raises_contract_not_found_error(self):
        """ContractSnapshot should raise ContractNotFoundError for missing contracts"""
        from backend.database_handler.contract_snapshot import ContractSnapshot

        mock_session = MagicMock(spec=Session)
        # Mock query to return None (contract not found)
        mock_session.query.return_value.filter.return_value.populate_existing.return_value.one_or_none.return_value = (
            None
        )

        with pytest.raises(ContractNotFoundError) as exc_info:
            ContractSnapshot("0xnonexistent", mock_session)

        assert exc_info.value.address == "0xnonexistent"
        assert "0xnonexistent" in str(exc_info.value)
