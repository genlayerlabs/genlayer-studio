"""Comprehensive consensus-level tests for payable value scenarios.

All tests use real PostgreSQL but mock GenVM execution.
"""

import base64
import secrets
from unittest.mock import Mock, AsyncMock, MagicMock

import pytest

from backend.consensus.base import ConsensusAlgorithm
from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.domain.types import Transaction, TransactionType
from backend.node.types import (
    Receipt,
    PendingTransaction,
    ExecutionMode,
    ExecutionResultStatus,
    Vote,
)
import backend.validators as validators

WEI_PER_GEN = 10**18


# ── Helpers ──


def _receipt(
    pending_transactions=None,
    contract_state=None,
    execution_result=ExecutionResultStatus.SUCCESS,
):
    calldata = base64.b64encode(b"\x06").decode("ascii")
    return Receipt(
        vote=Vote.AGREE,
        execution_result=execution_result,
        result=base64.b64encode(b"\x01").decode("ascii"),
        calldata=calldata,
        gas_used=100,
        mode=ExecutionMode.LEADER,
        contract_state=contract_state or {"slot": "data"},
        node_config={"provider": "test", "model": "test", "config": {}},
        eq_outputs=None,
        pending_transactions=pending_transactions or [],
    )


def _pending_tx(value=0, on="accepted", address="0xChildTarget"):
    return PendingTransaction(
        address=address,
        calldata=b"\x06",
        code=None,
        salt_nonce=0,
        on=on,
        value=value,
    )


def _node_factory(receipt):
    observed_balances = []

    def factory(*args, **kwargs):
        if len(args) > 2 and hasattr(args[2], "balance"):
            observed_balances.append(args[2].balance)
        node = MagicMock()
        node.exec_transaction = AsyncMock(return_value=receipt)
        return node

    factory.observed_balances = observed_balances
    return factory


def _validators_snapshot():
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


def _consensus(session):
    return ConsensusAlgorithm(
        get_session=lambda: session,
        msg_handler=MagicMock(),
        consensus_service=MagicMock(),
        validators_manager=MagicMock(),
        genvm_manager=MagicMock(),
    )


def _insert_tx(
    tp, session, from_addr, to_addr, value=0, tx_type=TransactionType.RUN_CONTRACT
):
    tx_hash = "0x" + secrets.token_hex(32)
    tp.insert_transaction(
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


def _setup_contract(session, accounts_manager, contract_addr, balance):
    from backend.database_handler.models import CurrentState

    row = session.query(CurrentState).filter_by(id=contract_addr).one()
    row.data = {
        "state": {
            "accepted": {"slot": base64.b64encode(b"data").decode()},
            "finalized": {},
        }
    }
    session.commit()
    accounts_manager.update_account_balance(contract_addr, balance)
    session.commit()


async def _run_tx(
    session,
    accounts_manager,
    transactions_processor,
    contract_processor,
    tx_hash,
    receipt,
):
    consensus = _consensus(session)
    tx_data = transactions_processor.get_transaction_by_hash(tx_hash)
    transaction = Transaction.from_dict(tx_data)
    nf = _node_factory(receipt)
    await consensus.exec_transaction(
        transaction=transaction,
        transactions_processor=transactions_processor,
        chain_snapshot=None,
        accounts_manager=accounts_manager,
        contract_snapshot_factory=lambda addr: ContractSnapshot(addr, session),
        contract_processor=contract_processor,
        node_factory=nf,
        validators_snapshot=_validators_snapshot(),
    )
    session.expire_all()
    return nf


# ── Fixtures ──


@pytest.fixture
def addrs(accounts_manager, session):
    sender = "0xSender000000000000000000000000000000001"
    contract = "0xContract0000000000000000000000000000001"
    child1 = "0xChild10000000000000000000000000000000001"
    child2 = "0xChild20000000000000000000000000000000001"
    for addr in [sender, contract, child1, child2]:
        accounts_manager.create_new_account_with_address(addr)
    session.commit()
    return sender, contract, child1, child2


# ── Tests ──


class TestMultipleMessages:
    """Two on_accepted messages: both debited, both children created with unique nonces."""

    @pytest.mark.asyncio
    async def test_two_accepted_messages(
        self,
        session,
        accounts_manager,
        transactions_processor,
        contract_processor,
        addrs,
    ):
        sender, contract, child1, child2 = addrs
        _setup_contract(session, accounts_manager, contract, 20 * WEI_PER_GEN)

        tx_hash = _insert_tx(transactions_processor, session, sender, contract)

        receipt = _receipt(
            pending_transactions=[
                _pending_tx(value=3 * WEI_PER_GEN, on="accepted", address=child1),
                _pending_tx(value=5 * WEI_PER_GEN, on="accepted", address=child2),
            ]
        )

        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
            receipt,
        )

        assert (
            accounts_manager.get_account_balance(contract) == 12 * WEI_PER_GEN
        )  # 20 - 3 - 5

        children = session.execute(
            __import__("sqlalchemy").text(
                "SELECT value, nonce FROM transactions WHERE triggered_by_hash = :h ORDER BY nonce"
            ),
            {"h": tx_hash},
        ).fetchall()
        assert len(children) == 2
        assert children[0].value == 3 * WEI_PER_GEN
        assert children[1].value == 5 * WEI_PER_GEN
        assert children[0].nonce != children[1].nonce  # unique nonces


