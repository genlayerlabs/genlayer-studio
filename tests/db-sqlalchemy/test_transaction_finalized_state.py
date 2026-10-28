"""Persisted acceptance results, not the moving contract head, become final."""

import base64
import json
import time
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from backend.consensus.base import (
    AcceptedState,
    TransactionContext,
    contract_snapshot_factory,
    node_factory,
    upgrade_code_slot,
)
import backend.consensus.base as consensus_base
from backend.consensus.history import APPEAL_RECOVERY_SNAPSHOT_KEY
from backend.consensus.types import ConsensusRound
from backend.consensus.worker import ConsensusWorker
from backend.database_handler.accounts_manager import AccountsManager
from backend.database_handler.contract_processor import ContractProcessor
from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.database_handler.models import CurrentState, Transactions
from backend.database_handler.transactions_processor import TransactionsProcessor
from backend.database_handler.types import ConsensusData
from backend.domain.types import Transaction, TransactionType
from backend.node.base import _SnapshotView
from backend.node.genvm import get_code_slot
from backend.node.types import Address, ExecutionMode, ExecutionResultStatus, Receipt
from backend.protocol_rpc.endpoints import admin_upgrade_contract_code
from backend.protocol_rpc.fees import (
    FEE_ACCOUNTING_KEY,
    calculate_appeal_charge,
    create_fee_accounting,
    record_appeal_bond,
    required_fee_deposit,
)


SENDER = "0x" + "11" * 20
CONTRACT = "0x" + "22" * 20
SLOT = b"\x01" * 32
KEY = base64.b64encode(SLOT).decode()


def state(value):
    return {KEY: base64.b64encode(bytes([value])).decode()}


@pytest.fixture
def lifecycle(engine, monkeypatch):
    monkeypatch.setenv("VITE_FINALITY_WINDOW", "3600")
    monkeypatch.setenv("VITE_FINALITY_WINDOW_APPEAL_FAILED_REDUCTION", "0.2")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def get_session():
        with sessions() as session:
            yield session

    rollup = Mock()
    rollup.emit_transaction_event.return_value = {"txIds": [], "messages": []}
    messages = Mock()
    messages.send_message_async = AsyncMock()
    worker = ConsensusWorker(get_session, messages, rollup, None, None)
    worker.usage_metrics_service = SimpleNamespace(
        send_finalized_transaction_metrics=AsyncMock()
    )
    with sessions() as session:
        accounts = AccountsManager(session)
        accounts.create_new_account_with_address(SENDER)
        accounts.create_new_account_with_address(CONTRACT)
        session.query(CurrentState).filter_by(id=CONTRACT).one().data = {
            "state": {"accepted": state(0), "finalized": state(0)}
        }
        session.commit()
    return SimpleNamespace(sessions=sessions, worker=worker, rollup=rollup)


def context_for(lifecycle, session, tx):
    return TransactionContext(
        transaction=tx,
        transactions_processor=TransactionsProcessor(session),
        chain_snapshot=None,
        accounts_manager=AccountsManager(session),
        contract_snapshot_factory=lambda addr: contract_snapshot_factory(
            addr, session, tx
        ),
        contract_processor=ContractProcessor(session),
        node_factory=node_factory,
        msg_handler=lifecycle.worker.msg_handler,
        consensus_service=lifecycle.rollup,
        validators_snapshot=None,
        genvm_manager=None,
    )


