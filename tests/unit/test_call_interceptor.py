"""
Comprehensive unit tests for RPC call interceptor functionality.
Tests both the interception logic and integration with eth_call endpoint.
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import eth_utils
from backend.protocol_rpc.endpoints import eth_call
from backend.protocol_rpc.call_interceptor import (
    handle_consensus_data_call,
    is_consensus_data_contract_call,
    CONSENSUS_DATA_CONTRACT_ADDRESS,
)
from backend.protocol_rpc.calls_intercept.get_latest_pending_tx_count import (
    GetLatestPendingTxCountHandler,
)
from backend.errors.errors import InvalidAddressError
from backend.protocol_rpc.exceptions import JSONRPCError
from backend.node.types import ExecutionResultStatus
import backend.validators as validators
from backend.protocol_rpc.transactions_parser import TransactionParser


class TestCallInterceptor:

    @pytest.fixture
    def mock_session(self):
        """Mock database session"""
        return MagicMock()

    @pytest.fixture
    def mock_accounts_manager(self):
        """Mock accounts manager that validates addresses"""
        manager = MagicMock()
        manager.is_valid_address.return_value = True
        return manager

    @pytest.fixture
    def mock_msg_handler(self):
        """Mock message handler for WebSocket communication"""
        handler = MagicMock()
        handler.with_client_session.return_value = handler
        return handler

    @pytest.fixture
    def mock_transactions_parser(self):
        """Mock transaction parser for decoding call data"""
        parser = MagicMock(spec=TransactionParser)
        decoded_data = MagicMock()
        decoded_data.calldata = b"test_calldata"
        parser.decode_method_call_data.return_value = decoded_data
        return parser

    @pytest.fixture
    def mock_validators_manager(self):
        """Mock validators manager with snapshot context manager"""
        manager = MagicMock(spec=validators.Manager)
        snapshot = MagicMock()
        validator = MagicMock()
        validator.address = "0x123"
        node_mock = MagicMock()
        node_mock.validator = validator
        snapshot.nodes = [node_mock]
        snapshot.__aenter__ = AsyncMock(return_value=snapshot)
        snapshot.__aexit__ = AsyncMock(return_value=None)
        manager.snapshot.return_value = snapshot
        return manager

    @pytest.fixture
    def mock_transactions_processor(self):
        """Mock transactions processor for database queries"""
        processor = MagicMock()
        processor.get_pending_transaction_count_for_address.return_value = 5
        return processor

    @pytest.fixture
    def mock_genvm_manager(self):
        genvm_manager = MagicMock()
        return genvm_manager

    @pytest.fixture
    def sample_call_data(self):
        """Generate sample call data for getLatestPendingTxCount"""
        recipient = "0x1234567890123456789012345678901234567890"
        # Method selector (fe4cfca7) + padded recipient address
        return f"0x{GetLatestPendingTxCountHandler.METHOD_SELECTOR}{recipient[2:].zfill(64)}"

    @pytest.fixture
    def handler(self):
        """Get handler instance for testing"""
        return GetLatestPendingTxCountHandler()

    # ============================================================================
    # CONTRACT DETECTION TESTS
    # ============================================================================

    def test_consensus_data_contract_detection(self):
        """
        Test 1: Verify ConsensusData contract address detection

        Why needed: Ensures we correctly identify calls to the ConsensusData contract
        How it works: Tests case-insensitive address matching
        """
        # Test exact match
        assert is_consensus_data_contract_call(CONSENSUS_DATA_CONTRACT_ADDRESS)

        # Test case insensitive matching
        assert is_consensus_data_contract_call(CONSENSUS_DATA_CONTRACT_ADDRESS.lower())
        assert is_consensus_data_contract_call(CONSENSUS_DATA_CONTRACT_ADDRESS.upper())

        # Test different address should not match
        assert not is_consensus_data_contract_call(
            "0x1234567890123456789012345678901234567890"
        )

    def test_method_signature_detection(self, handler, sample_call_data):
        """
        Test 2: Verify getLatestPendingTxCount method signature detection

        Why needed: Ensures we correctly identify the specific method being called
        How it works: Checks if call data starts with correct method selector
        """
        # Valid call data should be detected
        assert handler.can_handle(sample_call_data)

        # Different method selector should not match
        assert not handler.can_handle("0xabcdef12" + "0" * 64)

        # Invalid data formats should not match
        assert not handler.can_handle("0x")
        assert not handler.can_handle("")
        assert not handler.can_handle("0x123")  # Too short

    # ============================================================================
    # PARAMETER EXTRACTION TESTS
    # ============================================================================

    def test_extract_recipient_address_from_valid_data(self, handler, sample_call_data):
        """
        Test 3: Extract recipient address from valid call data

        Why needed: Ensures we correctly parse the address parameter from encoded call data
        How it works: Extracts bytes 5-36 (after method selector) and formats as address
        """
        expected_recipient = "0x1234567890123456789012345678901234567890"
        actual_recipient = handler._extract_recipient_address(sample_call_data)
        assert actual_recipient.lower() == expected_recipient.lower()

    def test_extract_recipient_address_error_handling(self, handler):
        """
        Test 4: Error handling for invalid call data when extracting address

        Why needed: Ensures robust error handling for malformed inputs
        How it works: Tests various invalid input scenarios
        """
        # Empty data
        with pytest.raises(ValueError, match="Call data is empty"):
            handler._extract_recipient_address("")

        # Too short data (missing address parameter)
        with pytest.raises(ValueError, match="Call data too short"):
            handler._extract_recipient_address("0xfe4cfca7")

        # Only method selector, no parameters
        with pytest.raises(ValueError, match="Call data too short"):
            handler._extract_recipient_address("0x" + handler.METHOD_SELECTOR)

    # ============================================================================
    # HANDLER LOGIC TESTS
    # ============================================================================

    def test_handle_consensus_data_call_success(
        self, mock_transactions_processor, sample_call_data
    ):
        """
        Test 5: Successful handling of ConsensusData call

        Why needed: Verifies the main handler correctly processes valid requests
        How it works: Mocks processor to return a count, checks hex encoding
        """
        mock_transactions_processor.get_pending_transaction_count_for_address.return_value = (
            3
        )

        result = handle_consensus_data_call(
            mock_transactions_processor,
            CONSENSUS_DATA_CONTRACT_ADDRESS,
            sample_call_data,
        )

        # Should return hex-encoded uint256 for count 3
        expected = "0x0000000000000000000000000000000000000000000000000000000000000003"
        assert result == expected

        # Verify processor was called with correct address
        mock_transactions_processor.get_pending_transaction_count_for_address.assert_called_once_with(
            "0x1234567890123456789012345678901234567890"
        )

    def test_handle_non_consensus_data_contract(
        self, mock_transactions_processor, sample_call_data
    ):
        """
        Test 6: Non-ConsensusData contract calls return None

        Why needed: Ensures other contracts pass through without interception
        How it works: Tests with different contract address
        """
        other_address = "0x9999999999999999999999999999999999999999"
        result = handle_consensus_data_call(
            mock_transactions_processor, other_address, sample_call_data
        )
        assert result is None

        # Processor should not be called
        mock_transactions_processor.get_pending_transaction_count_for_address.assert_not_called()

    def test_handle_unsupported_method_on_consensus_data(
        self, mock_transactions_processor
    ):
        """
        Test 7: Unsupported methods on ConsensusData return None

        Why needed: Ensures only implemented methods are intercepted
        How it works: Tests with different method selector
        """
        unsupported_call = "0xabcdef12" + "0" * 64  # Different method selector
        result = handle_consensus_data_call(
            mock_transactions_processor,
            CONSENSUS_DATA_CONTRACT_ADDRESS,
            unsupported_call,
        )
        assert result is None

    def test_handle_invalid_call_data(self, mock_transactions_processor):
        """
        Test 8: Invalid call data raises appropriate error

        Why needed: Ensures proper error handling for malformed requests
        How it works: Tests with invalid call data format
        """
        invalid_call = (
            "0x" + GetLatestPendingTxCountHandler.METHOD_SELECTOR
        )  # Missing address parameter

        with pytest.raises(JSONRPCError) as exc_info:
            handle_consensus_data_call(
                mock_transactions_processor,
                CONSENSUS_DATA_CONTRACT_ADDRESS,
                invalid_call,
            )

        assert exc_info.value.code == -32602  # Invalid params error code
        assert exc_info.value.message and "Invalid parameters" in exc_info.value.message

    # ============================================================================
    # HEXADECIMAL FORMATTING TESTS
    # ============================================================================

    def test_hexadecimal_response_formatting(self):
        """
        Test 9: Proper hexadecimal formatting of various count values

        Why needed: Ensures correct uint256 encoding for all possible values
        How it works: Tests boundary values and common cases
        """
        test_cases = [
            (0, "0x0000000000000000000000000000000000000000000000000000000000000000"),
            (1, "0x0000000000000000000000000000000000000000000000000000000000000001"),
            (5, "0x0000000000000000000000000000000000000000000000000000000000000005"),
            (255, "0x00000000000000000000000000000000000000000000000000000000000000ff"),
            (
                1000,
                "0x00000000000000000000000000000000000000000000000000000000000003e8",
            ),
            (
                2**32 - 1,
                "0x00000000000000000000000000000000000000000000000000000000ffffffff",
            ),
        ]

        for count, expected_hex in test_cases:
            # Convert count to 32-byte big-endian hex string
            result = eth_utils.hexadecimal.encode_hex(
                count.to_bytes(32, byteorder="big")
            )
            assert result == expected_hex

    def test_large_pending_count_values(
        self, mock_transactions_processor, sample_call_data
    ):
        """
        Test 10: Handle large pending transaction counts correctly

        Why needed: Ensures system handles edge cases and large values
        How it works: Tests with various large count values
        """
        large_counts = [0, 1, 100, 2**32 - 1, 2**64 - 1]

        for count in large_counts:
            mock_transactions_processor.get_pending_transaction_count_for_address.return_value = (
                count
            )

            result = handle_consensus_data_call(
                mock_transactions_processor,
                CONSENSUS_DATA_CONTRACT_ADDRESS,
                sample_call_data,
            )

            # Verify result format
            assert result is not None
            assert result.startswith("0x")
            assert len(result) == 66  # 0x + 64 hex chars

            # Verify the actual count value
            result_int = int(result, 16)
            assert result_int == count

    # ============================================================================
    # ETH_CALL INTEGRATION TESTS
    # ============================================================================

    @pytest.mark.asyncio
    async def test_eth_call_integration_with_consensus_data(
        self,
        mock_session,
        mock_accounts_manager,
        mock_msg_handler,
        mock_transactions_parser,
        mock_validators_manager,
        mock_genvm_manager,
        mock_transactions_processor,
        sample_call_data,
    ):
        """
        Test 11: Full eth_call integration with ConsensusData interception

        Why needed: Verifies end-to-end flow from eth_call to interceptor
        How it works: Simulates complete eth_call request with mocked dependencies
        """
        params = {
            "from": "0x1111111111111111111111111111111111111111",
            "to": CONSENSUS_DATA_CONTRACT_ADDRESS,
            "data": sample_call_data,
        }

        mock_transactions_processor.get_pending_transaction_count_for_address.return_value = (
            5
        )

        result = await eth_call(
            mock_session,
            mock_accounts_manager,
            mock_msg_handler,
            mock_transactions_parser,
            mock_validators_manager,
            mock_genvm_manager,
            mock_transactions_processor,
            params,
        )

        # Should return hex-encoded uint256 for count 5
        expected = "0x0000000000000000000000000000000000000000000000000000000000000005"
        assert result == expected

        # Verify processor was called correctly
        mock_transactions_processor.get_pending_transaction_count_for_address.assert_called_once_with(
            "0x1234567890123456789012345678901234567890"
        )

    @pytest.mark.asyncio
    async def test_eth_call_passthrough_for_non_consensus_data(
        self,
        mock_session,
        mock_accounts_manager,
        mock_msg_handler,
        mock_transactions_parser,
        mock_validators_manager,
        mock_genvm_manager,
        mock_transactions_processor,
    ):
        """
        Test 12: Non-ConsensusData calls pass through to normal processing

        Why needed: Ensures other contracts still work normally
        How it works: Tests eth_call with different contract address
        """
        other_contract = "0x9999999999999999999999999999999999999999"
        params = {
            "from": "0x1111111111111111111111111111111111111111",
            "to": other_contract,
            "data": "0xabcdef",
        }

        # Mock normal contract execution
        mock_receipt = MagicMock()
        mock_receipt.execution_result = ExecutionResultStatus.SUCCESS
        mock_receipt.result = b"\x00" + b"normal_result"

        with patch("backend.protocol_rpc.endpoints.Node") as mock_node_class:
            mock_node = MagicMock()
            mock_node.get_contract_data = AsyncMock(return_value=mock_receipt)
            mock_node_class.return_value = mock_node

            with patch(
                "backend.protocol_rpc.endpoints.get_client_session_id",
                return_value="test",
            ):
                with patch("backend.protocol_rpc.endpoints.ContractSnapshot"):
                    result = await eth_call(
                        mock_session,
                        mock_accounts_manager,
                        mock_msg_handler,
                        mock_transactions_parser,
                        mock_validators_manager,
                        mock_genvm_manager,
                        mock_transactions_processor,
                        params,
                    )

                    # Should return normal result, not intercepted
                    assert result.startswith("0x")
                    # Should not be our intercepted value
                    assert (
                        result
                        != "0x0000000000000000000000000000000000000000000000000000000000000005"
                    )

                    # Interceptor should not be called
                    mock_transactions_processor.get_pending_transaction_count_for_address.assert_not_called()

    @pytest.mark.asyncio
    async def test_eth_call_with_invalid_addresses(
        self,
        mock_session,
        mock_accounts_manager,
        mock_msg_handler,
        mock_transactions_parser,
        mock_validators_manager,
        mock_genvm_manager,
        mock_transactions_processor,
    ):
        """
        Test 13: eth_call validates addresses before processing

        Why needed: Ensures address validation happens before interception
        How it works: Tests with invalid addresses
        """
        params = {
            "from": "invalid_address",
            "to": CONSENSUS_DATA_CONTRACT_ADDRESS,
            "data": "0xfe4cfca7" + "0" * 64,
        }

        # Mock invalid address
        mock_accounts_manager.is_valid_address.return_value = False

        with pytest.raises(InvalidAddressError):
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

    @pytest.mark.asyncio
    async def test_eth_call_without_from_address(
        self,
        mock_session,
        mock_accounts_manager,
        mock_msg_handler,
        mock_transactions_parser,
        mock_validators_manager,
        mock_genvm_manager,
        mock_transactions_processor,
    ):
        """
        Test 14: eth_call handles missing 'from' address with ConsensusData interception

        Why needed: Some eth_call requests don't specify sender
        How it works: Tests that interceptor now works even when 'from' is missing
                     (interceptor moved before early return check)
        """
        params = {
            # No 'from' address
            "to": CONSENSUS_DATA_CONTRACT_ADDRESS,
            "data": "0xfe4cfca7" + "0" * 64,
        }

        mock_transactions_processor.get_pending_transaction_count_for_address.return_value = (
            7
        )

        result = await eth_call(
            mock_session,
            mock_accounts_manager,
            mock_msg_handler,
            mock_transactions_parser,
            mock_validators_manager,
            mock_genvm_manager,
            mock_transactions_processor,
            params,
        )

        # Should return hex-encoded count from ConsensusData interceptor (now works without 'from')
        expected = "0x0000000000000000000000000000000000000000000000000000000000000007"
        assert result == expected

        # Verify interceptor was called correctly (extracts 20-byte address from 32-byte parameter)
        mock_transactions_processor.get_pending_transaction_count_for_address.assert_called_once_with(
            "0x0000000000000000000000000000000000000000"
        )

    # ============================================================================
    # ERROR HANDLING TESTS
    # ============================================================================

    def test_database_query_error_handling(
        self, mock_transactions_processor, sample_call_data
    ):
        """
        Test 15: Handle database errors gracefully

        Why needed: Ensures system handles database failures properly
        How it works: Simulates database exception using SQLAlchemyError
        """
        from sqlalchemy.exc import SQLAlchemyError

        mock_transactions_processor.get_pending_transaction_count_for_address.side_effect = SQLAlchemyError(
            "Database connection error"
        )

        with pytest.raises(JSONRPCError) as exc_info:
            handle_consensus_data_call(
                mock_transactions_processor,
                CONSENSUS_DATA_CONTRACT_ADDRESS,
                sample_call_data,
            )

        assert exc_info.value.code == -32000  # Internal error
        assert (
            exc_info.value.message
            and "Database error querying pending transaction count"
            in exc_info.value.message
        )

    # ============================================================================
    # ADDRESS NORMALIZATION TESTS
    # ============================================================================

    def test_consensus_data_contract_address_normalization(self):
        """
        Test 16: Contract address comparison is case-insensitive

        Why needed: Ethereum addresses can be in different cases (checksum format)
        How it works: Tests that all case variations match correctly
        """
        addresses = [
            "0x88B0F18613Db92Bf970FfE264E02496e20a74D16",  # Mixed case (checksum)
            "0x88B0F18613DB92BF970FFE264E02496E20A74D16",  # Upper case
            "0x88b0f18613db92bf970ffe264e02496e20a74d16",  # Lower case
        ]

        # All should be detected as ConsensusData contract
        for addr in addresses:
            assert is_consensus_data_contract_call(addr)

        # All should normalize to same value
        normalized = [addr.lower() for addr in addresses]
        assert len(set(normalized)) == 1
