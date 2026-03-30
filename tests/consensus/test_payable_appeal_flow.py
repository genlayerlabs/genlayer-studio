"""Test the payable value flow through consensus with appeals.

Scenario (from design spec):
  Round 1: Contract balance=10. Execute, emit on_accepted msg value=3.
           On acceptance: balance debited to 7. Child A created.
  Appeal succeeds. Storage restored. Balance stays at 7.
  Round 2: Contract balance=7. Re-execute, emit on_accepted msg value=3.
           On acceptance: balance debited to 4. Child B created.
  Both children exist. Contract debited 6 total. Balance = 4.

This test uses a real PostgreSQL database but mocks GenVM execution,
exercising the actual consensus state machine and balance accounting.
"""

import base64
from unittest.mock import Mock, AsyncMock, MagicMock

import pytest

from backend.consensus.base import ConsensusAlgorithm
from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.domain.types import (
    Transaction,
    TransactionType,
)
from backend.node.types import (
    Receipt,
    PendingTransaction,
    ExecutionMode,
    ExecutionResultStatus,
    Vote,
)
import backend.validators as validators


WEI_PER_GEN = 10**18


def _make_receipt_with_message(value: int, contract_state: dict = None):
    """Create a Receipt that emits an on_accepted message with the given value."""
    calldata = base64.b64encode(b"\x06").decode("ascii")  # minimal calldata
    return Receipt(
        vote=Vote.AGREE,
        execution_result=ExecutionResultStatus.SUCCESS,
        result=base64.b64encode(b"\x01").decode("ascii"),
        calldata=calldata,
        gas_used=100,
        mode=ExecutionMode.LEADER,
        contract_state=contract_state or {"accepted_slot": "data"},
        node_config={"provider": "test", "model": "test", "config": {}},
        eq_outputs=None,
        pending_transactions=[
            PendingTransaction(
                address="0xChildTarget",
                calldata=b"\x06",
                code=None,
                salt_nonce=0,
                on="accepted",
                value=value,
            )
        ],
    )


def _make_mock_node(receipt: Receipt):
    """Create a mock Node that returns the given receipt from exec_transaction."""
    node = MagicMock()
    node.exec_transaction = AsyncMock(return_value=receipt)
    return node


def _make_node_factory(receipt: Receipt):
    """Create a node_factory that returns a mock Node."""

    def factory(*args, **kwargs):
        return _make_mock_node(receipt)

    return factory


def _make_validators_snapshot():
    """Create a minimal validators snapshot with one validator."""
    validator = Mock()
    validator.address = "0xValidator1"
    validator.to_dict.return_value = {
        "address": "0xValidator1",
        "stake": 100,
        "provider": "test",
        "model": "test",
        "config": {},
        "plugin": "test",
        "plugin_config": {},
    }
    node = Mock()
    node.validator = validator
    node.genvm_host_data = {}
    snapshot = Mock(spec=validators.Snapshot)
    snapshot.nodes = [node]
    return snapshot


@pytest.fixture
def setup_accounts(accounts_manager, session):
    """Set up sender and contract accounts with initial balances."""
    sender_addr = "0xSender000000000000000000000000000000001"
    contract_addr = "0xContract0000000000000000000000000000001"
    child_target = "0xChildTarget"

    # Create accounts
    accounts_manager.create_new_account_with_address(sender_addr)
    accounts_manager.create_new_account_with_address(contract_addr)
    accounts_manager.create_new_account_with_address(child_target)

    # Fund contract with 10 GEN
    accounts_manager.update_account_balance(contract_addr, 10 * WEI_PER_GEN)
    session.commit()

    return sender_addr, contract_addr, child_target


@pytest.fixture
def insert_transaction(transactions_processor, session):
    """Insert a transaction into the DB."""

    def _insert(from_addr, to_addr, value=0, tx_type=TransactionType.RUN_CONTRACT):
        import secrets

        tx_hash = "0x" + secrets.token_hex(32)
        transactions_processor.insert_transaction(
            from_address=from_addr,
            to_address=to_addr,
            data={"calldata": base64.b64encode(b"\x06").decode("ascii")},
            value=value,
            type=tx_type.value,
            nonce=0,
            leader_only=False,
            config_rotation_rounds=3,
            num_of_initial_validators=5,
            transaction_hash=tx_hash,
        )
        session.commit()
        return tx_hash

    return _insert


