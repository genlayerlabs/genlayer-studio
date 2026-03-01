"""Tests for EffectExecutor — verifies effects are dispatched to the correct service methods."""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from types import SimpleNamespace

from backend.consensus.effects import (
    AddTimestampEffect,
    StatusUpdateEffect,
    SendMessageEffect,
    EmitRollupEventEffect,
    DBWriteEffect,
    RegisterContractEffect,
    UpdateContractStateEffect,
    SetTransactionResultEffect,
    SetAppealEffect,
    SetAppealUndeterminedEffect,
    SetAppealLeaderTimeoutEffect,
    SetAppealValidatorsTimeoutEffect,
    SetAppealFailedEffect,
    SetAppealProcessingTimeEffect,
    ResetAppealProcessingTimeEffect,
    SetTimestampAppealEffect,
    SetTimestampAwaitingFinalizationEffect,
    SetContractSnapshotEffect,
    SetLeaderTimeoutValidatorsEffect,
    ResetRotationCountEffect,
    IncreaseRotationCountEffect,
    SetTimestampLastVoteEffect,
    UpdateConsensusHistoryEffect,
    InsertTriggeredTransactionEffect,
    Effect,
)
from backend.consensus.effect_executor import EffectExecutor


def _make_context():
    """Create a minimal mock context with all required services."""
    tp = MagicMock(name="transactions_processor")
    mh = MagicMock(name="msg_handler")
    # Add async send_message_async
    mh.send_message_async = AsyncMock()
    cs = MagicMock(name="consensus_service")
    cp = MagicMock(name="contract_processor")

    ctx = SimpleNamespace(
        transactions_processor=tp,
        msg_handler=mh,
        consensus_service=cs,
        contract_processor=cp,
    )
    return ctx


@pytest.mark.asyncio
class TestAddTimestampEffect:
    async def test_calls_add_state_timestamp(self):
        ctx = _make_context()
        executor = EffectExecutor(ctx)
        await executor.execute(
            [AddTimestampEffect(tx_hash="0x1", state_name="PENDING")]
        )
        ctx.transactions_processor.add_state_timestamp.assert_called_once_with(
            "0x1", "PENDING"
        )


@pytest.mark.asyncio
class TestStatusUpdateEffect:
    async def test_updates_status_and_sends_message_async(self):
        ctx = _make_context()
        executor = EffectExecutor(ctx)
        await executor.execute(
            [StatusUpdateEffect(tx_hash="0x1", new_status="ACCEPTED")]
        )
        ctx.transactions_processor.update_transaction_status.assert_called_once()
        call_args = ctx.transactions_processor.update_transaction_status.call_args
        assert call_args[0][0] == "0x1"
        assert call_args[0][1].value == "ACCEPTED"
        assert call_args[0][2] is True
        ctx.msg_handler.send_message_async.assert_awaited_once()

    async def test_status_update_no_current_status_changes(self):
        ctx = _make_context()
        executor = EffectExecutor(ctx)
        await executor.execute(
            [
                StatusUpdateEffect(
                    tx_hash="0x1",
                    new_status="ACCEPTED",
                    update_current_status_changes=False,
                )
            ]
        )
        call_args = ctx.transactions_processor.update_transaction_status.call_args
        assert call_args[0][2] is False

    async def test_falls_back_to_sync_send_message(self):
        ctx = _make_context()
        del ctx.msg_handler.send_message_async  # Remove async method
        executor = EffectExecutor(ctx)
        await executor.execute(
            [StatusUpdateEffect(tx_hash="0x1", new_status="PENDING")]
        )
        ctx.msg_handler.send_message.assert_called_once()