async def accept(
    lifecycle,
    nonce,
    post_state,
    *,
    execution_result=ExecutionResultStatus.SUCCESS,
    recompute=False,
    tx_type=TransactionType.RUN_CONTRACT,
):
    tx_hash = "0x" + f"{nonce + 1:064x}"
    with lifecycle.sessions() as session:
        tp = TransactionsProcessor(session)
        if not recompute:
            tp.insert_transaction(
                from_address=SENDER,
                to_address=CONTRACT,
                data=(
                    {
                        "contract_address": CONTRACT,
                        "contract_code": base64.b64encode(
                            b"class Contract: pass"
                        ).decode(),
                        "calldata": "Bg==",
                    }
                    if tx_type == TransactionType.DEPLOY_CONTRACT
                    else {"calldata": "Bg=="}
                ),
                value=0,
                type=tx_type.value,
                nonce=nonce,
                leader_only=False,
                config_rotation_rounds=3,
                transaction_hash=tx_hash,
            )
        session.commit()
        claimed = await lifecycle.worker.claim_next_transaction(session)
        assert claimed is not None and claimed["hash"] == tx_hash
        tx = Transaction.from_dict(tp.get_transaction_by_hash(tx_hash))
        context = context_for(lifecycle, session, tx)
        # Only VM execution is replaced: scheduling, acceptance, persistence,
        # appeal timing, finalization and storage reads use the real code.
        receipt = Receipt(
            result=b"\x00\x00",
            calldata=b"\x06",
            gas_used=0,
            mode=ExecutionMode.LEADER,
            contract_state=post_state,
            node_config={"address": SENDER, "stake": 100},
            execution_result=execution_result,
            pending_transactions=[],
            genvm_result={"stdout": "", "stderr": ""},
        )
        context.consensus_data = ConsensusData(
            votes={}, leader_receipt=[receipt], validators=[]
        )
        await AcceptedState().handle(context)
        session.commit()
        lifecycle.worker.release_transaction(session, tx_hash)
        assert tp.get_transaction_by_hash(tx_hash)["status"] == "ACCEPTED"
        stored = session.execute(
            text("SELECT consensus_data FROM transactions WHERE hash=:hash"),
            {"hash": tx_hash},
        ).scalar_one()
        assert stored["leader_receipt"][0]["contract_state"] == {}
    return tx_hash


async def finalize(lifecycle, tx_hash, *, expected_status="FINALIZED"):
    with lifecycle.sessions() as session:
        tp = TransactionsProcessor(session)
        row = tp.get_transaction_by_hash(tx_hash)
        history = row["consensus_history"]
        history["latestDecision"]["appealDeadline"] = int(time.time()) - 1
        session.execute(
            text(
                "UPDATE transactions SET timestamp_awaiting_finalization=:ts, "
                "consensus_history=CAST(:history AS jsonb) WHERE hash=:hash"
            ),
            {
                "ts": int(time.time()) - 3601,
                "history": json.dumps(history),
                "hash": tx_hash,
            },
        )
        session.commit()
        claimed = await lifecycle.worker.claim_next_finalization(session)
        assert claimed is not None and claimed["hash"] == tx_hash
        await lifecycle.worker.process_finalization(claimed, session)
        session.expire_all()
        assert tp.get_transaction_by_hash(tx_hash)["status"] == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize("overlap", [False, True])
async def test_finalizes_only_its_own_accepted_result(lifecycle, overlap):
    first = await accept(lifecycle, 0, state(1))
    second = await accept(lifecycle, 1, state(2)) if overlap else None
    with lifecycle.sessions() as session:
        assert await lifecycle.worker.claim_next_finalization(session) is None
    await finalize(lifecycle, first)
    with lifecycle.sessions() as session:
        snapshot = ContractSnapshot(CONTRACT, session)
        view = _SnapshotView(
            snapshot, lambda addr: ContractSnapshot(addr, session), True, "finalized"
        )
        assert view.storage_read(Address(CONTRACT), SLOT, 0, 1) == b"\x01"
        assert snapshot.states["accepted"] == state(2 if overlap else 1)
        if second:
            tp = TransactionsProcessor(session)
            row = tp.get_transaction_by_hash(second)
            assert row["status"] == "ACCEPTED"
            assert not lifecycle.worker.consensus_algorithm.can_finalize_transaction(
                tp, Transaction.from_dict(row), 0, [row]
            )
    if second:
        await finalize(lifecycle, second)
        with lifecycle.sessions() as session:
            assert ContractSnapshot(CONTRACT, session).states["finalized"] == state(2)


@pytest.mark.asyncio
async def test_deployment_finalizes_constructor_state_not_later_write(lifecycle):
    with lifecycle.sessions() as session:
        session.query(CurrentState).filter_by(id=CONTRACT).one().data = {}
        session.commit()
    code_slot = base64.b64encode(get_code_slot()).decode()
    deployed = {**state(1), code_slot: base64.b64encode(b"contract code").decode()}
    updated = {**deployed, **state(2)}
    deployment = await accept(
        lifecycle, 0, deployed, tx_type=TransactionType.DEPLOY_CONTRACT
    )
    await accept(lifecycle, 1, updated)
    await finalize(lifecycle, deployment)
    with lifecycle.sessions() as session:
        assert ContractSnapshot(CONTRACT, session).states == {
            "accepted": updated,
            "finalized": deployed,
        }


