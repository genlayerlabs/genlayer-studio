"""Unit tests for improved methods in transactions_processor.py"""

from unittest.mock import Mock, patch
from backend.database_handler.transactions_processor import TransactionsProcessor
from backend.database_handler.models import EvmEnvelope, TransactionStatus, Transactions
from sqlalchemy import func
from sqlalchemy.orm import Session


class TestLeaderReceiptCompatibility:
    def setup_method(self):
        self.processor = TransactionsProcessor(Mock())

    def test_processor_helpers_accept_dict_shaped_leader_receipts(self):
        address = "0xaAaAaAaaAaAaAaaAaAAAAAAAAaaaAaAaAaaAaaAa"
        receipt = {
            "vote": "agree",
            "result": "AG9r",
            "node_config": {"address": address},
            "pending_transactions": [
                {
                    "address": "0xbBbBBBBbbBBBbbbBbbBbbbbBBbBbbbbBbBbbBBbB",
                    "calldata": "0x1234",
                    "value": 3,
                    "on": "accepted",
                    "code": "",
                    "salt_nonce": 0,
                }
            ],
        }
        transaction_data = {
            "hash": "0xabc",
            "from_address": address,
            "to_address": address,
            "data": {},
            "value": 0,
            "type": 1,
            "status": TransactionStatus.FINALIZED.value,
            "result": {},
            "consensus_data": {"leader_receipt": receipt},
            "gaslimit": 0,
            "nonce": 7,
            "created_at": "2026-06-24T12:00:00+00:00",
            "leader_only": True,
            "execution_mode": "LEADER_ONLY",
            "origin_address": None,
            "triggered_by": None,
            "triggered_on": None,
            "triggered_transactions": [],
            "appealed": False,
            "timestamp_awaiting_finalization": None,
            "appeal_failed": False,
            "appeal_undetermined": False,
            "consensus_history": {
                "consensus_results": [
                    {
                        "leader_result": receipt,
                        "validator_results": [],
                    }
                ]
            },
            "timestamp_appeal": None,
            "appeal_processing_time": None,
            "config_rotation_rounds": 3,
            "num_of_initial_validators": 1,
            "last_vote_timestamp": None,
            "rotation_count": 0,
            "appeal_leader_timeout": False,
            "leader_timeout_validators": [],
            "appeal_validators_timeout": [],
            "sim_config": None,
            "value_credited": False,
        }

        transaction_data = self.processor._prepare_basic_transaction_data(
            transaction_data
        )
        transaction_data = self.processor._process_execution_hash(transaction_data)
        transaction_data = self.processor._process_messages(transaction_data)
        transaction_data = self.processor._process_round_data(transaction_data)

        assert transaction_data["activator"] == address
        assert transaction_data["last_leader"] == address
        assert transaction_data["tx_execution_hash"].startswith("0x")
        assert transaction_data["messages"] == [
            {
                "messageType": "0",
                "recipient": "0xbBbBBBBbbBBBbbbBbbBbbbbBBbBbbbbBbBbbBBbB",
                "value": 3,
                "data": "0x1234",
                "onAcceptance": True,
            }
        ]
        assert transaction_data["last_round"]["validator_votes_name"] == ["AGREE"]


class FakeSnapshotArchive:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls = []

    def load_snapshot(self, session, tx_hash):
        self.calls.append((session, tx_hash))
        return self.snapshot