@pytest.mark.asyncio
class TestSendMessageEffect:
    async def test_sends_message_to_terminal(self):
        ctx = _make_context()
        executor = EffectExecutor(ctx)
        await executor.execute(
            [
                SendMessageEffect(
                    event_name="consensus_event",
                    event_type="info",
                    event_scope="Consensus",
                    message="test msg",
                    data={"key": "val"},
                    tx_hash="0x1",
                )
            ]
        )
        ctx.msg_handler.send_message.assert_called_once()
        log_event = ctx.msg_handler.send_message.call_args[0][0]
        assert log_event.name == "consensus_event"
        assert log_event.message == "test msg"

    async def test_sends_message_not_to_terminal(self):
        ctx = _make_context()
        executor = EffectExecutor(ctx)
        await executor.execute(
            [
                SendMessageEffect(
                    event_name="ev",
                    event_type="info",
                    event_scope="Consensus",
                    message="msg",
                    log_to_terminal=False,
                )
            ]
        )
        ctx.msg_handler.send_message.assert_called_once()
        call_kwargs = ctx.msg_handler.send_message.call_args[1]
        assert call_kwargs["log_to_terminal"] is False


@pytest.mark.asyncio
class TestEmitRollupEventEffect:
    async def test_emits_event_with_extra_args(self):
        ctx = _make_context()
        executor = EffectExecutor(ctx)
        account = {"address": "0xleader"}
        await executor.execute(
            [
                EmitRollupEventEffect(
                    event_name="emitTransactionActivated",
                    account=account,
                    tx_hash="0x1",
                    extra_args=("0xleader", ["0xleader", "0xval"]),
                )
            ]
        )
        ctx.consensus_service.emit_transaction_event.assert_called_once_with(
            "emitTransactionActivated",
            account,
            "0x1",
            "0xleader",
            ["0xleader", "0xval"],
        )

    async def test_emits_event_without_extra_args(self):
        ctx = _make_context()
        executor = EffectExecutor(ctx)
        account = {"address": "0xleader"}
        await executor.execute(
            [
                EmitRollupEventEffect(
                    event_name="emitTransactionLeaderTimeout",
                    account=account,
                    tx_hash="0x1",
                )
            ]
        )
        ctx.consensus_service.emit_transaction_event.assert_called_once_with(
            "emitTransactionLeaderTimeout", account, "0x1"
        )


@pytest.mark.asyncio
class TestDBWriteEffect:
    async def test_calls_method_on_transactions_processor(self):
        ctx = _make_context()
        executor = EffectExecutor(ctx)
        await executor.execute(
            [
                DBWriteEffect(
                    method_name="set_transaction_result",
                    args=("0x1", {"votes": {}}),
                )
            ]
        )
        ctx.transactions_processor.set_transaction_result.assert_called_once_with(
            "0x1", {"votes": {}}
        )


@pytest.mark.asyncio
class TestContractEffects:
    async def test_register_contract(self):
        ctx = _make_context()
        executor = EffectExecutor(ctx)
        data = {"id": "0xcontract", "data": {"state": {}}}
        await executor.execute([RegisterContractEffect(contract_data=data)])
        ctx.contract_processor.register_contract.assert_called_once_with(data)

    async def test_update_contract_state_accepted(self):
        ctx = _make_context()
        executor = EffectExecutor(ctx)
        await executor.execute(
            [
                UpdateContractStateEffect(
                    address="0xcontract", accepted_state={"key": "val"}
                )
            ]
        )
        ctx.contract_processor.update_contract_state.assert_called_once_with(
            "0xcontract", accepted_state={"key": "val"}
        )

    async def test_update_contract_state_finalized(self):
        ctx = _make_context()
        executor = EffectExecutor(ctx)
        await executor.execute(
            [
                UpdateContractStateEffect(
                    address="0xcontract", finalized_state={"key": "val"}
                )
            ]
        )
        ctx.contract_processor.update_contract_state.assert_called_once_with(
            "0xcontract", finalized_state={"key": "val"}
        )