@pytest.mark.asyncio
async def test_failed_appeal_preserves_original_post_state(lifecycle):
    first = await accept(lifecycle, 0, state(1))
    await accept(lifecycle, 1, state(2))
    with lifecycle.sessions() as session:
        tp = TransactionsProcessor(session)
        tp.set_transaction_appeal(first, True)
        tx = Transaction.from_dict(tp.get_transaction_by_hash(first))
        assert not tx.consensus_data.leader_receipt[0].contract_state
        context = context_for(lifecycle, session, tx)
        context.consensus_data = tx.consensus_data
        await AcceptedState().handle(context)
        row = tp.get_transaction_by_hash(first)
        assert row["appeal_failed"] == 1
        assert row["contract_snapshot"]["states"]["accepted"] == state(0)
        assert row["contract_snapshot"]["finalization_state"] == state(1)
        assert ContractSnapshot(CONTRACT, session).states["accepted"] == state(2)
    await finalize(lifecycle, first)
    with lifecycle.sessions() as session:
        assert ContractSnapshot(CONTRACT, session).states["finalized"] == state(1)


@pytest.mark.asyncio
async def test_successful_appeal_discards_post_states_before_reexecution(
    lifecycle, monkeypatch
):
    first = await accept(lifecycle, 0, state(1))
    second = await accept(lifecycle, 1, state(2))
    # Supply the jury verdict, but run real appeal restoration and descendant reset.
    monkeypatch.setattr(
        consensus_base.ConsensusAlgorithm,
        "get_extra_validators",
        staticmethod(lambda *args, **kwargs: (None, [{"address": SENDER}])),
    )
    monkeypatch.setattr(
        consensus_base,
        "CommittingState",
        lambda: SimpleNamespace(
            handle=AsyncMock(return_value=ConsensusRound.VALIDATOR_APPEAL_SUCCESSFUL)
        ),
    )
    with lifecycle.sessions() as session:
        tp = TransactionsProcessor(session)
        tp.set_transaction_appeal(first, True)
        tx = Transaction.from_dict(tp.get_transaction_by_hash(first))
        context = context_for(lifecycle, session, tx)
        await lifecycle.worker.consensus_algorithm.process_validator_appeal(
            transaction=tx,
            transactions_processor=tp,
            chain_snapshot=None,
            accounts_manager=context.accounts_manager,
            contract_snapshot_factory=context.contract_snapshot_factory,
            contract_processor=context.contract_processor,
            node_factory=node_factory,
            validators_snapshot=SimpleNamespace(nodes=[]),
        )
        for tx_hash in (first, second):
            row = tp.get_transaction_by_hash(tx_hash)
            assert row["status"] == "PENDING"
            assert row["contract_snapshot"] is None
        assert ContractSnapshot(CONTRACT, session).states == {
            "accepted": state(0),
            "finalized": state(0),
        }
    await accept(lifecycle, 0, state(3), recompute=True)
    await accept(lifecycle, 1, state(4), recompute=True)
    await finalize(lifecycle, first)
    with lifecycle.sessions() as session:
        assert ContractSnapshot(CONTRACT, session).states == {
            "accepted": state(4),
            "finalized": state(3),
        }
    await finalize(lifecycle, second)
    with lifecycle.sessions() as session:
        assert ContractSnapshot(CONTRACT, session).states["finalized"] == state(4)


@pytest.mark.asyncio
async def test_empty_post_state_is_distinct_from_missing_state(lifecycle):
    tx_hash = await accept(lifecycle, 0, {})
    await finalize(lifecycle, tx_hash)
    with lifecycle.sessions() as session:
        assert ContractSnapshot(CONTRACT, session).states["finalized"] == {}


@pytest.mark.asyncio
async def test_accepted_execution_error_does_not_promote_contract_state(lifecycle):
    tx_hash = await accept(
        lifecycle, 0, state(9), execution_result=ExecutionResultStatus.ERROR
    )
    await finalize(lifecycle, tx_hash)
    with lifecycle.sessions() as session:
        assert ContractSnapshot(CONTRACT, session).states == {
            "accepted": state(0),
            "finalized": state(0),
        }


def strip_finalization_state(session, tx_hash):
    session.execute(
        text(
            "UPDATE transactions SET contract_snapshot = contract_snapshot - 'finalization_state' WHERE hash=:hash"
        ),
        {"hash": tx_hash},
    )
    session.commit()