class TestPayableAppealFlow:
    """End-to-end test for the cumulative appeal balance flow."""

    @pytest.mark.asyncio
    async def test_cumulative_debit_across_appeal_rounds(
        self,
        session,
        accounts_manager,
        transactions_processor,
        contract_processor,
        setup_accounts,
        insert_transaction,
    ):
        sender_addr, contract_addr, child_target = setup_accounts

        # Insert transaction (no value — this is a write call to existing contract)
        tx_hash = insert_transaction(sender_addr, contract_addr)

        # Set up contract in DB with initial state
        from backend.database_handler.models import CurrentState

        contract_row = session.query(CurrentState).filter_by(id=contract_addr).one()
        contract_row.data = {
            "state": {
                "accepted": {"slot": base64.b64encode(b"hello").decode()},
                "finalized": {},
            }
        }
        session.commit()

        # Verify initial balance
        assert accounts_manager.get_account_balance(contract_addr) == 10 * WEI_PER_GEN

        # ── Round 1: Execute with message emission ──
        receipt1 = _make_receipt_with_message(value=3 * WEI_PER_GEN)
        consensus = ConsensusAlgorithm(
            get_session=lambda: session,
            msg_handler=MagicMock(),
            consensus_service=MagicMock(),
            validators_manager=MagicMock(),
            genvm_manager=MagicMock(),
        )

        # Load transaction from DB
        tx_data = transactions_processor.get_transaction_by_hash(tx_hash)
        transaction = Transaction.from_dict(tx_data)

        def contract_snapshot_factory(addr):
            return ContractSnapshot(addr, session)

        await consensus.exec_transaction(
            transaction=transaction,
            transactions_processor=transactions_processor,
            chain_snapshot=None,
            accounts_manager=accounts_manager,
            contract_snapshot_factory=contract_snapshot_factory,
            contract_processor=contract_processor,
            node_factory=_make_node_factory(receipt1),
            validators_snapshot=_make_validators_snapshot(),
        )

        # Verify Round 1 results
        session.expire_all()
        balance_after_r1 = accounts_manager.get_account_balance(contract_addr)
        assert (
            balance_after_r1 == 7 * WEI_PER_GEN
        ), f"After Round 1: expected 7 GEN, got {balance_after_r1 / WEI_PER_GEN} GEN"

        # Verify child A was created
        children = session.execute(
            __import__("sqlalchemy").text(
                "SELECT hash, value FROM transactions WHERE triggered_by_hash = :parent"
            ),
            {"parent": tx_hash},
        ).fetchall()
        assert (
            len(children) == 1
        ), f"Expected 1 child after Round 1, got {len(children)}"
        assert children[0].value == 3 * WEI_PER_GEN

        # ── Simulate appeal ──
        transactions_processor.set_transaction_appeal(tx_hash, True)
        session.commit()

        # ── Round 2: Re-execute after appeal ──
        receipt2 = _make_receipt_with_message(value=3 * WEI_PER_GEN)

        # Reload transaction (now has appealed=True)
        tx_data2 = transactions_processor.get_transaction_by_hash(tx_hash)
        transaction2 = Transaction.from_dict(tx_data2)

        await consensus.exec_transaction(
            transaction=transaction2,
            transactions_processor=transactions_processor,
            chain_snapshot=None,
            accounts_manager=accounts_manager,
            contract_snapshot_factory=contract_snapshot_factory,
            contract_processor=contract_processor,
            node_factory=_make_node_factory(receipt2),
            validators_snapshot=_make_validators_snapshot(),
        )

        # Verify Round 2 results
        session.expire_all()
        balance_after_r2 = accounts_manager.get_account_balance(contract_addr)
        assert (
            balance_after_r2 == 4 * WEI_PER_GEN
        ), f"After Round 2: expected 4 GEN, got {balance_after_r2 / WEI_PER_GEN} GEN"

        # Verify both children exist
        all_children = session.execute(
            __import__("sqlalchemy").text(
                "SELECT hash, value FROM transactions WHERE triggered_by_hash = :parent"
            ),
            {"parent": tx_hash},
        ).fetchall()
        assert (
            len(all_children) == 2
        ), f"Expected 2 children after Round 2, got {len(all_children)}"
        assert all(c.value == 3 * WEI_PER_GEN for c in all_children)

        # Total debit: 6 GEN (3 + 3), balance: 10 - 6 = 4 GEN
        total_child_value = sum(c.value for c in all_children)
        assert total_child_value == 6 * WEI_PER_GEN
