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
)
import backend.consensus.base as consensus_base
from backend.consensus.types import ConsensusRound
from backend.consensus.worker import ConsensusWorker
from backend.database_handler.accounts_manager import AccountsManager
from backend.database_handler.contract_processor import ContractProcessor
from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.database_handler.models import CurrentState
from backend.database_handler.transactions_processor import TransactionsProcessor
from backend.database_handler.types import ConsensusData
from backend.domain.types import Transaction, TransactionType
from backend.node.base import _SnapshotView
from backend.node.genvm import get_code_slot
from backend.node.types import Address, ExecutionMode, ExecutionResultStatus, Receipt


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


@pytest.mark.asyncio
@pytest.mark.parametrize("overlap", [False, True])
async def test_legacy_row_requires_matching_receipt_hash(lifecycle, overlap):
    first = await accept(lifecycle, 0, state(1))
    if overlap:
        await accept(lifecycle, 1, state(2))
    with lifecycle.sessions() as session:
        session.execute(
            text(
                "UPDATE transactions SET contract_snapshot = contract_snapshot - 'finalization_state' WHERE hash=:hash"
            ),
            {"hash": first},
        )
        session.commit()
    if not overlap:
        await finalize(lifecycle, first)
    else:
        lifecycle.rollup.reset_mock()
        # Exhaust the generic retry budget: an unverifiable agreed decision
        # must neither be finalized nor canceled to let younger state escape.
        for _ in range(lifecycle.worker.MAX_GENERIC_ERROR_RETRIES + 1):
            await finalize(lifecycle, first, expected_status="ACCEPTED")
        lifecycle.rollup.emit_transaction_event.assert_not_called()
    with lifecycle.sessions() as session:
        assert ContractSnapshot(CONTRACT, session).states["finalized"] == state(
            0 if overlap else 1
        )