@pytest.mark.asyncio
async def test_legacy_row_without_successor_uses_matching_head(lifecycle):
    first = await accept(lifecycle, 0, state(1))
    with lifecycle.sessions() as session:
        strip_finalization_state(session, first)
    await finalize(lifecycle, first)
    with lifecycle.sessions() as session:
        assert ContractSnapshot(CONTRACT, session).states["finalized"] == state(1)


@pytest.mark.asyncio
async def test_legacy_row_recovers_post_state_from_successor_pre_state(lifecycle):
    first = await accept(lifecycle, 0, state(1))
    second = await accept(lifecycle, 1, state(2))
    with lifecycle.sessions() as session:
        strip_finalization_state(session, first)
    await finalize(lifecycle, first)
    with lifecycle.sessions() as session:
        assert ContractSnapshot(CONTRACT, session).states == {
            "accepted": state(2),
            "finalized": state(1),
        }
    await finalize(lifecycle, second)
    with lifecycle.sessions() as session:
        assert ContractSnapshot(CONTRACT, session).states["finalized"] == state(2)


@pytest.mark.asyncio
@pytest.mark.parametrize("successor_snapshot", ["tampered", "missing"])
async def test_legacy_row_without_verifiable_state_stays_accepted(
    lifecycle, successor_snapshot
):
    first = await accept(lifecycle, 0, state(1))
    second = await accept(lifecycle, 1, state(2))
    with lifecycle.sessions() as session:
        strip_finalization_state(session, first)
        session.execute(
            text(
                "UPDATE transactions SET contract_snapshot = "
                + (
                    "jsonb_set(contract_snapshot, '{states,accepted}', "
                    "CAST(:tampered AS jsonb))"
                    if successor_snapshot == "tampered"
                    else "NULL"
                )
                + " WHERE hash=:hash"
            ),
            {"hash": second, "tampered": json.dumps(state(7))},
        )
        session.commit()
    lifecycle.rollup.reset_mock()
    # Exhaust the generic retry budget: an unverifiable agreed decision
    # must neither be finalized nor canceled to let younger state escape.
    for _ in range(lifecycle.worker.MAX_GENERIC_ERROR_RETRIES + 1):
        await finalize(lifecycle, first, expected_status="ACCEPTED")
    lifecycle.rollup.emit_transaction_event.assert_not_called()
    with lifecycle.sessions() as session:
        assert ContractSnapshot(CONTRACT, session).states["finalized"] == state(0)


@pytest.mark.asyncio
async def test_legacy_report_classifies_without_writing(lifecycle, capsys):
    from backend.database_handler.finalization_legacy_report import report

    first = await accept(lifecycle, 0, state(1))
    second = await accept(lifecycle, 1, state(2))
    with lifecycle.sessions() as session:
        strip_finalization_state(session, first)
    capsys.readouterr()
    with lifecycle.sessions() as session:
        totals = report(session, limit=10)
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines == [f"{first}\t{CONTRACT}\tsuccessor 1 ({second})"]
    assert dict(totals) == {"successor 1": 1}
    with lifecycle.sessions() as session:
        tp = TransactionsProcessor(session)
        assert tp.get_transaction_by_hash(first)["status"] == "ACCEPTED"
        assert ContractSnapshot(CONTRACT, session).states["finalized"] == state(0)


UPGRADE_CODE = "class Contract: pass  # v2"
APPEALER = "0x" + "33" * 20
FEES = {
    "leaderTimeunitsAllocation": 100,
    "validatorTimeunitsAllocation": 200,
    "appealRounds": 0,
    "executionBudgetPerRound": 0,
    "executionConsumed": 0,
    "totalMessageFees": 0,
    "rotations": [0],
    "maxPriceGenPerTimeUnit": 0,
    "storageFeeMaxGasPrice": 0,
    "receiptFeeMaxGasPrice": 0,
}


def code(new_code=UPGRADE_CODE):
    return dict([upgrade_code_slot(new_code, None)])


CODE_KEY = next(iter(code()))


@pytest.fixture
def upgrades(lifecycle, monkeypatch):
    monkeypatch.setenv("UPGRADE_DEFER_SECONDS", "0")
    monkeypatch.delenv("VITE_IS_HOSTED", raising=False)
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    return lifecycle


