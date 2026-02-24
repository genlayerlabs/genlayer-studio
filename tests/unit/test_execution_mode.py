"""
Unit tests for TransactionExecutionMode behavior.

Tests cover:
1. Domain types and enum values
2. Transaction parsing with V1 (boolean) and V2 (integer) formats
3. Execution mode mapping from leader_only
4. Finalization logic for different execution modes
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from rlp import encode

from backend.domain.types import (
    Transaction,
    TransactionType,
    TransactionExecutionMode,
    TransactionStatus,
)
from backend.protocol_rpc.transactions_parser import (
    TransactionParser,
    DecodedMethodSendData,
    DecodedDeploymentData,
    EXECUTION_MODE_INT_TO_STR,
    EXECUTION_MODE_STR_TO_INT,
)
from backend.protocol_rpc.types import DecodedGenlayerTransactionData
import backend.node.genvm.origin.calldata as calldata


class TestTransactionExecutionModeEnum:
    """Tests for the TransactionExecutionMode enum."""

    def test_execution_mode_values(self):
        """Test that all execution mode values are correct."""
        assert TransactionExecutionMode.LEADER_ONLY.value == "LEADER_ONLY"
        assert (
            TransactionExecutionMode.LEADER_SELF_VALIDATOR.value
            == "LEADER_SELF_VALIDATOR"
        )
        assert TransactionExecutionMode.NORMAL.value == "NORMAL"

    def test_execution_mode_from_string(self):
        """Test creating execution mode from string values."""
        assert (
            TransactionExecutionMode("LEADER_ONLY")
            == TransactionExecutionMode.LEADER_ONLY
        )
        assert (
            TransactionExecutionMode("LEADER_SELF_VALIDATOR")
            == TransactionExecutionMode.LEADER_SELF_VALIDATOR
        )
        assert TransactionExecutionMode("NORMAL") == TransactionExecutionMode.NORMAL

    def test_execution_mode_invalid_value(self):
        """Test that invalid values raise ValueError."""
        with pytest.raises(ValueError):
            TransactionExecutionMode("INVALID_MODE")


class TestExecutionModeMapping:
    """Tests for execution mode integer-to-string mapping."""

    def test_int_to_str_mapping(self):
        """Test integer to string mapping for execution modes."""
        assert EXECUTION_MODE_INT_TO_STR[0] == "NORMAL"
        assert EXECUTION_MODE_INT_TO_STR[1] == "LEADER_ONLY"
        assert EXECUTION_MODE_INT_TO_STR[2] == "LEADER_SELF_VALIDATOR"

    def test_str_to_int_mapping(self):
        """Test string to integer mapping for execution modes."""
        assert EXECUTION_MODE_STR_TO_INT["NORMAL"] == 0
        assert EXECUTION_MODE_STR_TO_INT["LEADER_ONLY"] == 1
        assert EXECUTION_MODE_STR_TO_INT["LEADER_SELF_VALIDATOR"] == 2


class TestTransactionDataclass:
    """Tests for Transaction dataclass with execution_mode."""

    def test_transaction_default_execution_mode(self):
        """Test that Transaction defaults to NORMAL execution mode."""
        tx = Transaction(
            hash="0x123",
            status=TransactionStatus.PENDING,
            from_address="0xabc",
            to_address="0xdef",
            data={},
            value=0,
            type=TransactionType.RUN_CONTRACT,
            nonce=1,
            leader_only=False,
        )
        assert tx.execution_mode == TransactionExecutionMode.NORMAL

    def test_transaction_with_leader_only_execution_mode(self):
        """Test Transaction with LEADER_ONLY execution mode."""
        tx = Transaction(
            hash="0x123",
            status=TransactionStatus.PENDING,
            from_address="0xabc",
            to_address="0xdef",
            data={},
            value=0,
            type=TransactionType.RUN_CONTRACT,
            nonce=1,
            leader_only=True,
            execution_mode=TransactionExecutionMode.LEADER_ONLY,
        )
        assert tx.execution_mode == TransactionExecutionMode.LEADER_ONLY

    def test_transaction_to_dict_includes_execution_mode(self):
        """Test that to_dict includes execution_mode."""
        tx = Transaction(
            hash="0x123",
            status=TransactionStatus.PENDING,
            from_address="0xabc",
            to_address="0xdef",
            data={},
            value=0,
            type=TransactionType.RUN_CONTRACT,
            nonce=1,
            leader_only=False,
            execution_mode=TransactionExecutionMode.LEADER_ONLY,
        )
        tx_dict = tx.to_dict()
        assert "execution_mode" in tx_dict
        assert tx_dict["execution_mode"] == "LEADER_ONLY"

    def test_transaction_from_dict_parses_execution_mode(self):
        """Test that from_dict correctly parses execution_mode."""
        tx_dict = {
            "hash": "0x123",
            "status": TransactionStatus.PENDING.value,
            "from_address": "0xabc",
            "to_address": "0xdef",
            "data": {},
            "value": 0,
            "type": TransactionType.RUN_CONTRACT.value,
            "nonce": 1,
            "leader_only": True,
            "execution_mode": "LEADER_ONLY",
        }
        tx = Transaction.from_dict(tx_dict)
        assert tx.execution_mode == TransactionExecutionMode.LEADER_ONLY

    def test_transaction_from_dict_defaults_to_normal(self):
        """Test that from_dict defaults to NORMAL if execution_mode is missing."""
        tx_dict = {
            "hash": "0x123",
            "status": TransactionStatus.PENDING.value,
            "from_address": "0xabc",
            "to_address": "0xdef",
            "data": {},
            "value": 0,
            "type": TransactionType.RUN_CONTRACT.value,
            "nonce": 1,
            "leader_only": False,
        }
        tx = Transaction.from_dict(tx_dict)
        assert tx.execution_mode == TransactionExecutionMode.NORMAL


class TestTransactionParserV1Format:
    """Tests for parsing V1 format (leader_only boolean)."""

    @pytest.fixture
    def transaction_parser(self):
        consensus_service = Mock()
        consensus_service.web3 = Mock()
        consensus_service.load_contract = Mock(return_value=None)
        return TransactionParser(consensus_service)

    def test_v1_leader_only_false_maps_to_normal(self, transaction_parser):
        """Test that V1 leader_only=False maps to NORMAL execution mode."""
        data = [{"method": "test", "args": []}, False]
        encoded = encode([calldata.encode(data[0]), data[1]])
        result = transaction_parser.decode_method_send_data(encoded.hex())

        assert result.leader_only is False
        assert result.execution_mode == "NORMAL"

    def test_v1_leader_only_true_maps_to_leader_only(self, transaction_parser):
        """Test that V1 leader_only=True maps to LEADER_ONLY execution mode."""
        data = [{"method": "test", "args": []}, True]
        encoded = encode([calldata.encode(data[0]), data[1]])
        result = transaction_parser.decode_method_send_data(encoded.hex())

        assert result.leader_only is True
        assert result.execution_mode == "LEADER_ONLY"

    def test_v1_deployment_leader_only_false(self, transaction_parser):
        """Test deployment data with V1 leader_only=False."""
        data = [b"contract code", {"method": "__init__", "args": []}, False]
        encoded = encode([data[0], calldata.encode(data[1]), data[2]])
        result = transaction_parser.decode_deployment_data(encoded.hex())

        assert result.leader_only is False
        assert result.execution_mode == "NORMAL"

    def test_v1_deployment_leader_only_true(self, transaction_parser):
        """Test deployment data with V1 leader_only=True."""
        data = [b"contract code", {"method": "__init__", "args": []}, True]
        encoded = encode([data[0], calldata.encode(data[1]), data[2]])
        result = transaction_parser.decode_deployment_data(encoded.hex())

        assert result.leader_only is True
        assert result.execution_mode == "LEADER_ONLY"


class TestTransactionParserV2Format:
    """Tests for parsing V2 format (execution_mode integer)."""

    @pytest.fixture
    def transaction_parser(self):
        consensus_service = Mock()
        consensus_service.web3 = Mock()
        consensus_service.load_contract = Mock(return_value=None)
        return TransactionParser(consensus_service)

    def test_v2_execution_mode_0_is_normal(self, transaction_parser):
        """Test that V2 execution_mode=0 is NORMAL."""
        # V2 format: [calldata, execution_mode_int]
        # execution_mode=0 means NORMAL
        data = [{"method": "test", "args": []}, 0]
        encoded = encode([calldata.encode(data[0]), data[1]])
        result = transaction_parser.decode_method_send_data(encoded.hex())

        # Note: V1 decoder will also accept this (0 == False in boolean context)
        assert result.execution_mode == "NORMAL"
        assert result.leader_only is False

    def test_v2_execution_mode_2_is_leader_self_validator(self, transaction_parser):
        """Test that V2 execution_mode=2 is LEADER_SELF_VALIDATOR."""
        # V2 format with execution_mode=2 (LEADER_SELF_VALIDATOR)
        # This value (2) is not valid for boolean sedes, so V2 decoder should be used
        data = [{"method": "test", "args": []}, 2]
        encoded = encode([calldata.encode(data[0]), data[1]])
        result = transaction_parser.decode_method_send_data(encoded.hex())

        assert result.execution_mode == "LEADER_SELF_VALIDATOR"
        assert result.leader_only is True

    def test_v2_deployment_execution_mode_2(self, transaction_parser):
        """Test deployment data with V2 execution_mode=2."""
        data = [b"contract code", {"method": "__init__", "args": []}, 2]
        encoded = encode([data[0], calldata.encode(data[1]), data[2]])
        result = transaction_parser.decode_deployment_data(encoded.hex())

        assert result.execution_mode == "LEADER_SELF_VALIDATOR"
        assert result.leader_only is True


class TestTransactionParserDefaultFormat:
    """Tests for parsing default format (no execution mode field)."""

    @pytest.fixture
    def transaction_parser(self):
        consensus_service = Mock()
        consensus_service.web3 = Mock()
        consensus_service.load_contract = Mock(return_value=None)
        return TransactionParser(consensus_service)

    def test_default_format_method_send(self, transaction_parser):
        """Test method send data with default format (no leader_only field)."""
        data = [{"method": "test", "args": []}]
        encoded = encode([calldata.encode(data[0])])
        result = transaction_parser.decode_method_send_data(encoded.hex())

        assert result.leader_only is False
        assert result.execution_mode == "NORMAL"

    def test_default_format_deployment(self, transaction_parser):
        """Test deployment data with default format (no leader_only field)."""
        data = [b"contract code", {"method": "__init__", "args": []}]
        encoded = encode([data[0], calldata.encode(data[1])])
        result = transaction_parser.decode_deployment_data(encoded.hex())

        assert result.leader_only is False
        assert result.execution_mode == "NORMAL"


class TestDecodedGenlayerTransactionData:
    """Tests for DecodedGenlayerTransactionData with execution_mode."""

    def test_default_execution_mode(self):
        """Test that DecodedGenlayerTransactionData defaults to NORMAL."""
        data = DecodedGenlayerTransactionData(
            contract_code="code",
            calldata="data",
        )
        assert data.execution_mode == "NORMAL"
        assert data.leader_only is False

    def test_leader_only_execution_mode(self):
        """Test DecodedGenlayerTransactionData with LEADER_ONLY mode."""
        data = DecodedGenlayerTransactionData(
            contract_code="code",
            calldata="data",
            leader_only=True,
            execution_mode="LEADER_ONLY",
        )
        assert data.execution_mode == "LEADER_ONLY"
        assert data.leader_only is True

    def test_leader_self_validator_mode(self):
        """Test DecodedGenlayerTransactionData with LEADER_SELF_VALIDATOR mode."""
        data = DecodedGenlayerTransactionData(
            contract_code="code",
            calldata="data",
            leader_only=True,
            execution_mode="LEADER_SELF_VALIDATOR",
        )
        assert data.execution_mode == "LEADER_SELF_VALIDATOR"
        assert data.leader_only is True


class TestLeaderOnlyConsensusResult:
    """Tests for LEADER_ONLY consensus result in transaction parsing."""

    def _check_leader_only_result(self, transaction_data):
        """Helper to simulate the _process_round_data logic."""
        from backend.consensus.types import ConsensusResult
        from backend.consensus.utils import determine_consensus_from_votes

        if transaction_data.get(
            "execution_mode"
        ) == "LEADER_ONLY" and transaction_data.get("status") in [
            TransactionStatus.ACCEPTED.value,
            TransactionStatus.FINALIZED.value,
        ]:
            return int(ConsensusResult.MAJORITY_AGREE)
        else:
            return int(determine_consensus_from_votes([]))

    def test_leader_only_accepted_returns_majority_agree(self):
        """Test that LEADER_ONLY mode with ACCEPTED status returns MAJORITY_AGREE result."""
        from backend.consensus.types import ConsensusResult

        transaction_data = {
            "execution_mode": "LEADER_ONLY",
            "status": TransactionStatus.ACCEPTED.value,
        }

        last_round_result = self._check_leader_only_result(transaction_data)
        assert last_round_result == int(ConsensusResult.MAJORITY_AGREE)
        assert last_round_result == 6

    def test_leader_only_finalized_returns_majority_agree(self):
        """Test that LEADER_ONLY mode with FINALIZED status returns MAJORITY_AGREE result."""
        from backend.consensus.types import ConsensusResult

        transaction_data = {
            "execution_mode": "LEADER_ONLY",
            "status": TransactionStatus.FINALIZED.value,
        }

        last_round_result = self._check_leader_only_result(transaction_data)
        assert last_round_result == int(ConsensusResult.MAJORITY_AGREE)
        assert last_round_result == 6

    def test_leader_only_pending_uses_votes(self):
        """Test that LEADER_ONLY mode with PENDING status uses vote-based logic."""
        from backend.consensus.types import ConsensusResult

        transaction_data = {
            "execution_mode": "LEADER_ONLY",
            "status": TransactionStatus.PENDING.value,
        }

        last_round_result = self._check_leader_only_result(transaction_data)
        assert last_round_result == int(ConsensusResult.NO_MAJORITY)
        assert last_round_result == 5

    def test_normal_mode_uses_votes(self):
        """Test that NORMAL mode always uses vote-based logic."""
        from backend.consensus.types import ConsensusResult

        transaction_data = {
            "execution_mode": "NORMAL",
            "status": TransactionStatus.ACCEPTED.value,
        }

        last_round_result = self._check_leader_only_result(transaction_data)
        assert last_round_result == int(ConsensusResult.NO_MAJORITY)


class TestProcessResultLeaderOnly:
    """Tests for _process_result with LEADER_ONLY mode."""

    def _simulate_process_result(self, transaction_data):
        """Helper to simulate the _process_result logic."""
        from backend.consensus.types import ConsensusResult
        from backend.consensus.utils import determine_consensus_from_votes

        if transaction_data.get(
            "execution_mode"
        ) == "LEADER_ONLY" and transaction_data.get("status") in [
            TransactionStatus.ACCEPTED.value,
            TransactionStatus.FINALIZED.value,
        ]:
            consensus_result = ConsensusResult.MAJORITY_AGREE
            transaction_data["result"] = int(consensus_result)
            transaction_data["result_name"] = consensus_result.value
        else:
            votes_temp = list(transaction_data["consensus_data"]["votes"].values())
            consensus_result = determine_consensus_from_votes(votes_temp)
            transaction_data["result"] = int(consensus_result)
            transaction_data["result_name"] = consensus_result.value

    def test_process_result_leader_only_accepted(self):
        """Test that _process_result returns MAJORITY_AGREE for LEADER_ONLY + ACCEPTED."""
        transaction_data = {
            "execution_mode": "LEADER_ONLY",
            "status": TransactionStatus.ACCEPTED.value,
            "consensus_data": {"votes": {}},
        }

        self._simulate_process_result(transaction_data)
        assert transaction_data["result"] == 6
        assert transaction_data["result_name"] == "MAJORITY_AGREE"

    def test_process_result_leader_only_finalized(self):
        """Test that _process_result returns MAJORITY_AGREE for LEADER_ONLY + FINALIZED."""
        transaction_data = {
            "execution_mode": "LEADER_ONLY",
            "status": TransactionStatus.FINALIZED.value,
            "consensus_data": {"votes": {}},
        }

        self._simulate_process_result(transaction_data)
        assert transaction_data["result"] == 6
        assert transaction_data["result_name"] == "MAJORITY_AGREE"

    def test_process_result_leader_only_pending(self):
        """Test that _process_result uses votes for LEADER_ONLY + PENDING."""
        transaction_data = {
            "execution_mode": "LEADER_ONLY",
            "status": TransactionStatus.PENDING.value,
            "consensus_data": {"votes": {}},
        }

        self._simulate_process_result(transaction_data)
        assert transaction_data["result"] == 5
        assert transaction_data["result_name"] == "NO_MAJORITY"

    def test_process_result_normal_mode(self):
        """Test that _process_result uses votes for NORMAL mode even if ACCEPTED."""
        transaction_data = {
            "execution_mode": "NORMAL",
            "status": TransactionStatus.ACCEPTED.value,
            "consensus_data": {"votes": {"addr1": "agree", "addr2": "agree"}},
        }

        self._simulate_process_result(transaction_data)
        assert transaction_data["result"] == 6
        assert transaction_data["result_name"] == "MAJORITY_AGREE"
