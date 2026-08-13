"""Tests for contract state restoration on a successful validator appeal.

Regression tests for the bug where `ConsensusWorker.claim_next_appeal`
omitted `transactions.contract_snapshot` and `transactions.consensus_history`
from its RETURNING list, so every appealed transaction was rebuilt with
`contract_snapshot=None` and an empty `consensus_history`. On
VALIDATOR_APPEAL_SUCCESSFUL, `process_validator_appeal` then fell into the
"no snapshot" branch and restored `accepted_state = {}` for non-deploy
transactions — wiping the contract's accepted state, including the code slot.

Two layers are covered:
1. `claim_next_appeal` must return the two columns (worker.py fix).
2. `process_validator_appeal` must never restore `{}` for a non-deploy
   transaction that is missing its snapshot; it re-fetches from the DB and,
   failing that, skips the restore entirely (base.py defense in depth).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import backend.consensus.base as consensus_base
from backend.consensus.base import ConsensusAlgorithm
from backend.consensus.types import ConsensusRound
from backend.consensus.worker import ConsensusWorker
from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.database_handler.transactions_processor import TransactionStatus
from backend.domain.types import Transaction, TransactionType

TX_HASH = "0xtxhash"
CONTRACT_ADDRESS = "0xcontract"


def _make_snapshot_dict(accepted=None):
    return {
        "contract_address": CONTRACT_ADDRESS,
        "states": {"accepted": accepted or {}, "finalized": {}},
        "balance": 0,
    }


def _make_transaction(*, tx_type=TransactionType.RUN_CONTRACT, contract_snapshot=None):
    """Build a minimal appealed Transaction for process_validator_appeal."""
    tx = Mock(spec=Transaction)
    tx.hash = TX_HASH
    tx.type = tx_type
    tx.to_address = CONTRACT_ADDRESS
    tx.contract_snapshot = contract_snapshot
    tx.consensus_data = Mock()
    tx.consensus_data.leader_receipt = None
    tx.consensus_history = {}
    tx.appeal_failed = 0
    tx.appealed = True
    tx.status = TransactionStatus.ACCEPTED
    return tx


def _make_algorithm():
    """ConsensusAlgorithm without __init__ (which requires env vars / live deps)."""
    algorithm = ConsensusAlgorithm.__new__(ConsensusAlgorithm)
    algorithm.msg_handler = Mock()
    algorithm.consensus_service = Mock()
    algorithm.genvm_manager = Mock()
    algorithm.rollback_transactions = AsyncMock()
    return algorithm


async def _run_validator_appeal(
    algorithm, transaction, transactions_processor, contract_processor, monkeypatch
):
    """Drive process_validator_appeal straight into VALIDATOR_APPEAL_SUCCESSFUL."""
    monkeypatch.setattr(
        ConsensusAlgorithm,
        "get_extra_validators",
        staticmethod(Mock(return_value=(None, [{"address": "0xvalidator"}]))),
    )
    monkeypatch.setattr(
        ConsensusAlgorithm,
        "dispatch_transaction_status_update",
        staticmethod(AsyncMock()),
    )
    committing_state = Mock()
    committing_state.handle = AsyncMock(
        return_value=ConsensusRound.VALIDATOR_APPEAL_SUCCESSFUL
    )
    monkeypatch.setattr(
        consensus_base, "CommittingState", Mock(return_value=committing_state)
    )

    validators_snapshot = Mock()
    validators_snapshot.nodes = []

    factory_snapshot = ContractSnapshot.from_dict(
        _make_snapshot_dict({"factory_slot": "factory_data"})
    )

    await algorithm.process_validator_appeal(
        transaction=transaction,
        transactions_processor=transactions_processor,
        chain_snapshot=None,
        accounts_manager=Mock(),
        contract_snapshot_factory=Mock(return_value=factory_snapshot),
        contract_processor=contract_processor,
        node_factory=Mock(),
        validators_snapshot=validators_snapshot,
    )


class TestValidatorAppealStateRestore:
    """Successful validator appeal must not wipe contract state."""

    @pytest.mark.asyncio
    async def test_missing_snapshot_non_deploy_refetches_from_db(self, monkeypatch):
        """No in-memory snapshot on a RUN_CONTRACT tx: restore from the DB row,
        not from `{}`."""
        stored_accepted = {"code_slot": "b64code", "storage_slot": "value"}
        transactions_processor = Mock()
        transactions_processor.get_transaction_by_hash.return_value = {
            "hash": TX_HASH,
            "contract_snapshot": _make_snapshot_dict(stored_accepted),
        }
        contract_processor = Mock()

        transaction = _make_transaction(contract_snapshot=None)
        await _run_validator_appeal(
            _make_algorithm(),
            transaction,
            transactions_processor,
            contract_processor,
            monkeypatch,
        )

        transactions_processor.get_transaction_by_hash.assert_called_once_with(TX_HASH)
        contract_processor.update_contract_state.assert_called_once_with(
            CONTRACT_ADDRESS, accepted_state=stored_accepted
        )

    @pytest.mark.asyncio
    async def test_missing_snapshot_non_deploy_unfetchable_skips_restore(
        self, monkeypatch
    ):
        """No in-memory snapshot and no DB row either: skip the restore instead
        of writing `{}` (which would delete the contract's code slot)."""
        transactions_processor = Mock()
        transactions_processor.get_transaction_by_hash.return_value = None
        contract_processor = Mock()

        transaction = _make_transaction(contract_snapshot=None)
        await _run_validator_appeal(
            _make_algorithm(),
            transaction,
            transactions_processor,
            contract_processor,
            monkeypatch,
        )

        transactions_processor.get_transaction_by_hash.assert_called_once_with(TX_HASH)
        contract_processor.update_contract_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_snapshot_non_deploy_row_without_snapshot_skips_restore(
        self, monkeypatch
    ):
        """DB row exists but carries no snapshot: still skip the restore."""
        transactions_processor = Mock()
        transactions_processor.get_transaction_by_hash.return_value = {
            "hash": TX_HASH,
            "contract_snapshot": None,
        }
        contract_processor = Mock()

        transaction = _make_transaction(contract_snapshot=None)
        await _run_validator_appeal(
            _make_algorithm(),
            transaction,
            transactions_processor,
            contract_processor,
            monkeypatch,
        )

        contract_processor.update_contract_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_snapshot_deploy_still_clears_state(self, monkeypatch):
        """DEPLOY_CONTRACT with no snapshot keeps the pre-fix behavior: rolling
        back a deploy legitimately clears the contract state."""
        transactions_processor = Mock()
        contract_processor = Mock()

        transaction = _make_transaction(
            tx_type=TransactionType.DEPLOY_CONTRACT, contract_snapshot=None
        )
        await _run_validator_appeal(
            _make_algorithm(),
            transaction,
            transactions_processor,
            contract_processor,
            monkeypatch,
        )

        contract_processor.update_contract_state.assert_called_once_with(
            CONTRACT_ADDRESS, accepted_state={}
        )
        transactions_processor.get_transaction_by_hash.assert_not_called()

    @pytest.mark.asyncio
    async def test_in_memory_snapshot_restores_saved_state(self, monkeypatch):
        """When the transaction carries its snapshot, restore from it directly."""
        saved_accepted = {"code_slot": "b64code", "counter": "0x01"}
        snapshot = ContractSnapshot.from_dict(_make_snapshot_dict(saved_accepted))
        transactions_processor = Mock()
        contract_processor = Mock()

        transaction = _make_transaction(contract_snapshot=snapshot)
        await _run_validator_appeal(
            _make_algorithm(),
            transaction,
            transactions_processor,
            contract_processor,
            monkeypatch,
        )

        contract_processor.update_contract_state.assert_called_once_with(
            CONTRACT_ADDRESS, accepted_state=saved_accepted
        )
        transactions_processor.get_transaction_by_hash.assert_not_called()


def _make_worker():
    """ConsensusWorker without __init__ (which spins up polling infrastructure)."""
    worker = ConsensusWorker.__new__(ConsensusWorker)
    worker.worker_id = "worker-test"
    worker.transaction_timeout_minutes = 20
    worker._log_query_result = Mock()
    return worker


def _make_appeal_row(contract_snapshot, consensus_history):
    """A row shaped like the RETURNING clause of claim_next_appeal."""
    return SimpleNamespace(
        hash=TX_HASH,
        from_address="0xsender",
        to_address=CONTRACT_ADDRESS,
        data=None,
        value=0,
        type=TransactionType.RUN_CONTRACT.value,
        nonce=1,
        gaslimit=0,
        r=None,
        s=None,
        v=None,
        leader_only=False,
        execution_mode="NORMAL",
        sim_config=None,
        status=TransactionStatus.ACCEPTED.value,
        consensus_data=None,
        contract_snapshot=contract_snapshot,
        consensus_history=consensus_history,
        input_data=None,
        created_at=None,
        appealed=True,
        appeal_failed=0,
        timestamp_appeal=None,
        appeal_undetermined=False,
        appeal_leader_timeout=False,
        appeal_validators_timeout=False,
        blocked_at=None,
        triggered_by_hash=None,
    )


class TestClaimNextAppealReturnsSnapshot:
    """claim_next_appeal must surface contract_snapshot and consensus_history."""

    @pytest.mark.asyncio
    async def test_returned_dict_includes_snapshot_and_history(self):
        stored_snapshot = _make_snapshot_dict({"code_slot": "b64code"})
        stored_history = {"consensus_results": [{"consensus_round": "Accepted"}]}
        row = _make_appeal_row(stored_snapshot, stored_history)

        session = Mock()
        session.execute.return_value.first.return_value = row

        worker = _make_worker()
        result = await worker.claim_next_appeal(session)

        assert result is not None
        assert result["contract_snapshot"] == stored_snapshot
        assert result["consensus_history"] == stored_history
        session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_returning_clause_selects_snapshot_and_history_columns(self):
        session = Mock()
        session.execute.return_value.first.return_value = None

        worker = _make_worker()
        result = await worker.claim_next_appeal(session)

        assert result is None
        executed_sql = str(session.execute.call_args[0][0])
        returning_clause = executed_sql.split("RETURNING", 1)[1]
        assert "transactions.contract_snapshot" in returning_clause
        assert "transactions.consensus_history" in returning_clause

    @pytest.mark.asyncio
    async def test_claimed_appeal_hydrates_transaction_snapshot(self):
        """End-to-end over the claimed dict: Transaction.from_dict must produce
        a non-None contract_snapshot and the stored consensus_history — the
        exact properties process_validator_appeal depends on."""
        stored_accepted = {"code_slot": "b64code", "storage_slot": "value"}
        stored_snapshot = _make_snapshot_dict(stored_accepted)
        stored_history = {"consensus_results": [{"consensus_round": "Accepted"}]}
        row = _make_appeal_row(stored_snapshot, stored_history)

        session = Mock()
        session.execute.return_value.first.return_value = row

        worker = _make_worker()
        appeal_data = await worker.claim_next_appeal(session)

        transaction = Transaction.from_dict(appeal_data)
        assert transaction.contract_snapshot is not None
        assert transaction.contract_snapshot.states["accepted"] == stored_accepted
        assert transaction.consensus_history == stored_history