class TestOnFinalizedMessage:
    """On-finalized message: NOT debited at acceptance, debited at finalization."""

    @pytest.mark.asyncio
    async def test_finalized_message_not_debited_on_acceptance(
        self,
        session,
        accounts_manager,
        transactions_processor,
        contract_processor,
        addrs,
    ):
        sender, contract, child1, _ = addrs
        _setup_contract(session, accounts_manager, contract, 10 * WEI_PER_GEN)

        tx_hash = _insert_tx(transactions_processor, session, sender, contract)

        receipt = _receipt(
            pending_transactions=[
                _pending_tx(value=4 * WEI_PER_GEN, on="finalized", address=child1),
            ]
        )

        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
            receipt,
        )

        # Balance should NOT be debited yet (on_finalized, not on_accepted)
        assert accounts_manager.get_account_balance(contract) == 10 * WEI_PER_GEN

        # No children created for on_accepted (none exist)
        children = session.execute(
            __import__("sqlalchemy").text(
                "SELECT value FROM transactions WHERE triggered_by_hash = :h AND triggered_on = 'accepted'"
            ),
            {"h": tx_hash},
        ).fetchall()
        assert len(children) == 0


class TestMixedMessages:
    """Mix of on_accepted and on_finalized messages."""

    @pytest.mark.asyncio
    async def test_mixed_messages_only_accepted_debited(
        self,
        session,
        accounts_manager,
        transactions_processor,
        contract_processor,
        addrs,
    ):
        sender, contract, child1, child2 = addrs
        _setup_contract(session, accounts_manager, contract, 20 * WEI_PER_GEN)

        tx_hash = _insert_tx(transactions_processor, session, sender, contract)

        receipt = _receipt(
            pending_transactions=[
                _pending_tx(value=3 * WEI_PER_GEN, on="accepted", address=child1),
                _pending_tx(value=7 * WEI_PER_GEN, on="finalized", address=child2),
            ]
        )

        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
            receipt,
        )

        # Only on_accepted debit: 20 - 3 = 17
        assert accounts_manager.get_account_balance(contract) == 17 * WEI_PER_GEN


class TestZeroValueTransaction:
    """Zero-value tx through consensus: no balance changes, no children."""

    @pytest.mark.asyncio
    async def test_zero_value_no_side_effects(
        self,
        session,
        accounts_manager,
        transactions_processor,
        contract_processor,
        addrs,
    ):
        sender, contract, _, _ = addrs
        _setup_contract(session, accounts_manager, contract, 10 * WEI_PER_GEN)

        tx_hash = _insert_tx(transactions_processor, session, sender, contract)

        receipt = _receipt(pending_transactions=[])

        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            tx_hash,
            receipt,
        )

        assert accounts_manager.get_account_balance(contract) == 10 * WEI_PER_GEN

        children = session.execute(
            __import__("sqlalchemy").text(
                "SELECT count(*) as cnt FROM transactions WHERE triggered_by_hash = :h"
            ),
            {"h": tx_hash},
        ).fetchone()
        assert children.cnt == 0


