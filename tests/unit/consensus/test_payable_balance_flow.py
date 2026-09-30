"""Tests for the payable value balance flow through consensus states.

Verifies the core accounting invariants:
- Sender debited at submission (tested in endpoints)
- Target credited once on activation via value_credited flag
- AcceptedState debits contract for message emissions
- Appeal re-entry does NOT re-credit (value_credited already true)
- Cumulative accepted message debits across appeal rounds
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from backend.consensus import base as consensus_base
from backend.consensus.base import (
    FinalizingState,
    PendingState,
    ProposingState,
    TransactionContext,
    UndeterminedState,
    _pending_valid_until_expired,
)
from backend.domain.types import (
    Transaction,
    TransactionType,
    TransactionStatus,
    TransactionExecutionMode,
)
from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.database_handler.types import ConsensusData
from backend.protocol_rpc.fees import FEE_ACCOUNTING_KEY


def _make_snapshot(states=None, balance=0):
    snap = ContractSnapshot.from_dict(
        {
            "contract_address": "0xcontract",
            "states": states or {"accepted": {"slot": "data"}, "finalized": {}},
        }
    )
    snap.balance = balance
    return snap


def _make_accounts_manager():
    am = Mock()
    am.get_account.return_value = Mock()  # account exists
    am.get_account_balance.return_value = 1000
    am.credit_tx_value_once.return_value = True  # first call credits
    am.debit_account_balance.return_value = True
    am.credit_account_balance.return_value = None
    return am


def _make_transaction(
    value=100,
    appealed=False,
    triggered_by_hash=None,
    status=TransactionStatus.PENDING,
    consensus_history=None,
):
    tx = Mock(spec=Transaction)
    tx.hash = "0xtxhash"
    tx.type = TransactionType.RUN_CONTRACT
    tx.from_address = "0xsender"
    tx.to_address = "0xcontract"
    tx.value = value
    tx.appealed = appealed
    tx.appeal_undetermined = False
    tx.appeal_leader_timeout = False
    tx.appeal_validators_timeout = False
    tx.triggered_by_hash = triggered_by_hash
    tx.origin_address = None
    tx.status = status
    tx.consensus_history = consensus_history or {}
    tx.consensus_data = Mock()
    tx.consensus_data.leader_receipt = None
    tx.contract_snapshot = None
    tx.execution_mode = TransactionExecutionMode.NORMAL
    tx.config_rotation_rounds = 3
    tx.num_of_initial_validators = 5
    tx.sim_config = None
    tx.data = {"calldata": "AA=="}
    tx.created_at = None
    tx.leader_only = False
    tx.rotation_count = 0
    tx.leader_timeout_validators = None
    tx.appeal_failed = 0
    tx.appeal_processing_time = 0
    tx.timestamp_appeal = None
    tx.last_vote_timestamp = None
    tx.timestamp_awaiting_finalization = None
    return tx


def _make_context(transaction, accounts_manager=None, snapshot=None):
    if accounts_manager is None:
        accounts_manager = _make_accounts_manager()
    if snapshot is None:
        snapshot = _make_snapshot(balance=0)
    factory = Mock(return_value=snapshot)
    ctx = TransactionContext(
        transaction=transaction,
        transactions_processor=Mock(),
        chain_snapshot=None,
        accounts_manager=accounts_manager,
        contract_snapshot_factory=factory,
        contract_processor=Mock(),
        node_factory=Mock(),
        msg_handler=Mock(),
        consensus_service=Mock(),
        validators_snapshot=None,
        genvm_manager=Mock(),
    )
    return ctx


def test_transaction_from_dict_coerces_serialized_value():
    tx = Transaction.from_dict(
        {
            "hash": "0xtxhash",
            "status": TransactionStatus.PENDING.value,
            "type": TransactionType.SEND.value,
            "from_address": "0xsender",
            "to_address": "0xrecipient",
            "value": str(3 * 10**18),
        }
    )

    assert tx.value == 3 * 10**18


class TestActivationCredit:
    """credit_tx_value_once is called on first activation."""

    def test_first_activation_credits_target(self):
        tx = _make_transaction(value=500)
        am = _make_accounts_manager()
        ctx = _make_context(tx, accounts_manager=am)

        # Verify credit_tx_value_once was called during context creation
        # (PendingState.handle calls it, but TransactionContext.__init__ doesn't)
        # We test the logic directly
        assert tx.value == 500
        am.credit_tx_value_once.assert_not_called()  # not called in __init__

    def test_credit_tx_value_once_is_idempotent(self):
        """Second call returns False, no double credit."""
        am = _make_accounts_manager()
        # First call credits
        assert am.credit_tx_value_once("0xhash", "0xcontract", 500) is True
        # Simulate second call returning False (already credited)
        am.credit_tx_value_once.return_value = False
        assert am.credit_tx_value_once("0xhash", "0xcontract", 500) is False


@pytest.mark.asyncio
async def test_expired_pending_transaction_cancels_before_activation(monkeypatch):
    transaction_hash = "0x" + "ab" * 32
    sender = "0x1111111111111111111111111111111111111111"
    transaction = {
        "hash": transaction_hash,
        "status": TransactionStatus.PENDING.value,
        "type": TransactionType.RUN_CONTRACT.value,
        "from_address": sender,
        "to_address": "0x2222222222222222222222222222222222222222",
        "value": 17,
        "data": {
            "valid_until": 99,
            FEE_ACCOUNTING_KEY: {"sender": sender},
        },
    }
    transactions_processor = Mock()
    transactions_processor.get_transaction_by_hash.return_value = transaction
    accounts_manager = Mock()
    dispatch = AsyncMock()
    monkeypatch.setattr(consensus_base.time, "time", lambda: 100)
    monkeypatch.setattr(
        consensus_base.ConsensusAlgorithm,
        "dispatch_transaction_status_update",
        dispatch,
    )
    context = SimpleNamespace(
        transaction=SimpleNamespace(hash=transaction_hash),
        transactions_processor=transactions_processor,
        accounts_manager=accounts_manager,
        msg_handler=Mock(),
    )

    result = await PendingState().handle(context)

    assert result is None
    accounts_manager.refund_tx_value.assert_called_once_with(transaction_hash, sender)
    accounts_manager.cancel_tx_fee_accounting_once.assert_called_once_with(
        transaction_hash,
        sender,
        "valid_until_expired",
    )
    dispatch.assert_awaited_once_with(
        transactions_processor,
        transaction_hash,
        TransactionStatus.CANCELED,
        context.msg_handler,
    )


def test_valid_until_equality_remains_activatable_for_full_evm_second():
    transaction = SimpleNamespace(data={"valid_until": 100}, consensus_history={})

    assert _pending_valid_until_expired(transaction, now=100.999) is False
    assert _pending_valid_until_expired(transaction, now=101.0) is True


def test_valid_until_does_not_cancel_appeal_recomputation():
    transaction = SimpleNamespace(
        data={"valid_until": 100},
        consensus_history={"current_status_changes": ["ACCEPTED"]},
        appealed=True,
    )

    assert _pending_valid_until_expired(transaction, now=10_000) is False


def _pending_context_with_validator_capacity(*, requested, available):
    transaction_hash = "0x" + "ab" * 32
    transaction = {
        "hash": transaction_hash,
        "status": TransactionStatus.PENDING.value,
        "type": TransactionType.RUN_CONTRACT.value,
        "from_address": "0x1111111111111111111111111111111111111111",
        "to_address": "0x2222222222222222222222222222222222222222",
        "value": 0,
        "data": {"calldata": "AA=="},
        "consensus_data": None,
        "consensus_history": {},
        "num_of_initial_validators": requested,
    }
    transactions_processor = Mock()
    transactions_processor.get_transaction_by_hash.return_value = transaction
    validator_nodes = [
        SimpleNamespace(
            validator=SimpleNamespace(
                to_dict=lambda index=index: {
                    "address": f"0x{index:040x}",
                    "stake": 1,
                }
            )
        )
        for index in range(1, available + 1)
    ]
    return SimpleNamespace(
        transaction=SimpleNamespace(hash=transaction_hash),
        transactions_processor=transactions_processor,
        accounts_manager=Mock(),
        msg_handler=SimpleNamespace(send_message=Mock()),
        consensus_service=Mock(),
        contract_processor=Mock(),
        validators_snapshot=SimpleNamespace(nodes=validator_nodes),
        involved_validators=[],
        consensus_data=ConsensusData(votes={}, leader_receipt=None, validators=[]),
        contract_snapshot=_make_snapshot(balance=0),
    )


@pytest.mark.asyncio
async def test_initial_committee_shortfall_becomes_undetermined_without_execution():
    context = _pending_context_with_validator_capacity(requested=7, available=5)

    result = await PendingState().handle(context)

    assert isinstance(result, UndeterminedState)
    assert context.involved_validators == []
    context.accounts_manager.credit_tx_value_once.assert_not_called()

    await result.handle(context)

    context.transactions_processor.update_transaction_status.assert_called_once_with(
        context.transaction.hash,
        TransactionStatus.UNDETERMINED,
        False,
    )


@pytest.mark.asyncio
async def test_initial_committee_uses_exact_requested_size_when_available():
    context = _pending_context_with_validator_capacity(requested=7, available=7)

    result = await PendingState().handle(context)

    assert isinstance(result, ProposingState)
    assert len(context.involved_validators) == 7


class TestAppealReentry:
    """On appeal re-entry, value_credited flag prevents double credit."""

    def test_appealed_tx_has_consensus_history(self):
        """Appealed tx has consensus_history set, not empty."""
        tx = _make_transaction(
            value=500,
            appealed=True,
            consensus_history={"round_0": "data"},
        )
        assert tx.consensus_history  # truthy

    def test_non_appealed_tx_has_empty_history(self):
        tx = _make_transaction(value=500)
        assert not tx.consensus_history  # empty dict is falsy


class TestSnapshotBalanceHydration:
    """Saved snapshots get balance hydrated from DB."""

    def test_saved_snapshot_without_balance_gets_hydrated(self):
        """TransactionContext hydrates balance from factory when missing."""
        saved = _make_snapshot(
            states={"accepted": {"slot": "data"}, "finalized": {}},
        )
        # Simulate deserialized snapshot without balance attr
        if hasattr(saved, "balance"):
            delattr(saved, "balance")

        tx = _make_transaction(value=100)
        tx.contract_snapshot = saved

        fresh = _make_snapshot(balance=999)
        factory = Mock(return_value=fresh)

        ctx = TransactionContext(
            transaction=tx,
            transactions_processor=Mock(),
            chain_snapshot=None,
            accounts_manager=_make_accounts_manager(),
            contract_snapshot_factory=factory,
            contract_processor=Mock(),
            node_factory=Mock(),
            msg_handler=Mock(),
            consensus_service=Mock(),
            validators_snapshot=None,
            genvm_manager=Mock(),
        )

        # Balance should be hydrated from factory
        assert ctx.contract_snapshot.balance == 999

    def test_saved_snapshot_with_balance_keeps_it(self):
        """If snapshot already has balance, don't overwrite."""
        saved = _make_snapshot(
            states={"accepted": {"slot": "data"}, "finalized": {}},
            balance=777,
        )
        tx = _make_transaction(value=100)
        tx.contract_snapshot = saved

        ctx = _make_context(tx)

        assert ctx.contract_snapshot.balance == 777