def queue_upgrade(lifecycle, *, legacy_finalized=False):
    with lifecycle.sessions() as session:
        upgrade_hash = admin_upgrade_contract_code(session, CONTRACT, UPGRADE_CODE)[
            "transaction_hash"
        ]
        if legacy_finalized:
            # What the pre-change worker left: code applied, no predecessor close.
            session.query(Transactions).filter_by(hash=upgrade_hash).one().status = (
                "FINALIZED"
            )
            session.query(Transactions).filter_by(
                hash=upgrade_hash
            ).one().consensus_data = {"upgrade_result": "success"}
            contract = session.query(CurrentState).filter_by(id=CONTRACT).one()
            contract.data = {
                "state": {
                    name: {**tree, **code()}
                    for name, tree in contract.data["state"].items()
                }
            }
            session.commit()
    return upgrade_hash


async def run_upgrade(lifecycle, upgrade_hash):
    with lifecycle.sessions() as session:
        claimed = await lifecycle.worker.claim_next_transaction(session)
        assert claimed is not None and claimed["hash"] == upgrade_hash
        await lifecycle.worker.process_transaction(claimed, session)
    return row_of(lifecycle, upgrade_hash)


def row_of(lifecycle, tx_hash):
    with lifecycle.sessions() as session:
        return TransactionsProcessor(session).get_transaction_by_hash(tx_hash)


def contract_states(lifecycle):
    with lifecycle.sessions() as session:
        return ContractSnapshot(CONTRACT, session).states


def balance(lifecycle, address):
    with lifecycle.sessions() as session:
        return AccountsManager(session).get_account_balance(address)


def add_fee_accounting(lifecycle, tx_hash, *, appeal=False):
    """Give a decided row fee custody, optionally with an admitted paid appeal."""
    accounting = create_fee_accounting(
        fees_distribution=dict(FEES),
        num_of_validators=5,
        submitted_value=required_fee_deposit(FEES, 5),
        user_value=0,
        sender=SENDER,
    )
    charge = 0
    if appeal:
        quote = calculate_appeal_charge(
            accounting["fees_distribution"], current_round=0, status="ACCEPTED"
        )
        charge = quote["bond"] + quote["funding"]
        accounting = record_appeal_bond(
            accounting,
            amount=charge,
            appealer=APPEALER,
            current_round=0,
            status="ACCEPTED",
        )
    with lifecycle.sessions() as session:
        row = session.query(Transactions).filter_by(hash=tx_hash).one()
        row.num_of_initial_validators = 5
        data = {**(row.data or {}), FEE_ACCOUNTING_KEY: accounting}
        if appeal:
            row.appealed = True
            row.timestamp_appeal = int(time.time())
            data[APPEAL_RECOVERY_SNAPSHOT_KEY] = {"status": "ACCEPTED"}
        row.data = data
        session.commit()
    return charge


def insert_pending(lifecycle, nonce):
    tx_hash = "0x" + f"{nonce + 1:064x}"
    with lifecycle.sessions() as session:
        TransactionsProcessor(session).insert_transaction(
            from_address=SENDER,
            to_address=CONTRACT,
            data={"calldata": "Bg=="},
            value=0,
            type=TransactionType.RUN_CONTRACT.value,
            nonce=nonce,
            leader_only=False,
            config_rotation_rounds=3,
            transaction_hash=tx_hash,
        )
        session.commit()
    return tx_hash


async def succeed_validator_appeal(lifecycle, monkeypatch, tx_hash):
    monkeypatch.setattr(
        consensus_base.ConsensusAlgorithm,
        "get_extra_validators",
        staticmethod(lambda *args, **kwargs: (None, [{"address": SENDER}])),
    )
    monkeypatch.setattr(
        consensus_base,
        "CommittingState",
        lambda: SimpleNamespace(
            handle=AsyncMock(return_value=ConsensusRound.VALIDATOR_APPEAL_SUCCESSFUL)
        ),
    )
    with lifecycle.sessions() as session:
        tp = TransactionsProcessor(session)
        tp.set_transaction_appeal(tx_hash, True)
        tx = Transaction.from_dict(tp.get_transaction_by_hash(tx_hash))
        context = context_for(lifecycle, session, tx)
        await lifecycle.worker.consensus_algorithm.process_validator_appeal(
            transaction=tx,
            transactions_processor=tp,
            chain_snapshot=None,
            accounts_manager=context.accounts_manager,
            contract_snapshot_factory=context.contract_snapshot_factory,
            contract_processor=context.contract_processor,
            node_factory=node_factory,
            validators_snapshot=SimpleNamespace(nodes=[]),
        )


