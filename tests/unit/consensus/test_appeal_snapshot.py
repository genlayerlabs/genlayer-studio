"""Tests for contract snapshot handling during appeal rounds.

Regression test for the bug where appeal validators received a saved
empty snapshot instead of a fresh one from the factory, causing GenVM
to crash with 'NoneType' errors when accessing root storage.
"""

from unittest.mock import Mock

from backend.consensus.base import TransactionContext
from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.domain.types import Transaction, TransactionType


def _make_snapshot(states=None):
    """Create a ContractSnapshot from_dict (same path as DB deserialization)."""
    return ContractSnapshot.from_dict(
        {
            "contract_address": "0xcontract",
            "states": states or {"accepted": {}, "finalized": {}},
        }
    )


def _make_transaction(*, tx_type=TransactionType.RUN_CONTRACT, contract_snapshot=None):
    """Build a minimal Transaction for context creation."""
    tx = Mock(spec=Transaction)
    tx.type = tx_type
    tx.to_address = "0xcontract"
    tx.contract_snapshot = contract_snapshot
    tx.consensus_data = Mock()
    tx.consensus_data.leader_receipt = None
    return tx


def _make_context(transaction, factory_snapshot=None):
    """Create a TransactionContext with mocked dependencies."""
    default_snapshot = factory_snapshot or _make_snapshot(
        {"accepted": {"slot0": "real_data"}, "finalized": {}}
    )
    # Factory snapshots need balance for hydration
    if not hasattr(default_snapshot, "balance"):
        default_snapshot.balance = 0
    factory = Mock(return_value=default_snapshot)
    context = TransactionContext(
        transaction=transaction,
        transactions_processor=Mock(),
        chain_snapshot=None,
        accounts_manager=Mock(),
        contract_snapshot_factory=factory,
        contract_processor=Mock(),
        node_factory=Mock(),
        msg_handler=Mock(),
        consensus_service=Mock(),
        validators_snapshot=None,
        genvm_manager=Mock(),
    )
    return context, factory


class TestAppealSnapshotLoading:
    """TransactionContext should always use factory for appeal validators."""

    def test_no_saved_snapshot_uses_factory(self):
        """When transaction has no saved snapshot, factory is called."""
        tx = _make_transaction(contract_snapshot=None)
        context, factory = _make_context(tx)

        factory.assert_called_once_with("0xcontract")
        assert context.contract_snapshot is not None
        assert context.contract_snapshot.states["accepted"] == {"slot0": "real_data"}

    def test_saved_snapshot_with_empty_states_uses_factory(self):
        """Saved snapshot with empty states should be ignored in favor of factory.

        The snapshot was saved during AcceptedState with empty states
        (before the leader's execution populated them). On appeal, the
        factory should be called to get a fresh snapshot with real state.
        """
        empty_snapshot = _make_snapshot({"accepted": {}, "finalized": {}})
        tx = _make_transaction(contract_snapshot=empty_snapshot)
        context, factory = _make_context(tx)

        factory.assert_called_once_with("0xcontract")
        assert context.contract_snapshot.states["accepted"] == {"slot0": "real_data"}

    def test_saved_snapshot_with_real_states_uses_it(self):
        """When snapshot has real state data, using it is fine."""
        real_snapshot = _make_snapshot({"accepted": {"slot0": "data"}, "finalized": {}})
        tx = _make_transaction(contract_snapshot=real_snapshot)
        context, factory = _make_context(tx)

        factory.assert_not_called()
        assert context.contract_snapshot.states["accepted"] == {"slot0": "data"}

    def test_deploy_transaction_with_empty_snapshot_on_appeal(self):
        """Deploy txs are especially affected — snapshot is always empty at save time.

        When a deploy tx reaches ACCEPTED, the contract_snapshot is saved
        with states={'accepted': {}, 'finalized': {}} because the deploy
        hasn't committed to DB yet. On appeal, the factory must be called
        to get the real contract state from the database.
        """
        empty_snapshot = _make_snapshot({"accepted": {}, "finalized": {}})
        tx = _make_transaction(
            tx_type=TransactionType.DEPLOY_CONTRACT,
            contract_snapshot=empty_snapshot,
        )
        context, factory = _make_context(tx)

        factory.assert_called_once_with("0xcontract")
        assert context.contract_snapshot.states["accepted"] == {"slot0": "real_data"}

    def test_send_transaction_skips_snapshot(self):
        """SEND transactions don't need contract snapshots."""
        tx = _make_transaction(tx_type=TransactionType.SEND)
        context, factory = _make_context(tx)

        factory.assert_not_called()
        assert not hasattr(context, "contract_snapshot")