@pytest.mark.asyncio
class TestAppealEffects:
    async def test_set_appeal(self):
        ctx = _make_context()
        executor = EffectExecutor(ctx)
        await executor.execute([SetAppealEffect(tx_hash="0x1", appealed=False)])
        ctx.transactions_processor.set_transaction_appeal.assert_called_once_with(
            "0x1", False
        )

    async def test_set_appeal_undetermined(self):
        ctx = _make_context()
        executor = EffectExecutor(ctx)
        await executor.execute([SetAppealUndeterminedEffect(tx_hash="0x1", value=True)])
        ctx.transactions_processor.set_transaction_appeal_undetermined.assert_called_once_with(
            "0x1", True
        )

    async def test_set_appeal_leader_timeout(self):
        ctx = _make_context()
        executor = EffectExecutor(ctx)
        await executor.execute(
            [SetAppealLeaderTimeoutEffect(tx_hash="0x1", value=True)]
        )
        ctx.transactions_processor.set_transaction_appeal_leader_timeout.assert_called_once_with(
            "0x1", True
        )

    async def test_set_appeal_validators_timeout(self):
        ctx = _make_context()
        executor = EffectExecutor(ctx)
        await executor.execute(
            [SetAppealValidatorsTimeoutEffect(tx_hash="0x1", value=False)]
        )
        ctx.transactions_processor.set_transaction_appeal_validators_timeout.assert_called_once_with(
            "0x1", False
        )

    async def test_set_appeal_failed(self):
        ctx = _make_context()
        executor = EffectExecutor(ctx)
        await executor.execute([SetAppealFailedEffect(tx_hash="0x1", count=3)])
        ctx.transactions_processor.set_transaction_appeal_failed.assert_called_once_with(
            "0x1", 3
        )

    async def test_set_appeal_processing_time(self):
        ctx = _make_context()
        executor = EffectExecutor(ctx)
        await executor.execute([SetAppealProcessingTimeEffect(tx_hash="0x1")])
        ctx.transactions_processor.set_transaction_appeal_processing_time.assert_called_once_with(
            "0x1"
        )

    async def test_reset_appeal_processing_time(self):
        ctx = _make_context()
        executor = EffectExecutor(ctx)
        await executor.execute([ResetAppealProcessingTimeEffect(tx_hash="0x1")])
        ctx.transactions_processor.reset_transaction_appeal_processing_time.assert_called_once_with(
            "0x1"
        )

    async def test_set_timestamp_appeal(self):
        ctx = _make_context()
        executor = EffectExecutor(ctx)
        await executor.execute([SetTimestampAppealEffect(tx_hash="0x1", value=None)])
        ctx.transactions_processor.set_transaction_timestamp_appeal.assert_called_once_with(
            "0x1", None
        )


@pytest.mark.asyncio
class TestTimestampEffects:
    async def test_set_timestamp_awaiting_finalization(self):
        ctx = _make_context()
        executor = EffectExecutor(ctx)
        await executor.execute([SetTimestampAwaitingFinalizationEffect(tx_hash="0x1")])
        ctx.transactions_processor.set_transaction_timestamp_awaiting_finalization.assert_called_once_with(
            "0x1"
        )

    async def test_set_timestamp_last_vote(self):
        ctx = _make_context()
        executor = EffectExecutor(ctx)
        await executor.execute([SetTimestampLastVoteEffect(tx_hash="0x1")])
        ctx.transactions_processor.set_transaction_timestamp_last_vote.assert_called_once_with(
            "0x1"
        )