@pytest.mark.asyncio
async def test_upgrade_finalizes_decided_predecessor_before_applying_code(upgrades):
    first = await accept(upgrades, 0, state(1))
    add_fee_accounting(upgrades, first)
    upgrade_hash = queue_upgrade(upgrades)

    upgrade = await run_upgrade(upgrades, upgrade_hash)

    assert upgrade["status"] == "FINALIZED"
    assert upgrade["consensus_data"]["upgrade_result"] == "success"
    assert upgrade["consensus_data"]["closed_transactions"] == [first]
    assert upgrade["consensus_data"]["refunded_appeals"] == []
    assert "applied_at" in upgrade["consensus_data"]
    finalized = row_of(upgrades, first)
    assert finalized["status"] == "FINALIZED"
    assert finalized["data"][FEE_ACCOUNTING_KEY]["status"] == "settled"
    assert contract_states(upgrades) == {
        "accepted": {**state(1), **code()},
        "finalized": {**state(1), **code()},
    }


@pytest.mark.asyncio
async def test_never_started_transaction_stays_queued_and_runs_on_new_code(
    upgrades,
):
    first = await accept(upgrades, 0, state(1))
    pending = insert_pending(upgrades, 1)
    upgrade_hash = queue_upgrade(upgrades)

    assert (await run_upgrade(upgrades, upgrade_hash))["status"] == "FINALIZED"
    assert row_of(upgrades, first)["status"] == "FINALIZED"
    assert row_of(upgrades, pending)["status"] == "PENDING"

    await accept(upgrades, 1, {**state(2), **code()}, recompute=True)
    pre_state = row_of(upgrades, pending)["contract_snapshot"]["states"]["accepted"]
    assert pre_state == {**state(1), **code()}


@pytest.mark.asyncio
async def test_upgrade_refunds_pending_paid_appeal_before_finalizing(upgrades):
    first = await accept(upgrades, 0, state(1))
    charge = add_fee_accounting(upgrades, first, appeal=True)
    upgrade_hash = queue_upgrade(upgrades)

    upgrade = await run_upgrade(upgrades, upgrade_hash)

    assert upgrade["status"] == "FINALIZED"
    assert upgrade["consensus_data"]["refunded_appeals"] == [
        {"transaction": first, "refund": charge}
    ]
    assert balance(upgrades, APPEALER) == charge
    finalized = row_of(upgrades, first)
    assert finalized["status"] == "FINALIZED"
    assert finalized["appealed"] is False
    assert finalized["data"][FEE_ACCOUNTING_KEY]["appeal_bonds"] == []
    assert APPEAL_RECOVERY_SNAPSHOT_KEY not in finalized["data"]
    assert contract_states(upgrades)["finalized"] == {**state(1), **code()}


@pytest.mark.asyncio
async def test_queued_or_applied_upgrade_closes_appeal_admission(upgrades):
    first = await accept(upgrades, 0, state(1))
    with upgrades.sessions() as session:
        assert not TransactionsProcessor(session).upgrade_closes_appeals(first)
    upgrade_hash = queue_upgrade(upgrades)
    with upgrades.sessions() as session:
        tp = TransactionsProcessor(session)
        assert tp.upgrade_closes_appeals(first)
        with pytest.raises(ValueError, match="CanNotAppeal"):
            tp.admit_transaction_appeal(
                first,
                expected_decision_id=1,
                submitted_at=int(time.time()),
                appeal_deadline=int(time.time()) + 600,
                retention_bps=0,
                prepare_fee_accounting=lambda accounting: (accounting, 0),
            )
        session.rollback()
        for status, closes in (("FINALIZED", True), ("CANCELED", False)):
            session.execute(
                text(
                    "UPDATE transactions SET status = CAST(:status AS"
                    " transaction_status) WHERE hash = :hash"
                ),
                {"status": status, "hash": upgrade_hash},
            )
            assert tp.upgrade_closes_appeals(first) is closes
        session.rollback()


