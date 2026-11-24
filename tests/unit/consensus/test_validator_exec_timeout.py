import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.consensus import base as consensus
from backend.consensus.base import (
    CommittingState,
    LeaderTimeoutState,
    ProposingState,
    RevealingState,
)
from backend.consensus.effects import (
    SetTransactionResultEffect,
    UpdateConsensusHistoryEffect,
)
from backend.database_handler.types import ConsensusData
from backend.domain.types import TransactionExecutionMode
from backend.node.genvm.error_codes import GenVMInternalError, GenVMErrorCode
from backend.node.genvm.origin.host_fns import ResultCode
from backend.node.types import ExecutionMode, ExecutionResultStatus, Receipt, Vote
from backend.protocol_rpc.fees import FEE_ACCOUNTING_KEY


class _MessageHandler:
    def send_message(self, *_args, **_kwargs):
        return None


def _make_receipt(address: str, vote: Vote) -> Receipt:
    return Receipt(
        result=bytes([ResultCode.RETURN]) + b"ok",
        calldata=b"",
        gas_used=0,
        mode=ExecutionMode.VALIDATOR,
        contract_state={},
        node_config={"address": address},
        execution_result=ExecutionResultStatus.SUCCESS,
        vote=vote,
        genvm_result={"raw_error": {"fatal": False}},
    )


def _snapshot_node(address: str) -> SimpleNamespace:
    validator = MagicMock()
    validator.to_dict.return_value = {"address": address}
    return SimpleNamespace(validator=validator)


@pytest.fixture
def receipt_round(monkeypatch):
    executor = MagicMock(execute=AsyncMock())
    monkeypatch.setattr(consensus, "EffectExecutor", MagicMock(return_value=executor))
    context = SimpleNamespace(
        transaction=SimpleNamespace(
            hash="tx-hash",
            data={},
            execution_mode="NORMAL",
            consensus_history={},
            config_rotation_rounds=1,
            appealed=False,
            appeal_validators_timeout=False,
            appeal_leader_timeout=False,
            appeal_undetermined=False,
            appeal_failed=0,
        ),
        consensus_data=ConsensusData(votes={}, leader_receipt=[], validators=[]),
        votes={},
        rotation_count=0,
        active_fee_round=0,
        transactions_processor=MagicMock(),
        consensus_service=MagicMock(),
        msg_handler=_MessageHandler(),
        validators_snapshot=SimpleNamespace(nodes=[]),
        remaining_validators=[],
    )
    context.transactions_processor.get_transaction_by_hash.return_value = {
        "consensus_history": {}
    }
    return context, executor


@pytest.mark.asyncio
async def test_rotation_replaces_leader_receipt_pair(receipt_round, monkeypatch):
    context, executor = receipt_round
    old_execution = _make_receipt("old-leader", Vote.DISAGREE)
    old_execution.mode = ExecutionMode.LEADER
    old_validation = _make_receipt("old-leader", Vote.DISAGREE)
    context.consensus_data.leader_receipt = [old_execution]
    context.validation_results = [old_validation]
    monkeypatch.setattr(
        consensus.ConsensusAlgorithm,
        "add_new_validator",
        MagicMock(return_value=[{"address": "new-leader"}]),
    )

    proposing = await RevealingState().handle(context)
    assert isinstance(proposing, ProposingState)
    history_effect = next(
        effect
        for effect in executor.execute.await_args.args[0]
        if isinstance(effect, UpdateConsensusHistoryEffect)
    )
    assert history_effect.leader_receipt == [old_execution, old_validation]

    execution = _make_receipt("new-leader", Vote.AGREE)
    execution.mode = ExecutionMode.LEADER
    validation = _make_receipt("new-leader", Vote.AGREE)
    context.contract_snapshot = MagicMock()
    context.contract_snapshot_factory = MagicMock()
    context.genvm_manager = MagicMock()
    context.shared_decoded_value_cache = {}
    context.shared_contract_snapshot_cache = {}
    context.node_factory = MagicMock(
        return_value=SimpleNamespace(exec_transaction=AsyncMock(return_value=execution))
    )
    await proposing.handle(context)
    assert context.consensus_data.leader_receipt == [execution]

    context.validation_results = [validation]
    await RevealingState().handle(context)
    assert context.consensus_data.leader_receipt == [execution, validation]
    assert history_effect.leader_receipt == [old_execution, old_validation]


