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
from backend.database_handler.models import TransactionStatus


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


class TestWorkerAppealContractNotFoundHandling:
    """Test that worker correctly handles ContractNotFoundError during appeal processing"""

    @pytest.mark.asyncio
    async def test_contract_not_found_during_appeal_dispatches_finalized_status(self):
        """
        When ContractNotFoundError is raised during appeal processing,
        the transaction should be marked as FINALIZED to prevent infinite retries.
        """
        from backend.consensus.worker import ConsensusWorker

        mock_session = MagicMock(spec=Session)
        mock_session.rollback = MagicMock()
        mock_session.commit = MagicMock()

        def get_session_side_effect():
            ctx = MagicMock()
            inner_session = MagicMock(spec=Session)
            inner_session.commit = MagicMock()
            ctx.__enter__ = MagicMock(return_value=inner_session)
            ctx.__exit__ = MagicMock(return_value=None)
            return ctx

        mock_msg_handler = MagicMock()
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

        appeal_data = {
            "hash": "0xtest_appeal_hash",
            "from_address": "0xfrom",
            "to_address": "0xnonexistent_contract",
            "data": {"calldata": "test"},
            "value": 0,
            "type": 2,
            "status": "UNDETERMINED",
            "blocked_at": None,
        }

        contract_address = "0xnonexistent_contract"

        # Mock the validators_manager.snapshot() context manager
        mock_snapshot = MagicMock()
        mock_snapshot_cm = AsyncMock()
        mock_snapshot_cm.__aenter__ = AsyncMock(return_value=mock_snapshot)
        mock_snapshot_cm.__aexit__ = AsyncMock(return_value=None)
        mock_validators_manager.snapshot = MagicMock(return_value=mock_snapshot_cm)

        with patch(
            "backend.consensus.base.ConsensusAlgorithm.process_leader_appeal",
            new_callable=AsyncMock,
            side_effect=ContractNotFoundError(contract_address),
        ):
            with patch.object(worker, "release_transaction"):
                with patch(
                    "backend.consensus.base.ConsensusAlgorithm.dispatch_transaction_status_update",
                    new_callable=AsyncMock,
                ) as mock_dispatch:
                    await worker.process_appeal(appeal_data, mock_session)

                    mock_dispatch.assert_called_once()
                    call_args = mock_dispatch.call_args
                    assert call_args.args[1] == "0xtest_appeal_hash"
                    assert call_args.args[2] == TransactionStatus.FINALIZED
                    assert call_args.args[3] == mock_msg_handler


class TestGenCallContractNotFoundHandling:
    """Test that gen_call/sim_call properly handle ContractNotFoundError from execution"""

    @pytest.mark.asyncio
    async def test_gen_call_with_validator_catches_contract_not_found_during_execution(
        self,
    ):
        """
        When ContractNotFoundError is raised during GenVM execution (e.g., cross-contract
        call to a non-existent contract), it should be caught and converted to a
        NotFoundError JSON-RPC response.
        """
        from backend.protocol_rpc.endpoints import _gen_call_with_validator
        from backend.protocol_rpc.exceptions import NotFoundError

        mock_session = MagicMock(spec=Session)
        mock_accounts_manager = MagicMock()
        mock_genvm_manager = MagicMock()
        mock_msg_handler = MagicMock()
        mock_msg_handler.with_client_session = MagicMock(return_value=mock_msg_handler)
        mock_transactions_parser = MagicMock()

        mock_validators_snapshot = MagicMock()
        mock_validator = MagicMock()
        mock_validator.validator = MagicMock()
        mock_validators_snapshot.nodes = [mock_validator]

        contract_address = "0xnonexistent_cross_contract"

        params = {
            "type": "read",
            "data": "0x1234",
            "to": "0x" + "ab" * 20,
            "from": "0x" + "cd" * 20,
        }

        # Mock ContractSnapshot to succeed (initial contract exists)
        with patch(
            "backend.protocol_rpc.endpoints.ContractSnapshot"
        ) as mock_snapshot_cls:
            mock_snapshot_cls.return_value = MagicMock()

            # Mock the decode call
            mock_decoded = MagicMock()
            mock_decoded.calldata = b"test"
            mock_transactions_parser.decode_method_call_data.return_value = mock_decoded

            # Mock Node to raise ContractNotFoundError during execution
            with patch("backend.protocol_rpc.endpoints.Node") as mock_node_cls:
                mock_node = MagicMock()
                mock_node.get_contract_data = AsyncMock(
                    side_effect=ContractNotFoundError(contract_address)
                )
                mock_node_cls.return_value = mock_node

                with patch("backend.protocol_rpc.endpoints._check_rate_limit"):
                    with patch(
                        "backend.protocol_rpc.endpoints._genvm_semaphore"
                    ) as mock_sem:
                        mock_sem.locked.return_value = False
                        mock_sem.__aenter__ = AsyncMock()
                        mock_sem.__aexit__ = AsyncMock(return_value=False)

                        with patch(
                            "backend.protocol_rpc.endpoints.get_client_session_id",
                            return_value="test-session",
                        ):
                            with pytest.raises(NotFoundError) as exc_info:
                                await _gen_call_with_validator(
                                    mock_session,
                                    mock_accounts_manager,
                                    mock_genvm_manager,
                                    mock_msg_handler,
                                    mock_transactions_parser,
                                    mock_validators_snapshot,
                                    params,
                                )

                            assert contract_address in str(exc_info.value)


