from __future__ import annotations

from backend.consensus.effects import (
    Effect,
    AddTimestampEffect,
    StatusUpdateEffect,
    SendMessageEffect,
    EmitRollupEventEffect,
    DBWriteEffect,
    RegisterContractEffect,
    UpdateContractStateEffect,
    InsertTriggeredTransactionEffect,
    UpdateConsensusHistoryEffect,
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
)
from backend.database_handler.models import TransactionStatus
from backend.protocol_rpc.message_handler.types import LogEvent, EventType, EventScope


class EffectExecutor:
    """Executes a list of Effect objects against real infrastructure.

    Each effect dataclass maps to a concrete call on the appropriate service
    (TransactionsProcessor, MessageHandler, ConsensusService, ContractProcessor).
    """

    def __init__(self, context):
        """
        Args:
            context: A TransactionContext (or any object exposing
                     transactions_processor, msg_handler, consensus_service,
                     contract_processor).
        """
        self.ctx = context

    async def execute(self, effects: list[Effect]) -> None:
        for effect in effects:
            await self._execute_one(effect)

    async def _execute_one(self, effect: Effect) -> None:
        tp = self.ctx.transactions_processor
        mh = self.ctx.msg_handler
        cs = self.ctx.consensus_service
        cp = self.ctx.contract_processor

        if isinstance(effect, AddTimestampEffect):
            tp.add_state_timestamp(effect.tx_hash, effect.state_name)

        elif isinstance(effect, StatusUpdateEffect):
            status = TransactionStatus(effect.new_status)
            tp.update_transaction_status(
                effect.tx_hash,
                status,
                effect.update_current_status_changes,
            )
            log_event = LogEvent(
                "transaction_status_updated",
                EventType.INFO,
                EventScope.CONSENSUS,
                f"{status.value} {effect.tx_hash}",
                {
                    "hash": effect.tx_hash,
                    "new_status": status.value,
                },
                transaction_hash=effect.tx_hash,
            )
            if hasattr(mh, "send_message_async"):
                await mh.send_message_async(log_event)
            else:
                mh.send_message(log_event)

        elif isinstance(effect, SendMessageEffect):
            log_event = LogEvent(
                effect.event_name,
                EventType(effect.event_type),
                EventScope(effect.event_scope),
                effect.message,
                effect.data,
                transaction_hash=effect.tx_hash,
            )
            if effect.log_to_terminal:
                mh.send_message(log_event)
            else:
                mh.send_message(log_event=log_event, log_to_terminal=False)

        elif isinstance(effect, EmitRollupEventEffect):
            cs.emit_transaction_event(
                effect.event_name,
                effect.account,
                effect.tx_hash,
                *effect.extra_args,
            )

        elif isinstance(effect, DBWriteEffect):
            method = getattr(tp, effect.method_name)
            method(*effect.args, **effect.kwargs)

        elif isinstance(effect, RegisterContractEffect):
            cp.register_contract(effect.contract_data)

        elif isinstance(effect, UpdateContractStateEffect):
            kwargs = {}
            if effect.accepted_state is not None:
                kwargs["accepted_state"] = effect.accepted_state
            if effect.finalized_state is not None:
                kwargs["finalized_state"] = effect.finalized_state
            cp.update_contract_state(effect.address, **kwargs)

        elif isinstance(effect, InsertTriggeredTransactionEffect):
            tp.insert_transaction(
                effect.from_address,
                effect.to_address,
                effect.data,
                value=effect.value,
                type=effect.tx_type,
                nonce=effect.nonce,
                leader_only=effect.leader_only,
                num_of_initial_validators=effect.num_of_initial_validators,
                triggered_by_hash=effect.triggered_by_hash,
                transaction_hash=effect.transaction_hash,
                config_rotation_rounds=effect.config_rotation_rounds,
                sim_config=effect.sim_config,
                triggered_on=effect.triggered_on,
                execution_mode=effect.execution_mode,
            )

        elif isinstance(effect, UpdateConsensusHistoryEffect):
            args = [
                effect.tx_hash,
                effect.consensus_round,
                effect.leader_receipt,
                effect.validation_results,
            ]
            if effect.new_status is not None:
                args.append(TransactionStatus(effect.new_status))
            tp.update_consensus_history(*args)

        elif isinstance(effect, SetTransactionResultEffect):
            tp.set_transaction_result(effect.tx_hash, effect.consensus_data_dict)

        elif isinstance(effect, SetAppealEffect):
            tp.set_transaction_appeal(effect.tx_hash, effect.appealed)

        elif isinstance(effect, SetAppealUndeterminedEffect):
            tp.set_transaction_appeal_undetermined(effect.tx_hash, effect.value)

        elif isinstance(effect, SetAppealLeaderTimeoutEffect):
            tp.set_transaction_appeal_leader_timeout(effect.tx_hash, effect.value)

        elif isinstance(effect, SetAppealValidatorsTimeoutEffect):
            tp.set_transaction_appeal_validators_timeout(effect.tx_hash, effect.value)

        elif isinstance(effect, SetAppealFailedEffect):
            tp.set_transaction_appeal_failed(effect.tx_hash, effect.count)

        elif isinstance(effect, SetAppealProcessingTimeEffect):
            tp.set_transaction_appeal_processing_time(effect.tx_hash)

        elif isinstance(effect, ResetAppealProcessingTimeEffect):
            tp.reset_transaction_appeal_processing_time(effect.tx_hash)

        elif isinstance(effect, SetTimestampAppealEffect):
            tp.set_transaction_timestamp_appeal(effect.tx_hash, effect.value)

        elif isinstance(effect, SetTimestampAwaitingFinalizationEffect):
            tp.set_transaction_timestamp_awaiting_finalization(effect.tx_hash)

        elif isinstance(effect, SetContractSnapshotEffect):
            tp.set_transaction_contract_snapshot(effect.tx_hash, effect.snapshot_dict)

        elif isinstance(effect, SetLeaderTimeoutValidatorsEffect):
            tp.set_leader_timeout_validators(effect.tx_hash, effect.validators)

        elif isinstance(effect, ResetRotationCountEffect):
            tp.reset_transaction_rotation_count(effect.tx_hash)

        elif isinstance(effect, IncreaseRotationCountEffect):
            tp.increase_transaction_rotation_count(effect.tx_hash)

        elif isinstance(effect, SetTimestampLastVoteEffect):
            tp.set_transaction_timestamp_last_vote(effect.tx_hash)

        else:
            raise TypeError(f"Unknown effect type: {type(effect).__name__}")