@pytest.mark.asyncio
class TestMiscEffects:
    async def test_set_contract_snapshot(self):
        ctx = _make_context()
        executor = EffectExecutor(ctx)
        await executor.execute(
            [SetContractSnapshotEffect(tx_hash="0x1", snapshot_dict={"state": {}})]
        )
        ctx.transactions_processor.set_transaction_contract_snapshot.assert_called_once_with(
            "0x1", {"state": {}}
        )

    async def test_set_leader_timeout_validators(self):
        ctx = _make_context()
        executor = EffectExecutor(ctx)
        validators = [{"address": "0xval1"}]
        await executor.execute(
            [SetLeaderTimeoutValidatorsEffect(tx_hash="0x1", validators=validators)]
        )
        ctx.transactions_processor.set_leader_timeout_validators.assert_called_once_with(
            "0x1", validators
        )

    async def test_reset_rotation_count(self):
        ctx = _make_context()
        executor = EffectExecutor(ctx)
        await executor.execute([ResetRotationCountEffect(tx_hash="0x1")])
        ctx.transactions_processor.reset_transaction_rotation_count.assert_called_once_with(
            "0x1"
        )

    async def test_increase_rotation_count(self):
        ctx = _make_context()
        executor = EffectExecutor(ctx)
        await executor.execute([IncreaseRotationCountEffect(tx_hash="0x1")])
        ctx.transactions_processor.increase_transaction_rotation_count.assert_called_once_with(
            "0x1"
        )

    async def test_set_transaction_result(self):
        ctx = _make_context()
        executor = EffectExecutor(ctx)
        data = {"votes": {}, "leader_receipt": None}
        await executor.execute(
            [SetTransactionResultEffect(tx_hash="0x1", consensus_data_dict=data)]
        )
        ctx.transactions_processor.set_transaction_result.assert_called_once_with(
            "0x1", data
        )

    async def test_update_consensus_history(self):
        ctx = _make_context()
        executor = EffectExecutor(ctx)
        await executor.execute(
            [
                UpdateConsensusHistoryEffect(
                    tx_hash="0x1",
                    consensus_round="Accepted",
                    leader_receipt=None,
                    validation_results=[],
                    new_status="ACCEPTED",
                )
            ]
        )
        from backend.database_handler.models import TransactionStatus

        ctx.transactions_processor.update_consensus_history.assert_called_once_with(
            "0x1", "Accepted", None, [], TransactionStatus.ACCEPTED
        )

    async def test_update_consensus_history_no_status(self):
        ctx = _make_context()
        executor = EffectExecutor(ctx)
        await executor.execute(
            [
                UpdateConsensusHistoryEffect(
                    tx_hash="0x1",
                    consensus_round="Leader Rotation",
                    leader_receipt=["receipt"],
                    validation_results=["val1"],
                )
            ]
        )
        ctx.transactions_processor.update_consensus_history.assert_called_once_with(
            "0x1", "Leader Rotation", ["receipt"], ["val1"]
        )


@pytest.mark.asyncio
class TestInsertTriggeredTransactionEffect:
    async def test_inserts_transaction(self):
        ctx = _make_context()
        executor = EffectExecutor(ctx)
        await executor.execute(
            [
                InsertTriggeredTransactionEffect(
                    from_address="0xsender",
                    to_address="0xrecipient",
                    data={"calldata": "0x"},
                    value=0,
                    tx_type="RUN_CONTRACT",
                    nonce=5,
                    leader_only=False,
                    num_of_initial_validators=5,
                    triggered_by_hash="0xparent",
                    transaction_hash="0xchild",
                    config_rotation_rounds=3,
                    sim_config=None,
                    triggered_on="accepted",
                    execution_mode="NORMAL",
                )
            ]
        )
        ctx.transactions_processor.insert_transaction.assert_called_once_with(
            "0xsender",
            "0xrecipient",
            {"calldata": "0x"},
            value=0,
            type="RUN_CONTRACT",
            nonce=5,
            leader_only=False,
            num_of_initial_validators=5,
            triggered_by_hash="0xparent",
            transaction_hash="0xchild",
            config_rotation_rounds=3,
            sim_config=None,
            triggered_on="accepted",
            execution_mode="NORMAL",
        )


@pytest.mark.asyncio
class TestUnknownEffect:
    async def test_raises_for_unknown_effect(self):
        ctx = _make_context()
        executor = EffectExecutor(ctx)
        # Create an unknown effect subclass
        unknown = Effect()
        with pytest.raises(TypeError, match="Unknown effect type"):
            await executor.execute([unknown])


@pytest.mark.asyncio
class TestMultipleEffects:
    async def test_executes_effects_in_order(self):
        ctx = _make_context()
        executor = EffectExecutor(ctx)
        call_order = []
        ctx.transactions_processor.add_state_timestamp.side_effect = (
            lambda *a: call_order.append("timestamp")
        )
        ctx.transactions_processor.update_transaction_status.side_effect = (
            lambda *a: call_order.append("status")
        )

        await executor.execute(
            [
                AddTimestampEffect(tx_hash="0x1", state_name="PENDING"),
                StatusUpdateEffect(tx_hash="0x1", new_status="PROPOSING"),
                AddTimestampEffect(tx_hash="0x1", state_name="PROPOSING"),
            ]
        )

        assert call_order == ["timestamp", "status", "timestamp"]