@pytest.mark.asyncio
async def test_successful_appeal_after_upgrade_keeps_new_code(upgrades, monkeypatch):
    upgrade_hash = queue_upgrade(upgrades)
    assert (await run_upgrade(upgrades, upgrade_hash))["status"] == "FINALIZED"
    later = await accept(upgrades, 0, {**state(1), **code()})

    await succeed_validator_appeal(upgrades, monkeypatch, later)

    assert row_of(upgrades, upgrade_hash)["status"] == "FINALIZED"
    assert row_of(upgrades, later)["status"] == "PENDING"
    assert contract_states(upgrades) == {
        "accepted": {**state(0), **code()},
        "finalized": {**state(0), **code()},
    }


@pytest.mark.asyncio
async def test_rewind_never_resurrects_canceled_rows_or_upgrades(upgrades, monkeypatch):
    first = await accept(upgrades, 0, state(1))
    canceled = insert_pending(upgrades, 1)
    with upgrades.sessions() as session:
        session.query(Transactions).filter_by(hash=canceled).one().status = "CANCELED"
        session.commit()
    upgrade_hash = queue_upgrade(upgrades, legacy_finalized=True)

    await succeed_validator_appeal(upgrades, monkeypatch, first)

    assert row_of(upgrades, first)["status"] == "PENDING"
    assert row_of(upgrades, canceled)["status"] == "CANCELED"
    assert row_of(upgrades, upgrade_hash)["status"] == "FINALIZED"
    # The restored pre-state must not drop the code of the upgrade that overtook it.
    assert contract_states(upgrades)["accepted"] == {**state(0), **code()}


@pytest.mark.asyncio
async def test_unverifiable_predecessor_cancels_upgrade_without_side_effects(
    upgrades,
):
    first = await accept(upgrades, 0, state(1))
    second = await accept(upgrades, 1, state(2))
    with upgrades.sessions() as session:
        strip_finalization_state(session, first)
        session.execute(
            text(
                "UPDATE transactions SET contract_snapshot = jsonb_set("
                "contract_snapshot, '{states,accepted}', CAST(:tampered AS jsonb))"
                " WHERE hash=:hash"
            ),
            {"hash": second, "tampered": json.dumps(state(7))},
        )
        session.commit()
    before = contract_states(upgrades)
    upgrades.rollup.reset_mock()
    upgrade_hash = queue_upgrade(upgrades)

    upgrade = await run_upgrade(upgrades, upgrade_hash)

    assert upgrade["status"] == "CANCELED"
    assert upgrade["consensus_data"] == {
        "upgrade_result": "failed",
        "error": "predecessor_finalization_state",
        "transaction": first,
    }
    assert row_of(upgrades, first)["status"] == "ACCEPTED"
    assert row_of(upgrades, second)["status"] == "ACCEPTED"
    assert contract_states(upgrades) == before
    upgrades.rollup.emit_transaction_event.assert_not_called()


@pytest.mark.asyncio
async def test_upgrade_retry_after_crash_never_refunds_or_settles_twice(
    upgrades, monkeypatch
):
    first = await accept(upgrades, 0, state(1))
    second = await accept(upgrades, 1, state(2))
    add_fee_accounting(upgrades, first)
    charge = add_fee_accounting(upgrades, second, appeal=True)
    upgrade_hash = queue_upgrade(upgrades)
    worker = upgrades.worker
    finalize_transaction = worker._finalize_transaction
    crashed = []

    async def crash_once(transaction, session):
        if transaction.hash == second and not crashed:
            crashed.append(transaction.hash)
            raise RuntimeError("worker died mid-close")
        await finalize_transaction(transaction, session)

    monkeypatch.setattr(worker, "_finalize_transaction", crash_once)

    interrupted = await run_upgrade(upgrades, upgrade_hash)

    assert crashed == [second]
    assert interrupted["status"] == "PENDING"
    assert interrupted["data"]["upgrade_close"] == {
        "closed_transactions": [first, second],
        "refunded_appeals": [{"transaction": second, "refund": charge}],
    }
    assert row_of(upgrades, first)["status"] == "FINALIZED"
    assert row_of(upgrades, second)["status"] == "ACCEPTED"
    assert row_of(upgrades, second)["appealed"] is False
    assert balance(upgrades, APPEALER) == charge
    settled_first = row_of(upgrades, first)["data"][FEE_ACCOUNTING_KEY]
    sender_balance = balance(upgrades, SENDER)
    assert CODE_KEY not in contract_states(upgrades)["accepted"]

    upgrade = await run_upgrade(upgrades, upgrade_hash)

    assert upgrade["status"] == "FINALIZED"
    assert upgrade["consensus_data"]["closed_transactions"] == [first, second]
    assert upgrade["consensus_data"]["refunded_appeals"] == [
        {"transaction": second, "refund": charge}
    ]
    assert balance(upgrades, APPEALER) == charge
    assert row_of(upgrades, first)["data"][FEE_ACCOUNTING_KEY] == settled_first
    second_row = row_of(upgrades, second)
    assert second_row["status"] == "FINALIZED"
    second_accounting = second_row["data"][FEE_ACCOUNTING_KEY]
    assert [a["refund"] for a in second_accounting["aborted_appeals"]] == [charge]
    # total_refunded also counts the appeal refund paid to the appealer.
    assert (
        balance(upgrades, SENDER)
        == sender_balance + second_accounting["total_refunded"] - charge
    )
    assert contract_states(upgrades)["finalized"] == {**state(2), **code()}
    with upgrades.sessions() as session:
        assert await worker.claim_next_transaction(session) is None