class TestArchivedContractSnapshotHydration:
    def test_hydrates_missing_contract_snapshot_from_archive(self):
        session = Mock(spec=Session)
        archive = FakeSnapshotArchive({"state": {"accepted": {"x": 1}}})
        processor = TransactionsProcessor(session, snapshot_archive=archive)
        transaction_data = {"hash": "0xabc", "contract_snapshot": None}

        processor._hydrate_archived_contract_snapshot(transaction_data)

        assert transaction_data["contract_snapshot"] == {
            "state": {"accepted": {"x": 1}}
        }
        assert archive.calls == [(session, "0xabc")]

    def test_keeps_hot_contract_snapshot_without_archive_lookup(self):
        session = Mock(spec=Session)
        archive = FakeSnapshotArchive({"state": "archived"})
        processor = TransactionsProcessor(session, snapshot_archive=archive)
        transaction_data = {"hash": "0xabc", "contract_snapshot": {"state": "hot"}}

        processor._hydrate_archived_contract_snapshot(transaction_data)

        assert transaction_data["contract_snapshot"] == {"state": "hot"}
        assert archive.calls == []


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
        mock_query.scalar.return_value = 4
        self.mock_session.query.return_value = mock_query

        # Execute
        result = self.processor.get_transaction_count(test_address)

        # Verify
        self.mock_web3.to_checksum_address.assert_called_once_with(test_address)
        query_expression = self.mock_session.query.call_args.args[0]
        assert str(query_expression) == str(func.max(EvmEnvelope.nonce))
        mock_query.filter.assert_called_once()
        # EVM address identity is case-insensitive in the durable nonce ledger.
        filter_call = mock_query.filter.call_args[0][0]
        assert str(filter_call.left) == "evm_envelopes.from_address"
        assert str(filter_call.right.value) == checksum_address.lower()
        assert result == 5

    def test_get_transaction_count_with_invalid_address(self):
        """Test get_transaction_count with invalid address that can't be checksummed"""
        # Setup
        test_address = "invalid_address"

        self.mock_web3.to_checksum_address.side_effect = Exception("Invalid address")

        # Mock database query with original address
        mock_query = Mock()
        mock_query.filter.return_value = mock_query
        mock_query.scalar.return_value = 2
        self.mock_session.query.return_value = mock_query

        # Execute
        result = self.processor.get_transaction_count(test_address)

        # Verify - should use original address after checksum fails
        self.mock_web3.to_checksum_address.assert_called_once_with(test_address)
        query_expression = self.mock_session.query.call_args.args[0]
        assert str(query_expression) == str(func.max(EvmEnvelope.nonce))
        mock_query.filter.assert_called_once()
        # Verify the filter uses the original address since checksum failed
        filter_call = mock_query.filter.call_args[0][0]
        assert str(filter_call.left) == "evm_envelopes.from_address"
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
        mock_query.scalar.return_value = None
        self.mock_session.query.return_value = mock_query

        # Execute
        result = self.processor.get_transaction_count(test_address)

        # Verify
        assert result == 0
        query_expression = self.mock_session.query.call_args.args[0]
        assert str(query_expression) == str(func.max(EvmEnvelope.nonce))
        mock_query.filter.assert_called_once()
        mock_query.scalar.assert_called_once()

    def test_get_transaction_count_with_multiple_transactions(self):
        """Test get_transaction_count correctly counts multiple transactions"""
        # Setup
        test_address = "0xABcdEF1234567890aBcDef1234567890AbCdEf12"

        self.mock_web3.to_checksum_address.return_value = test_address

        # Mock database query returning count of 7
        mock_query = Mock()
        mock_query.filter.return_value = mock_query
        mock_query.scalar.return_value = 6
        self.mock_session.query.return_value = mock_query

        # Execute
        result = self.processor.get_transaction_count(test_address)

        # Verify database is queried and correct count returned
        assert result == 7
        query_expression = self.mock_session.query.call_args.args[0]
        assert str(query_expression) == str(func.max(EvmEnvelope.nonce))
        mock_query.filter.assert_called_once()
        mock_query.scalar.assert_called_once()

    def test_get_transaction_count_database_query_structure(self):
        """Test that get_transaction_count queries database with correct structure"""
        # Setup
        test_address = "0xABcdEF1234567890aBcDef1234567890AbCdEf12"

        self.mock_web3.to_checksum_address.return_value = test_address

        # Mock database query
        mock_query = Mock()
        mock_filter = Mock()
        mock_query.filter.return_value = mock_filter
        mock_filter.scalar.return_value = 7
        self.mock_session.query.return_value = mock_query

        # Execute
        result = self.processor.get_transaction_count(test_address)

        # Verify correct database query structure
        query_expression = self.mock_session.query.call_args.args[0]
        assert str(query_expression) == str(func.max(EvmEnvelope.nonce))
        mock_query.filter.assert_called_once()
        mock_filter.scalar.assert_called_once()
        assert result == 8


def test_get_genlayer_transaction_count_keeps_child_sequence_separate_from_evm_nonce():
    session = Mock(spec=Session)
    query = Mock()
    query.filter.return_value = query
    query.count.return_value = 4
    session.query.return_value = query
    processor = TransactionsProcessor(session)
    processor.web3 = Mock()
    processor.web3.to_checksum_address.return_value = (
        "0xABcdEF1234567890aBcDef1234567890AbCdEf12"
    )

    assert (
        processor.get_genlayer_transaction_count(
            "0xabcdef1234567890abcdef1234567890abcdef12"
        )
        == 4
    )
    session.query.assert_called_once_with(Transactions)
    filtered = query.filter.call_args.args[0]
    assert str(filtered.left) == "transactions.from_address"


class TestSetTransactionAppealProcessingTime:
    """Test the improved set_transaction_appeal_processing_time method"""

    def setup_method(self, method):
        """Set up test fixtures"""
        self.mock_session = Mock(spec=Session)
        self.processor = TransactionsProcessor(Mock())
        self.processor.session = self.mock_session

    def test_appeal_processing_time_with_none_timestamp(self):
        """Test set_transaction_appeal_processing_time when timestamp_appeal is NULL (raw SQL WHERE filters it out)"""
        # Setup - raw SQL UPDATE with WHERE timestamp_appeal IS NOT NULL
        # When timestamp_appeal is NULL, rowcount == 0
        mock_result = Mock()
        mock_result.rowcount = 0
        self.mock_session.execute.return_value = mock_result

        # Execute
        with patch("builtins.print") as mock_print:
            self.processor.set_transaction_appeal_processing_time("test_hash")

        # Verify - should not commit and should print message
        self.mock_session.commit.assert_not_called()
        mock_print.assert_called_once()
        assert "not found or has no timestamp_appeal" in str(mock_print.call_args)

    def test_appeal_processing_time_transaction_not_found(self):
        """Test set_transaction_appeal_processing_time when transaction doesn't exist"""
        # Setup - raw SQL UPDATE returns rowcount 0 when tx doesn't exist
        mock_result = Mock()
        mock_result.rowcount = 0
        self.mock_session.execute.return_value = mock_result

        # Execute
        with patch("builtins.print") as mock_print:
            self.processor.set_transaction_appeal_processing_time("nonexistent_hash")

        # Verify
        self.mock_session.commit.assert_not_called()
        mock_print.assert_called_once()
        assert "not found or has no timestamp_appeal" in str(mock_print.call_args)