@pytest.mark.asyncio
async def test_validator_appeal_keeps_original_leader_receipt_pair(receipt_round):
    context, executor = receipt_round
    execution = _make_receipt("leader", Vote.AGREE)
    execution.mode = ExecutionMode.LEADER
    validation = _make_receipt("leader", Vote.AGREE)
    jury_receipt = _make_receipt("appeal-juror", Vote.DISAGREE)
    context.transaction.appealed = True
    context.transaction.consensus_data = ConsensusData(
        votes={"leader": Vote.AGREE.value},
        leader_receipt=[execution, validation],
        validators=[],
    )
    context.consensus_data.leader_receipt = (
        context.transaction.consensus_data.leader_receipt
    )
    context.validation_results = [jury_receipt]

    await RevealingState().handle(context)

    result_effect = next(
        effect
        for effect in executor.execute.await_args.args[0]
        if isinstance(effect, SetTransactionResultEffect)
    )
    assert result_effect.consensus_data_dict["leader_receipt"] == [
        execution.to_dict(strip_contract_state=True),
        validation.to_dict(strip_contract_state=True),
    ]
    assert context.consensus_data.validators == [jury_receipt]


@pytest.mark.asyncio
async def test_committing_times_out_hung_validator_without_blocking(monkeypatch):
    """Outer slot timeout now produces a terminal TIMEOUT vote, not fatal IDLE."""
    monkeypatch.setenv("CONSENSUS_VALIDATOR_EXEC_TIMEOUT_SECONDS", "0.05")

    transaction = SimpleNamespace(hash="tx-hash", data=None)

    tx_processor = MagicMock()
    tx_processor.add_state_timestamp = MagicMock()
    tx_processor.update_transaction_status = MagicMock()
    tx_processor.set_transaction_timestamp_last_vote = MagicMock()

    consensus_service = MagicMock()

    async def exec_hung(_transaction):
        await asyncio.sleep(0.2)
        return _make_receipt("validator-1", Vote.AGREE)

    async def exec_fast(_transaction):
        return _make_receipt("validator-2", Vote.AGREE)

    def node_factory(
        validator,
        _mode,
        _contract_snapshot,
        _leader_receipt,
        _msg_handler,
        _contract_snapshot_factory,
        _validators_snapshot,
        _timing_callback,
        _genvm_manager,
        _shared_decoded_value_cache,
        _shared_contract_snapshot_cache,
    ):
        if validator["address"] == "validator-1":
            return SimpleNamespace(exec_transaction=exec_hung)
        return SimpleNamespace(exec_transaction=exec_fast)

    context = SimpleNamespace(
        transaction=transaction,
        transactions_processor=tx_processor,
        msg_handler=_MessageHandler(),
        consensus_service=consensus_service,
        contract_processor=MagicMock(),
        node_factory=node_factory,
        contract_snapshot=MagicMock(),
        contract_snapshot_factory=MagicMock(),
        validators_snapshot=SimpleNamespace(
            nodes=[
                _snapshot_node("leader"),
                _snapshot_node("validator-1"),
                _snapshot_node("validator-2"),
            ]
        ),
        genvm_manager=MagicMock(),
        shared_decoded_value_cache={},
        shared_contract_snapshot_cache={},
        leader={"address": "leader"},
        remaining_validators=[
            {"address": "validator-1"},
            {"address": "validator-2"},
        ],
        consensus_data=ConsensusData(votes={}, leader_receipt=None, validators=[]),
        validation_results=[],
    )

    start = asyncio.get_running_loop().time()
    next_state = await CommittingState().handle(context)
    elapsed = asyncio.get_running_loop().time() - start

    assert next_state.__class__.__name__ == "RevealingState"
    assert elapsed <= 0.7

    votes = [r.vote for r in context.validation_results]
    assert votes.count(Vote.TIMEOUT) == 1
    assert votes.count(Vote.AGREE) == 1

    timeout_receipt = next(
        r for r in context.validation_results if r.vote == Vote.TIMEOUT
    )
    assert timeout_receipt.genvm_result is not None
    assert (
        timeout_receipt.genvm_result["error_code"] == "CONSENSUS_VALIDATOR_EXEC_TIMEOUT"
    )
    assert timeout_receipt.genvm_result["raw_error"]["causes"] == [
        "VALIDATOR_EXEC_TIMEOUT"
    ]
    assert timeout_receipt.genvm_result["raw_error"]["fatal"] is False

    timeout_timestamps = [
        call.args[1]
        for call in tx_processor.add_state_timestamp.call_args_list
        if "_TIMEOUT" in call.args[1]
    ]
    assert timeout_timestamps