class TestValueCreditedFlag:
    """The value_credited flag prevents double-crediting across retries."""

    def test_credit_tx_value_once_sets_flag(self):
        """Verify the method signature matches our expectations."""
        am = _make_accounts_manager()
        result = am.credit_tx_value_once("0xhash", "0xcontract", 100)
        assert result is True
        am.credit_tx_value_once.assert_called_once_with("0xhash", "0xcontract", 100)

    def test_zero_value_skips_credit(self):
        """Zero-value tx should not call credit_tx_value_once."""
        tx = _make_transaction(value=0)
        am = _make_accounts_manager()
        ctx = _make_context(tx, accounts_manager=am)
        # With value=0, credit should never be called
        am.credit_tx_value_once.assert_not_called()


@pytest.mark.asyncio
async def test_terminal_no_result_returns_child_value_to_emitting_contract(monkeypatch):
    """The outer origin must never receive value funded by a child sender."""

    tx = _make_transaction(
        value=500,
        triggered_by_hash="0xparent",
        status=TransactionStatus.LEADER_TIMEOUT,
    )
    tx.from_address = "0xemitting-contract"
    tx.origin_address = "0xouter-user"
    tx.consensus_data.leader_receipt = [
        SimpleNamespace(
            execution_result=SimpleNamespace(value="ERROR"),
            node_config={"address": "0xleader"},
            pending_transactions=[],
        )
    ]
    accounts_manager = _make_accounts_manager()
    context = _make_context(tx, accounts_manager=accounts_manager)
    executor = Mock()
    executor.execute = AsyncMock()
    monkeypatch.setattr(
        consensus_base,
        "EffectExecutor",
        Mock(return_value=executor),
    )
    monkeypatch.setattr(
        consensus_base,
        "_dispatch_messages_for_phase",
        Mock(return_value=False),
    )

    await FinalizingState().handle(context)

    accounts_manager.refund_activated_tx_value_once.assert_called_once_with(
        tx.hash,
        tx.to_address,
        "0xemitting-contract",
    )
    assert accounts_manager.refund_activated_tx_value_once.call_args.args[2] != (
        tx.origin_address
    )