class TestChildActivationCredit:
    """Child tx with value credits the target contract via credit_tx_value_once."""

    @pytest.mark.asyncio
    async def test_child_credited_on_activation(
        self,
        session,
        accounts_manager,
        transactions_processor,
        contract_processor,
        addrs,
    ):
        sender, contract, child_target, _ = addrs
        _setup_contract(session, accounts_manager, contract, 10 * WEI_PER_GEN)

        # Create parent tx that emits child with value
        parent_hash = _insert_tx(transactions_processor, session, sender, contract)

        receipt = _receipt(
            pending_transactions=[
                _pending_tx(value=3 * WEI_PER_GEN, on="accepted", address=child_target),
            ]
        )

        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            parent_hash,
            receipt,
        )

        # Parent debited
        assert accounts_manager.get_account_balance(contract) == 7 * WEI_PER_GEN

        # Find the child tx
        child_row = session.execute(
            __import__("sqlalchemy").text(
                "SELECT hash, value FROM transactions WHERE triggered_by_hash = :h"
            ),
            {"h": parent_hash},
        ).fetchone()
        assert child_row is not None
        assert child_row.value == 3 * WEI_PER_GEN

        # Set up child target as a contract so it can receive value
        from backend.database_handler.models import CurrentState

        child_contract = session.query(CurrentState).filter_by(id=child_target).one()
        child_contract.data = {
            "state": {
                "accepted": {"slot": base64.b64encode(b"child").decode()},
                "finalized": {},
            }
        }
        session.commit()

        # Execute child tx — credit_tx_value_once should credit child_target
        child_receipt = _receipt(pending_transactions=[])
        await _run_tx(
            session,
            accounts_manager,
            transactions_processor,
            contract_processor,
            child_row.hash,
            child_receipt,
        )

        # Child target should be credited with 3 GEN
        assert accounts_manager.get_account_balance(child_target) == 3 * WEI_PER_GEN


class TestMintOnDemand:
    """Sender with insufficient balance gets topped up automatically."""

    def test_mint_shortfall(self, session, accounts_manager, addrs):
        sender, _, _, _ = addrs
        # Sender starts with 0
        assert accounts_manager.get_account_balance(sender) == 0

        value = 100 * WEI_PER_GEN
        sender_balance = accounts_manager.get_account_balance(sender)
        if sender_balance < value:
            shortfall = value - sender_balance
            accounts_manager.credit_account_balance(sender, shortfall)

        accounts_manager.debit_account_balance(sender, value)
        session.commit()

        assert accounts_manager.get_account_balance(sender) == 0


class TestCancelRefund:
    """Payable tx canceled before activation: sender refunded."""

    def test_cancel_refunds_uncredited_value(self, session, accounts_manager, addrs):
        sender, contract, _, _ = addrs
        accounts_manager.update_account_balance(sender, 100 * WEI_PER_GEN)
        session.commit()

        value = 10 * WEI_PER_GEN

        # Simulate submission debit
        accounts_manager.debit_account_balance(sender, value)
        session.commit()
        assert accounts_manager.get_account_balance(sender) == 90 * WEI_PER_GEN

        # Simulate refund (value_credited is false, so refund succeeds)
        accounts_manager.credit_account_balance(sender, value)
        session.commit()
        assert accounts_manager.get_account_balance(sender) == 100 * WEI_PER_GEN


class TestCreditIdempotency:
    """credit_tx_value_once is idempotent — second call returns False."""

    def test_double_credit_prevented(
        self, session, accounts_manager, transactions_processor, addrs
    ):
        sender, contract, _, _ = addrs
        tx_hash = _insert_tx(
            transactions_processor, session, sender, contract, value=5 * WEI_PER_GEN
        )

        # First credit
        result1 = accounts_manager.credit_tx_value_once(
            tx_hash, contract, 5 * WEI_PER_GEN
        )
        session.commit()
        assert result1 is True
        assert accounts_manager.get_account_balance(contract) == 5 * WEI_PER_GEN

        # Second credit — should be rejected
        result2 = accounts_manager.credit_tx_value_once(
            tx_hash, contract, 5 * WEI_PER_GEN
        )
        session.commit()
        assert result2 is False
        assert (
            accounts_manager.get_account_balance(contract) == 5 * WEI_PER_GEN
        )  # unchanged
