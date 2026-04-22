"""Regression tests for origin_address tracking through transaction chains."""

import pytest
from unittest.mock import patch, MagicMock
import os
from web3 import Web3
from web3.providers import BaseProvider

from backend.database_handler.transactions_processor import (
    TransactionsProcessor,
)


_tx_counter = 100_000  # offset to avoid collisions with other test files


def _make_tx(tp, **overrides):
    """Create a transaction with sensible defaults and unique hash."""
    global _tx_counter
    _tx_counter += 1
    defaults = dict(
        from_address="0x9F0e84243496AcFB3Cd99D02eA59673c05901501",
        to_address="0xAcec3A6d871C25F591aBd4fC24054e524BBbF794",
        data={"key": "value"},
        value=0,
        type=2,
        nonce=0,
        leader_only=True,
        config_rotation_rounds=3,
        triggered_by_hash=None,
        transaction_hash=f"0x{_tx_counter:064x}",
    )
    defaults.update(overrides)
    tx_hash = tp.insert_transaction(**{k: v for k, v in defaults.items()})
    tp.session.commit()
    return tx_hash


@pytest.fixture
def mock_env_and_web3_connected():
    with patch.dict(
        os.environ,
        {
            "HARDHAT_PORT": "8545",
            "HARDHAT_URL": "http://localhost",
            "HARDHAT_PRIVATE_KEY": "0x0123456789",
        },
    ), patch("web3.Web3.HTTPProvider"):
        web3_instance = Web3(MagicMock(spec=BaseProvider))
        web3_instance.eth = MagicMock()
        web3_instance.eth.accounts = ["0x0000000000000000000000000000000000000000"]
        call_count = {"count": 0}

        def mock_get_transaction_count(address, block_identifier="latest"):
            result = call_count["count"]
            call_count["count"] += 1
            return result

        web3_instance.eth.get_transaction_count = mock_get_transaction_count
        web3_instance.is_connected = MagicMock(return_value=True)

        with patch(
            "backend.database_handler.transactions_processor.Web3",
            return_value=web3_instance,
        ):
            yield web3_instance


EOA = "0x1111111111111111111111111111111111111111"
CONTRACT_A = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
CONTRACT_B = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
CONTRACT_C = "0xCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"


class TestOriginAddressTopLevel:
    """Top-level transactions should set origin_address = from_address."""

    def test_origin_defaults_to_from_address(
        self, tp: TransactionsProcessor, mock_env_and_web3_connected
    ):
        tp.web3 = mock_env_and_web3_connected
        tx_hash = _make_tx(tp, from_address=EOA, to_address=CONTRACT_A)

        tx = tp.get_transaction_by_hash(tx_hash)
        assert tx["origin_address"] == EOA

    def test_origin_defaults_when_not_provided(
        self, tp: TransactionsProcessor, mock_env_and_web3_connected
    ):
        """When origin_address param is omitted, it falls back to from_address."""
        tp.web3 = mock_env_and_web3_connected
        tx_hash = _make_tx(tp, from_address=EOA, to_address=CONTRACT_A)

        tx = tp.get_transaction_by_hash(tx_hash)
        assert tx["origin_address"] == EOA
        assert tx["from_address"] == EOA


class TestOriginAddressSubCalls:
    """Sub-call transactions should preserve the root origin_address."""

    def test_subcall_preserves_origin(
        self, tp: TransactionsProcessor, mock_env_and_web3_connected
    ):
        """Contract A called by EOA emits message to Contract B.
        Contract B's transaction should have origin_address = EOA."""
        tp.web3 = mock_env_and_web3_connected

        # Top-level: EOA -> Contract A
        parent_hash = _make_tx(tp, from_address=EOA, to_address=CONTRACT_A)

        # Sub-call: Contract A -> Contract B (origin propagated from parent)
        child_hash = _make_tx(
            tp,
            from_address=CONTRACT_A,
            to_address=CONTRACT_B,
            triggered_by_hash=parent_hash,
            origin_address=EOA,  # propagated from parent
        )

        child_tx = tp.get_transaction_by_hash(child_hash)
        assert child_tx["from_address"] == CONTRACT_A
        assert child_tx["origin_address"] == EOA

    def test_deep_chain_preserves_origin(
        self, tp: TransactionsProcessor, mock_env_and_web3_connected
    ):
        """EOA -> A -> B -> C: all should have origin_address = EOA."""
        tp.web3 = mock_env_and_web3_connected

        tx1 = _make_tx(tp, from_address=EOA, to_address=CONTRACT_A)

        tx2 = _make_tx(
            tp,
            from_address=CONTRACT_A,
            to_address=CONTRACT_B,
            triggered_by_hash=tx1,
            origin_address=EOA,
        )

        tx3 = _make_tx(
            tp,
            from_address=CONTRACT_B,
            to_address=CONTRACT_C,
            triggered_by_hash=tx2,
            origin_address=EOA,
        )

        for tx_hash in [tx1, tx2, tx3]:
            tx = tp.get_transaction_by_hash(tx_hash)
            assert (
                tx["origin_address"] == EOA
            ), f"Transaction {tx_hash} has origin_address={tx['origin_address']}, expected {EOA}"


class TestOriginAddressExplicitOverride:
    """origin_address can be explicitly set (for gen_call use case)."""

    def test_explicit_origin_address(
        self, tp: TransactionsProcessor, mock_env_and_web3_connected
    ):
        """When origin_address is explicitly provided, it should be used."""
        tp.web3 = mock_env_and_web3_connected
        custom_origin = "0xDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD"

        tx_hash = _make_tx(
            tp,
            from_address=EOA,
            to_address=CONTRACT_A,
            origin_address=custom_origin,
        )

        tx = tp.get_transaction_by_hash(tx_hash)
        assert tx["origin_address"] == custom_origin
        assert tx["from_address"] == EOA


class TestOriginAddressNullFallback:
    """Old transactions with NULL origin_address should be handled gracefully."""

    def test_null_origin_in_domain_type(self):
        """Transaction domain type handles missing origin_address."""
        from backend.domain.types import Transaction, TransactionType
        from backend.database_handler.models import TransactionStatus

        tx = Transaction(
            hash="0x123",
            status=TransactionStatus.PENDING,
            type=TransactionType.RUN_CONTRACT,
            from_address=EOA,
            to_address=CONTRACT_A,
            origin_address=None,
        )
        assert tx.origin_address is None

        # to_dict includes it
        d = tx.to_dict()
        assert "origin_address" in d
        assert d["origin_address"] is None

    def test_from_dict_without_origin(self):
        """from_dict handles missing origin_address (old data)."""
        from backend.domain.types import Transaction, TransactionType
        from backend.database_handler.models import TransactionStatus

        data = {
            "hash": "0x123",
            "status": TransactionStatus.PENDING.value,
            "type": TransactionType.RUN_CONTRACT.value,
            "from_address": EOA,
            "to_address": CONTRACT_A,
        }
        tx = Transaction.from_dict(data)
        assert tx.origin_address is None