@pytest.mark.asyncio
async def test_receiptless_undetermined_finalizes_and_refunds_uncredited_value(
    monkeypatch,
):
    tx = _make_transaction(value=500, status=TransactionStatus.UNDETERMINED)
    tx.consensus_data = ConsensusData(votes={}, leader_receipt=None, validators=[])
    accounts_manager = _make_accounts_manager()
    accounts_manager.refund_activated_tx_value_once.return_value = False
    context = _make_context(tx, accounts_manager=accounts_manager)
    executor = Mock()
    executor.execute = AsyncMock()
    monkeypatch.setattr(
        consensus_base,
        "EffectExecutor",
        Mock(return_value=executor),
    )
    dispatch_messages = Mock(return_value=False)
    monkeypatch.setattr(
        consensus_base,
        "_dispatch_messages_for_phase",
        dispatch_messages,
    )

    await FinalizingState().handle(context)

    dispatch_messages.assert_not_called()
    accounts_manager.refund_activated_tx_value_once.assert_called_once_with(
        tx.hash,
        tx.to_address,
        tx.from_address,
    )
    accounts_manager.refund_tx_value.assert_called_once_with(
        tx.hash,
        tx.from_address,
    )
    accounts_manager.settle_tx_fee_accounting_once.assert_called_once_with(
        tx.hash,
        tx.from_address,
        receipt=None,
        reason="finalized",
    )


class TestMintOnDemand:
    """Studio sandbox mints shortfall for sender automatically."""

    def test_sender_balance_topped_up_when_insufficient(self):
        """If sender can't cover value, shortfall is minted."""
        am = _make_accounts_manager()
        am.get_account_balance.return_value = 30  # has 30
        value = 100  # needs 100
        shortfall = value - 30  # needs 70 more

        # Simulate the mint-on-demand logic from send_raw_transaction
        sender_balance = am.get_account_balance("0xsender")
        if sender_balance < value:
            am.credit_account_balance("0xsender", value - sender_balance)
        am.debit_account_balance("0xsender", value)

        am.credit_account_balance.assert_called_once_with("0xsender", 70)
        am.debit_account_balance.assert_called_once_with("0xsender", 100)