@pytest.mark.asyncio
async def test_upgrade_defers_while_stale_leased_appeal_may_still_run(upgrades):
    first = await accept(upgrades, 0, state(1))
    with upgrades.sessions() as session:
        session.execute(
            text(
                "UPDATE transactions SET status = 'COMMITTING',"
                " blocked_at = NOW() - INTERVAL '1 hour', worker_id = 'dead',"
                " data = data || CAST(:snapshot AS jsonb) WHERE hash = :hash"
            ),
            {
                "hash": first,
                "snapshot": json.dumps(
                    {APPEAL_RECOVERY_SNAPSHOT_KEY: {"status": "ACCEPTED"}}
                ),
            },
        )
        session.commit()
    before_row = row_of(upgrades, first)
    before_states = contract_states(upgrades)
    upgrade_hash = queue_upgrade(upgrades)

    upgrade = await run_upgrade(upgrades, upgrade_hash)

    assert upgrade["status"] == "PENDING"
    assert "upgrade_close" not in upgrade["data"]
    with upgrades.sessions() as session:
        assert (
            session.execute(
                text("SELECT blocked_at FROM transactions WHERE hash = :hash"),
                {"hash": upgrade_hash},
            ).scalar_one()
            is None
        )
    after_row = row_of(upgrades, first)
    assert after_row["status"] == "COMMITTING"
    assert after_row["data"] == before_row["data"]
    assert contract_states(upgrades) == before_states


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["UNDETERMINED", "LEADER_TIMEOUT"])
async def test_upgrade_finalizes_undecided_predecessor_without_promotion(
    upgrades, status
):
    first = await accept(upgrades, 0, state(1))
    with upgrades.sessions() as session:
        session.execute(
            text(
                "UPDATE transactions SET status = CAST(:status AS"
                " transaction_status), value = 50, value_credited = false"
                " WHERE hash = :hash"
            ),
            {"status": status, "hash": first},
        )
        session.commit()
    sender_balance = balance(upgrades, SENDER)
    upgrade_hash = queue_upgrade(upgrades)

    upgrade = await run_upgrade(upgrades, upgrade_hash)

    assert upgrade["status"] == "FINALIZED"
    assert upgrade["consensus_data"]["closed_transactions"] == [first]
    assert row_of(upgrades, first)["status"] == "FINALIZED"
    assert balance(upgrades, SENDER) == sender_balance + 50
    assert contract_states(upgrades)["finalized"] == {**state(0), **code()}


@pytest.mark.asyncio
async def test_legacy_interleaved_upgrade_keeps_code_when_older_row_finalizes(
    upgrades, capsys
):
    from backend.database_handler.finalization_legacy_report import report

    first = await accept(upgrades, 0, state(1))
    upgrade_hash = queue_upgrade(upgrades, legacy_finalized=True)

    capsys.readouterr()
    with upgrades.sessions() as session:
        totals = report(session, limit=10)
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines == [
        f"{first}\t{CONTRACT}\tinterleaved upgrade ({upgrade_hash})\tACCEPTED"
    ]
    assert dict(totals) == {"interleaved upgrade": 1}

    await finalize(upgrades, first)

    assert contract_states(upgrades)["finalized"] == {**state(1), **code()}
    with upgrades.sessions() as session:
        totals = report(session, limit=10)
    assert dict(totals) == {}