def _make_context(
    *,
    validators: list[str],
    node_factory,
    snapshot_addresses: list[str] | None = None,
) -> SimpleNamespace:
    tx_processor = MagicMock()
    tx_processor.add_state_timestamp = MagicMock()
    tx_processor.update_transaction_status = MagicMock()
    tx_processor.set_transaction_timestamp_last_vote = MagicMock()

    return SimpleNamespace(
        transaction=SimpleNamespace(hash="tx-hash", data=None),
        transactions_processor=tx_processor,
        msg_handler=_MessageHandler(),
        consensus_service=MagicMock(),
        contract_processor=MagicMock(),
        node_factory=node_factory,
        contract_snapshot=MagicMock(),
        contract_snapshot_factory=MagicMock(),
        validators_snapshot=SimpleNamespace(
            nodes=[
                _snapshot_node(address)
                for address in (snapshot_addresses or ["leader", *validators])
            ]
        ),
        genvm_manager=MagicMock(),
        shared_decoded_value_cache={},
        shared_contract_snapshot_cache={},
        leader={"address": "leader"},
        remaining_validators=[{"address": address} for address in validators],
        consensus_data=ConsensusData(votes={}, leader_receipt=None, validators=[]),
        validation_results=[],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["leader", "validator"])
async def test_consensus_state_times_out_at_funded_allocation(monkeypatch, role):
    monkeypatch.setenv("CONSENSUS_FUNDED_EXEC_TIMEOUT_FLOOR_SECONDS", "1")
    monkeypatch.setenv("CONSENSUS_LEADER_EXEC_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("CONSENSUS_VALIDATOR_EXEC_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("GENLAYER_STUDIO_MIN_PROPOSE_TIMEUNITS", "1")
    monkeypatch.setenv("GENLAYER_STUDIO_MIN_COMMIT_TIMEUNITS", "1")
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def exec_hung(_transaction):
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    context = _make_context(
        validators=["validator-1"],
        node_factory=lambda *_args: SimpleNamespace(exec_transaction=exec_hung),
    )
    context.transaction.data = {
        FEE_ACCOUNTING_KEY: {
            "fees_distribution": {
                "leaderTimeunitsAllocation": 1 if role == "leader" else 4,
                "validatorTimeunitsAllocation": 1 if role == "validator" else 4,
            }
        }
    }
    if role == "leader":
        context.involved_validators = [context.leader]
        context.transaction.execution_mode = TransactionExecutionMode.NORMAL
        context.transaction.appeal_leader_timeout = False
        next_state = await asyncio.wait_for(ProposingState().handle(context), 3)
        assert isinstance(next_state, LeaderTimeoutState)
        [receipt] = context.consensus_data.leader_receipt
    else:
        await asyncio.wait_for(CommittingState().handle(context), 3)
        [receipt] = context.validation_results
        assert receipt.vote == Vote.TIMEOUT

    assert started.is_set()
    assert cancelled.is_set()
    assert receipt.processing_time == 1000
    assert (
        receipt.genvm_result["error_code"] == f"CONSENSUS_{role.upper()}_EXEC_TIMEOUT"
    )


@pytest.mark.asyncio
async def test_outer_timeout_does_not_trigger_replacement(monkeypatch):
    """Replacement is no longer driven by the outer timeout receipt's fatal flag."""
    monkeypatch.setenv("CONSENSUS_VALIDATOR_EXEC_TIMEOUT_SECONDS", "0.02")
    calls: list[str] = []

    async def exec_hung(_transaction):
        await asyncio.sleep(0.2)
        return _make_receipt("validator-1", Vote.AGREE)

    async def exec_replacement(_transaction):
        return _make_receipt("replacement", Vote.AGREE)

    def node_factory(validator, *_args):
        calls.append(validator["address"])
        if validator["address"] == "validator-1":
            return SimpleNamespace(exec_transaction=exec_hung)
        return SimpleNamespace(exec_transaction=exec_replacement)

    context = _make_context(
        validators=["validator-1"],
        snapshot_addresses=["leader", "validator-1", "replacement"],
        node_factory=node_factory,
    )

    await CommittingState().handle(context)

    assert "replacement" not in calls
    timeout_receipt = next(
        r
        for r in context.validation_results
        if r.node_config["address"] == "validator-1"
    )
    assert timeout_receipt.vote == Vote.TIMEOUT
    assert timeout_receipt.genvm_result["raw_error"]["fatal"] is False


@pytest.mark.asyncio
async def test_fatal_internal_error_still_replaces_within_slot(monkeypatch):
    """Fatal GenVM internal errors still use replacement validators within budget."""
    monkeypatch.setenv("CONSENSUS_VALIDATOR_EXEC_TIMEOUT_SECONDS", "1")
    calls: list[str] = []

    async def exec_validator(_transaction):
        raise GenVMInternalError(
            message="fatal",
            error_code=GenVMErrorCode.LLM_NO_PROVIDER,
            causes=["NO_PROVIDER_FOR_PROMPT"],
            is_fatal=True,
        )

    async def exec_replacement(_transaction):
        return _make_receipt("replacement", Vote.AGREE)

    def node_factory(validator, *_args):
        calls.append(validator["address"])
        if validator["address"] == "validator-1":
            return SimpleNamespace(exec_transaction=exec_validator)
        return SimpleNamespace(exec_transaction=exec_replacement)

    context = _make_context(
        validators=["validator-1"],
        snapshot_addresses=["leader", "validator-1", "replacement"],
        node_factory=node_factory,
    )

    await CommittingState().handle(context)

    assert calls == ["validator-1", "replacement"]
    assert context.validation_results[0].node_config["address"] == "replacement"
    assert context.validation_results[0].vote == Vote.AGREE


@pytest.mark.asyncio
async def test_quorum_short_circuits_pending_validators(monkeypatch):
    monkeypatch.setenv("CONSENSUS_VALIDATOR_EXEC_TIMEOUT_SECONDS", "1")

    async def exec_fast(_transaction, address: str):
        return _make_receipt(address, Vote.AGREE)

    async def exec_slow(_transaction):
        await asyncio.sleep(1)
        return _make_receipt("slow", Vote.DISAGREE)

    def node_factory(validator, *_args):
        if validator["address"] in {"validator-4", "validator-5"}:
            return SimpleNamespace(exec_transaction=exec_slow)
        return SimpleNamespace(
            exec_transaction=lambda transaction, address=validator[
                "address"
            ]: exec_fast(transaction, address)
        )

    context = _make_context(
        validators=[
            "validator-1",
            "validator-2",
            "validator-3",
            "validator-4",
            "validator-5",
        ],
        node_factory=node_factory,
    )

    start = asyncio.get_running_loop().time()
    await CommittingState().handle(context)
    elapsed = asyncio.get_running_loop().time() - start

    assert elapsed < 0.2
    assert len(context.validation_results) == 5
    votes = [result.vote for result in context.validation_results]
    assert votes.count(Vote.AGREE) == 3
    assert votes.count(Vote.IDLE) == 2


@pytest.mark.asyncio
async def test_no_quorum_replaces_timeout_slots_only(monkeypatch):
    monkeypatch.setenv("CONSENSUS_VALIDATOR_EXEC_TIMEOUT_SECONDS", "0.02")
    calls: list[str] = []

    async def exec_hung(_transaction):
        await asyncio.sleep(0.2)
        return _make_receipt("validator-1", Vote.AGREE)

    async def exec_vote(address: str, vote: Vote):
        return _make_receipt(address, vote)

    async def exec_replacement(_transaction):
        return _make_receipt("replacement", Vote.AGREE)

    def node_factory(validator, *_args):
        calls.append(validator["address"])
        if validator["address"] == "validator-1":
            return SimpleNamespace(exec_transaction=exec_hung)
        if validator["address"] == "replacement":
            return SimpleNamespace(exec_transaction=exec_replacement)
        vote = Vote.AGREE if validator["address"] == "validator-4" else Vote.DISAGREE
        return SimpleNamespace(
            exec_transaction=lambda _transaction, address=validator["address"]: (
                exec_vote(address, vote)
            )
        )

    context = _make_context(
        validators=["validator-1", "validator-2", "validator-3", "validator-4"],
        snapshot_addresses=[
            "leader",
            "validator-1",
            "validator-2",
            "validator-3",
            "validator-4",
            "replacement",
        ],
        node_factory=node_factory,
    )

    await CommittingState().handle(context)

    assert calls.count("validator-2") == 1
    assert calls.count("validator-3") == 1
    assert calls.count("validator-4") == 1
    assert "replacement" in calls
    assert [result.node_config["address"] for result in context.validation_results] == [
        "replacement",
        "validator-2",
        "validator-3",
        "validator-4",
    ]


@pytest.mark.asyncio
async def test_unfunded_message_becomes_leader_timeout_at_proposal():
    from eth_abi import encode

    from backend.node.types import PendingTransaction
    from backend.protocol_rpc.fees import create_fee_accounting

    fee_params = encode(
        ["(uint256,uint256,uint256,uint256,uint256[],uint256,uint256,uint256)"],
        [(5, 10, 0, 0, [0], 1, 2**200, 2**200)],
    )

    async def exec_ok(_transaction):
        return Receipt(
            result=bytes([ResultCode.RETURN]),
            calldata=b"",
            gas_used=0,
            mode=ExecutionMode.LEADER,
            contract_state={},
            node_config={"address": "leader"},
            execution_result=ExecutionResultStatus.SUCCESS,
            vote=None,
            pending_transactions=[
                PendingTransaction(
                    address="0x2222222222222222222222222222222222222222",
                    calldata=b"\x06",
                    code=None,
                    salt_nonce=0,
                    on="finalized",
                    value=2 * 10**18,
                    fee_params=fee_params,
                    declared_budget=0,
                )
            ],
        )

    context = _make_context(
        validators=["validator-1"],
        node_factory=lambda *_args: SimpleNamespace(exec_transaction=exec_ok),
    )
    context.transaction.data = {
        FEE_ACCOUNTING_KEY: create_fee_accounting(
            fees_distribution={
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
            },
            num_of_validators=5,
            submitted_value=1100,
            user_value=0,
        )
    }
    context.involved_validators = [context.leader]
    context.transaction.execution_mode = TransactionExecutionMode.NORMAL
    context.transaction.appeal_leader_timeout = False

    next_state = await asyncio.wait_for(ProposingState().handle(context), 3)

    assert isinstance(next_state, LeaderTimeoutState)
    [receipt] = context.consensus_data.leader_receipt
    assert receipt.genvm_result["stderr"] == "MessageDeclaredBudgetInsufficient"