class TestEthCallContractNotFoundHandling:
    """Test that eth_call properly handles ContractNotFoundError from execution"""

    @pytest.mark.asyncio
    async def test_eth_call_catches_contract_not_found_during_execution(self):
        """
        When ContractNotFoundError is raised during eth_call GenVM execution
        (e.g., cross-contract call to a non-existent contract), it should be
        caught and converted to a NotFoundError JSON-RPC response.
        """
        from backend.protocol_rpc.endpoints import eth_call
        from backend.protocol_rpc.exceptions import NotFoundError

        mock_session = MagicMock(spec=Session)
        mock_accounts_manager = MagicMock()
        mock_accounts_manager.is_valid_address.return_value = True
        mock_msg_handler = MagicMock()
        mock_msg_handler.with_client_session = MagicMock(return_value=mock_msg_handler)
        mock_transactions_parser = MagicMock()
        mock_genvm_manager = MagicMock()
        mock_transactions_processor = MagicMock()

        mock_decoded = MagicMock()
        mock_decoded.calldata = b"test"
        mock_transactions_parser.decode_method_call_data.return_value = mock_decoded

        contract_address = "0xnonexistent_cross_contract"

        to_address = "0x" + "ab" * 20
        from_address = "0x" + "cd" * 20
        params = {
            "to": to_address,
            "from": from_address,
            "data": "0x1234",
        }

        # Mock validators_manager.snapshot() async context manager
        mock_validator = MagicMock()
        mock_validator.validator = MagicMock()
        mock_validator.validator.address = from_address
        mock_snapshot = MagicMock()
        mock_snapshot.nodes = [mock_validator]
        mock_validators_manager = MagicMock()
        mock_snapshot_cm = AsyncMock()
        mock_snapshot_cm.__aenter__ = AsyncMock(return_value=mock_snapshot)
        mock_snapshot_cm.__aexit__ = AsyncMock(return_value=False)
        mock_validators_manager.snapshot = MagicMock(return_value=mock_snapshot_cm)

        with patch(
            "backend.protocol_rpc.endpoints.ContractSnapshot"
        ) as mock_snapshot_cls:
            mock_snapshot_cls.return_value = MagicMock()

            with patch("backend.protocol_rpc.endpoints.Node") as mock_node_cls:
                mock_node = MagicMock()
                mock_node.get_contract_data = AsyncMock(
                    side_effect=ContractNotFoundError(contract_address)
                )
                mock_node_cls.return_value = mock_node

                with patch(
                    "backend.protocol_rpc.endpoints.handle_consensus_data_call",
                    return_value=None,
                ):
                    with patch(
                        "backend.protocol_rpc.endpoints.get_client_session_id",
                        return_value="test-session",
                    ):
                        with pytest.raises(NotFoundError) as exc_info:
                            await eth_call(
                                mock_session,
                                mock_accounts_manager,
                                mock_msg_handler,
                                mock_transactions_parser,
                                mock_validators_manager,
                                mock_genvm_manager,
                                mock_transactions_processor,
                                params,
                            )

                        assert contract_address in str(exc_info.value)


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
