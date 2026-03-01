"""Pure decision functions for consensus states.

Each function takes scalar inputs (no TransactionContext) and returns
a list of Effect objects plus metadata. This makes the decision logic
fully testable without mocks.
"""

from __future__ import annotations

from typing import Any

from backend.consensus.effects import (
    AddTimestampEffect,
    StatusUpdateEffect,
    SendMessageEffect,
    SetTransactionResultEffect,
    UpdateConsensusHistoryEffect,
    SetTimestampAwaitingFinalizationEffect,
    SetAppealUndeterminedEffect,
    SetAppealEffect,
    SetAppealFailedEffect,
    SetAppealProcessingTimeEffect,
    ResetAppealProcessingTimeEffect,
    SetTimestampAppealEffect,
    SetContractSnapshotEffect,
    SetLeaderTimeoutValidatorsEffect,
    SetAppealLeaderTimeoutEffect,
    EmitRollupEventEffect,
    RegisterContractEffect,
    UpdateContractStateEffect,
    IncreaseRotationCountEffect,
    SetTimestampLastVoteEffect,
    Effect,
)
from backend.consensus.types import ConsensusResult, ConsensusRound


# ── UndeterminedState ──────────────────────────────────────────────


def decide_undetermined(
    tx_hash: str,
    appeal_undetermined: bool,
    appeal_failed: int,
    has_contract_snapshot: bool,
    contract_snapshot_dict: dict | None,
    consensus_data_dict: dict,
    timestamp_appeal: int | None,
    leader_receipt: list | None,
    validators: list | None,
    redacted_consensus_data: dict,
) -> tuple[list[Effect], ConsensusRound]:
    """Decide effects for UndeterminedState.

    Returns:
        (effects, consensus_round)
    """
    effects: list[Effect] = []

    # Record timestamp
    effects.append(AddTimestampEffect(tx_hash=tx_hash, state_name="UNDETERMINED"))

    # Send failure message
    effects.append(
        SendMessageEffect(
            event_name="consensus_event",
            event_type="error",
            event_scope="Consensus",
            message="Failed to reach consensus",
            data={
                "transaction_hash": tx_hash,
                "consensus_data": redacted_consensus_data,
            },
            tx_hash=tx_hash,
        )
    )

    # When appeal fails, the appeal window is not reset
    if not appeal_undetermined:
        effects.append(SetTimestampAwaitingFinalizationEffect(tx_hash=tx_hash))

    # Determine consensus round and appeal-specific effects
    if appeal_undetermined:
        consensus_round = ConsensusRound.LEADER_APPEAL_FAILED
        effects.append(SetAppealUndeterminedEffect(tx_hash=tx_hash, value=False))
        effects.append(SetAppealFailedEffect(tx_hash=tx_hash, count=appeal_failed + 1))
    else:
        consensus_round = ConsensusRound.UNDETERMINED

    # Save contract snapshot if not already saved
    if not has_contract_snapshot:
        effects.append(
            SetContractSnapshotEffect(
                tx_hash=tx_hash, snapshot_dict=contract_snapshot_dict
            )
        )

    # Set transaction result
    effects.append(
        SetTransactionResultEffect(
            tx_hash=tx_hash, consensus_data_dict=consensus_data_dict
        )
    )

    # Increment appeal processing time when transaction was appealed
    if timestamp_appeal is not None:
        effects.append(SetAppealProcessingTimeEffect(tx_hash=tx_hash))

    # Update consensus history
    effects.append(
        UpdateConsensusHistoryEffect(
            tx_hash=tx_hash,
            consensus_round=consensus_round,
            leader_receipt=leader_receipt,
            validation_results=validators,
            new_status="UNDETERMINED",
        )
    )

    # Update status
    effects.append(
        StatusUpdateEffect(
            tx_hash=tx_hash,
            new_status="UNDETERMINED",
            update_current_status_changes=False,
        )
    )

    return effects, consensus_round


# ── LeaderTimeoutState ─────────────────────────────────────────────


def decide_leader_timeout(
    tx_hash: str,
    appeal_undetermined: bool,
    appeal_leader_timeout: bool,
    has_contract_snapshot: bool,
    contract_snapshot_dict: dict | None,
    leader_receipt: list | None,
    remaining_validators: list,
    leader: dict,
) -> tuple[list[Effect], ConsensusRound]:
    """Decide effects for LeaderTimeoutState.

    Returns:
        (effects, consensus_round)
    """
    effects: list[Effect] = []

    # Record timestamp
    effects.append(AddTimestampEffect(tx_hash=tx_hash, state_name="LEADER_TIMEOUT"))

    # Save contract snapshot if not already saved
    if not has_contract_snapshot:
        effects.append(
            SetContractSnapshotEffect(
                tx_hash=tx_hash, snapshot_dict=contract_snapshot_dict
            )
        )

    # Determine consensus round based on appeal type
    if appeal_undetermined:
        consensus_round = ConsensusRound.LEADER_APPEAL_SUCCESSFUL
        effects.append(SetTimestampAwaitingFinalizationEffect(tx_hash=tx_hash))
        effects.append(ResetAppealProcessingTimeEffect(tx_hash=tx_hash))
        effects.append(SetTimestampAppealEffect(tx_hash=tx_hash, value=None))
    elif appeal_leader_timeout:
        consensus_round = ConsensusRound.LEADER_TIMEOUT_APPEAL_FAILED
        effects.append(SetAppealProcessingTimeEffect(tx_hash=tx_hash))
    else:
        consensus_round = ConsensusRound.LEADER_TIMEOUT
        effects.append(SetTimestampAwaitingFinalizationEffect(tx_hash=tx_hash))

    # Save involved validators for appeal
    effects.append(
        SetLeaderTimeoutValidatorsEffect(
            tx_hash=tx_hash, validators=remaining_validators
        )
    )

    # Update consensus history
    effects.append(
        UpdateConsensusHistoryEffect(
            tx_hash=tx_hash,
            consensus_round=consensus_round,
            leader_receipt=leader_receipt,
            validation_results=[],
            new_status="LEADER_TIMEOUT",
        )
    )

    # Update status
    effects.append(
        StatusUpdateEffect(
            tx_hash=tx_hash,
            new_status="LEADER_TIMEOUT",
            update_current_status_changes=False,
        )
    )

    # Emit rollup event
    effects.append(
        EmitRollupEventEffect(
            event_name="emitTransactionLeaderTimeout",
            account=leader,
            tx_hash=tx_hash,
        )
    )

    return effects, consensus_round


# ── ValidatorsTimeoutState ─────────────────────────────────────────


def decide_validators_timeout(
    tx_hash: str,
    appeal_undetermined: bool,
    appeal_validators_timeout: bool,
    appeal_leader_timeout: bool,
    appeal_failed: int,
    has_contract_snapshot: bool,
    contract_snapshot_dict: dict | None,
    consensus_data_dict: dict,
    leader_receipt: list | None,
    validation_results: list,
) -> tuple[list[Effect], ConsensusRound]:
    """Decide effects for ValidatorsTimeoutState.

    Returns:
        (effects, consensus_round)
    """
    effects: list[Effect] = []

    # Record timestamp
    effects.append(AddTimestampEffect(tx_hash=tx_hash, state_name="VALIDATORS_TIMEOUT"))

    # Determine consensus round based on appeal type
    if appeal_undetermined:
        consensus_round = ConsensusRound.LEADER_APPEAL_SUCCESSFUL
        effects.append(SetTimestampAwaitingFinalizationEffect(tx_hash=tx_hash))
        effects.append(ResetAppealProcessingTimeEffect(tx_hash=tx_hash))
        effects.append(SetTimestampAppealEffect(tx_hash=tx_hash, value=None))
        effects.append(SetAppealUndeterminedEffect(tx_hash=tx_hash, value=False))
    elif appeal_validators_timeout:
        consensus_round = ConsensusRound.VALIDATORS_TIMEOUT_APPEAL_FAILED
        effects.append(SetAppealProcessingTimeEffect(tx_hash=tx_hash))
        effects.append(SetAppealFailedEffect(tx_hash=tx_hash, count=appeal_failed + 1))
    else:
        consensus_round = ConsensusRound.VALIDATORS_TIMEOUT
        effects.append(SetTimestampAwaitingFinalizationEffect(tx_hash=tx_hash))

    # Clear leader timeout appeal if active
    if appeal_leader_timeout:
        effects.append(SetAppealLeaderTimeoutEffect(tx_hash=tx_hash, value=False))

    # Set transaction result
    effects.append(
        SetTransactionResultEffect(
            tx_hash=tx_hash, consensus_data_dict=consensus_data_dict
        )
    )

    # Update consensus history (no leader receipt when appeal failed)
    effects.append(
        UpdateConsensusHistoryEffect(
            tx_hash=tx_hash,
            consensus_round=consensus_round,
            leader_receipt=(
                None
                if consensus_round == ConsensusRound.VALIDATORS_TIMEOUT_APPEAL_FAILED
                else leader_receipt
            ),
            validation_results=validation_results,
            new_status="VALIDATORS_TIMEOUT",
        )
    )

    # Update status
    effects.append(
        StatusUpdateEffect(
            tx_hash=tx_hash,
            new_status="VALIDATORS_TIMEOUT",
            update_current_status_changes=False,
        )
    )

    # Save contract snapshot if not already saved
    if not has_contract_snapshot:
        effects.append(
            SetContractSnapshotEffect(
                tx_hash=tx_hash, snapshot_dict=contract_snapshot_dict
            )
        )

    return effects, consensus_round


# ── AcceptedState ──────────────────────────────────────────────────


def decide_accepted(
    tx_hash: str,
    appeal_undetermined: bool,
    appealed: bool,
    appeal_leader_timeout: bool,
    appeal_failed: int,
    consensus_data_dict: dict,
    leader_receipt_list: Any,
    validation_results: list,
    redacted_consensus_data: dict,
    has_contract_snapshot: bool,
    contract_snapshot_dict: dict | None,
    execution_result_success: bool,
    tx_type_deploy: bool,
    accepted_contract_state: dict | None,
    contract_address: str | None,
    contract_code: Any | None,
    code_slot_b64: str | None,
    to_address: str,
    leader_node_config: dict,
) -> tuple[list[Effect], list[Effect], ConsensusRound, ConsensusRound | None]:
    """Decide effects for AcceptedState.

    Returns:
        (pre_effects, post_effects, consensus_round, return_value)

        pre_effects: Execute BEFORE triggered-tx processing.
        post_effects: Execute AFTER triggered-tx processing.
        return_value: What handle() should return from the state machine.

    Note: Triggered transaction processing (_get_messages_data / _emit_messages)
    stays in handle() because it involves DB reads (nonce lookup, account creation).
    """
    pre_effects: list[Effect] = []
    post_effects: list[Effect] = []

    # Record timestamp
    pre_effects.append(AddTimestampEffect(tx_hash=tx_hash, state_name="ACCEPTED"))

    # ── Consensus round determination ──
    if appeal_undetermined:
        consensus_round = ConsensusRound.LEADER_APPEAL_SUCCESSFUL
        pre_effects.append(SetTimestampAwaitingFinalizationEffect(tx_hash=tx_hash))
        pre_effects.append(ResetAppealProcessingTimeEffect(tx_hash=tx_hash))
        pre_effects.append(SetTimestampAppealEffect(tx_hash=tx_hash, value=None))
        pre_effects.append(SetAppealFailedEffect(tx_hash=tx_hash, count=0))
    elif not appealed:
        consensus_round = ConsensusRound.ACCEPTED
        pre_effects.append(SetTimestampAwaitingFinalizationEffect(tx_hash=tx_hash))
    else:
        consensus_round = ConsensusRound.VALIDATOR_APPEAL_FAILED
        pre_effects.append(SetAppealEffect(tx_hash=tx_hash, appealed=False))
        pre_effects.append(SetAppealProcessingTimeEffect(tx_hash=tx_hash))
        pre_effects.append(
            SetAppealFailedEffect(tx_hash=tx_hash, count=appeal_failed + 1)
        )

    # Set transaction result
    pre_effects.append(
        SetTransactionResultEffect(
            tx_hash=tx_hash, consensus_data_dict=consensus_data_dict
        )
    )

    # Update consensus history
    pre_effects.append(
        UpdateConsensusHistoryEffect(
            tx_hash=tx_hash,
            consensus_round=consensus_round,
            leader_receipt=(
                None
                if consensus_round == ConsensusRound.VALIDATOR_APPEAL_FAILED
                else leader_receipt_list
            ),
            validation_results=validation_results,
            new_status="ACCEPTED",
        )
    )

    # Send consensus reached message
    pre_effects.append(
        SendMessageEffect(
            event_name="consensus_event",
            event_type="success",
            event_scope="Consensus",
            message="Reached consensus",
            data={
                "transaction_hash": tx_hash,
                "consensus_data": redacted_consensus_data,
            },
            tx_hash=tx_hash,
        )
    )

    # ── Contract write decision ──
    if not appealed:
        # Save contract snapshot for rollback
        if not has_contract_snapshot:
            pre_effects.append(
                SetContractSnapshotEffect(
                    tx_hash=tx_hash, snapshot_dict=contract_snapshot_dict
                )
            )

        if execution_result_success:
            if tx_type_deploy:
                new_contract = {
                    "id": contract_address,
                    "data": {
                        "state": {
                            "accepted": accepted_contract_state,
                            "finalized": {
                                code_slot_b64: (accepted_contract_state or {}).get(
                                    code_slot_b64, b""
                                )
                            },
                        },
                        "code": contract_code,
                    },
                }
                pre_effects.append(RegisterContractEffect(contract_data=new_contract))
                pre_effects.append(
                    SendMessageEffect(
                        event_name="deployed_contract",
                        event_type="success",
                        event_scope="GenVM",
                        message="Contract deployed",
                        data=new_contract,
                        tx_hash=tx_hash,
                    )
                )
            else:
                pre_effects.append(
                    UpdateContractStateEffect(
                        address=to_address,
                        accepted_state=accepted_contract_state,
                    )
                )

            # NOTE: Triggered transaction processing (_get_messages_data +
            # emitTransactionAccepted rollup + _emit_messages) happens in
            # handle() between pre_effects and post_effects execution.
    else:
        # Appeal failed: emit rollup with empty messages
        pre_effects.append(
            EmitRollupEventEffect(
                event_name="emitTransactionAccepted",
                account=leader_node_config,
                tx_hash=tx_hash,
                extra_args=([],),
            )
        )

    # ── Post effects ──
    post_effects.append(
        StatusUpdateEffect(
            tx_hash=tx_hash,
            new_status="ACCEPTED",
            update_current_status_changes=False,
        )
    )

    # Appeal cleanup + return value
    if appeal_undetermined:
        post_effects.append(SetAppealUndeterminedEffect(tx_hash=tx_hash, value=False))
        return_value = consensus_round
    elif appeal_leader_timeout:
        post_effects.append(SetAppealLeaderTimeoutEffect(tx_hash=tx_hash, value=False))
        return_value = ConsensusRound.LEADER_TIMEOUT_APPEAL_SUCCESSFUL
    elif consensus_round == ConsensusRound.ACCEPTED:
        return_value = consensus_round
    else:
        return_value = None

    return pre_effects, post_effects, consensus_round, return_value


# ── FinalizingState ────────────────────────────────────────────────


def decide_finalizing(
    tx_hash: str,
    tx_status_accepted: bool,
    execution_result_success: bool,
    leader_node_config: dict,
) -> tuple[list[Effect], list[Effect], bool]:
    """Decide effects for FinalizingState.

    Returns:
        (pre_effects, post_effects, should_finalize_contract)

        pre_effects: Execute BEFORE contract finalization / triggered-tx processing.
        post_effects: Execute AFTER triggered-tx processing.
        should_finalize_contract: Whether handle() should do contract state
            finalization + triggered transaction processing.

    Note: Contract state update, _get_messages_data, and _emit_messages stay
    in handle() because they involve DB reads (snapshot lookup, nonce, etc.).
    """
    pre_effects: list[Effect] = []
    post_effects: list[Effect] = []

    # Record timestamp
    pre_effects.append(AddTimestampEffect(tx_hash=tx_hash, state_name="FINALIZED"))

    should_finalize_contract = tx_status_accepted and execution_result_success

    if not should_finalize_contract:
        # Emit rollup event with empty messages
        pre_effects.append(
            EmitRollupEventEffect(
                event_name="emitTransactionFinalized",
                account=leader_node_config,
                tx_hash=tx_hash,
                extra_args=([],),
            )
        )

    # Status update always happens last
    post_effects.append(StatusUpdateEffect(tx_hash=tx_hash, new_status="FINALIZED"))

    return pre_effects, post_effects, should_finalize_contract


# ── RevealingState ────────────────────────────────────────────────


def merge_appeal_validators(
    existing_votes: dict,
    current_votes: dict,
    existing_validators: list,
    current_validation_results: list,
    appeal_failed: int,
) -> tuple[dict, list]:
    """Pure merge of appeal vote/validator data.

    Returns:
        (merged_votes, merged_validators)
    """
    merged_votes = existing_votes | current_votes

    if appeal_failed == 0:
        merged_validators = existing_validators + current_validation_results
    elif appeal_failed == 1:
        n = (len(existing_validators) - 1) // 2
        merged_validators = existing_validators[: n - 1] + current_validation_results
    else:
        n = len(current_validation_results) - (len(existing_validators) + 1)
        merged_validators = existing_validators[: n - 1] + current_validation_results

    return merged_votes, merged_validators


def decide_revealing(
    tx_hash: str,
    consensus_result: ConsensusResult,
    appealed: bool,
    appeal_validators_timeout: bool,
    appeal_undetermined: bool,
    rotation_count: int,
    config_rotation_rounds: int,
    vote_reveal_entries: list[tuple[dict, int]],
    consensus_data_dict: dict,
    leader_receipt: list,
    validation_results: list,
) -> tuple[str | ConsensusRound, list[Effect]]:
    """Decide outcome after votes are tallied in RevealingState.

    Args:
        vote_reveal_entries: [(node_config, chain_vote_int)] for vote-revealed events.
        consensus_data_dict: Serialized consensus data for SetTransactionResultEffect.
        leader_receipt: Full leader receipt list for UpdateConsensusHistoryEffect.
        validation_results: Post-split validation results for history updates.

    Returns:
        (next_state, effects)

        next_state is one of:
        - "accepted": transition to AcceptedState
        - "validators_timeout": transition to ValidatorsTimeoutState
        - "undetermined": transition to UndeterminedState
        - "rotate": leader rotation needed (handle() does the impure add_new_validator)
        - ConsensusRound.VALIDATOR_APPEAL_SUCCESSFUL: appeal succeeded

    Note:
        handle() is responsible for:
        - Clearing appeal_leader_timeout before undetermined/rotate (DB + context mutation)
        - The impure add_new_validator call for rotation
        - Emitting "emitTransactionLeaderRotated" (needs new leader address)
    """
    effects: list[Effect] = []

    # ── Always-emitted effects ──
    effects.append(AddTimestampEffect(tx_hash=tx_hash, state_name="REVEALING"))
    effects.append(StatusUpdateEffect(tx_hash=tx_hash, new_status="REVEALING"))

    # Vote-revealed rollup events
    num_entries = len(vote_reveal_entries)
    for i, (node_config, chain_vote_int) in enumerate(vote_reveal_entries):
        is_last = i == num_entries - 1
        effects.append(
            EmitRollupEventEffect(
                event_name="emitVoteRevealed",
                account=node_config,
                tx_hash=tx_hash,
                extra_args=(
                    node_config["address"],
                    chain_vote_int,
                    is_last,
                    int(consensus_result) if is_last else int(ConsensusResult.IDLE),
                ),
            )
        )

    effects.append(SetTimestampLastVoteEffect(tx_hash=tx_hash))

    # ── Path-specific decision ──
    if appealed or appeal_validators_timeout:
        if appealed and consensus_result == ConsensusResult.MAJORITY_AGREE:
            return "accepted", effects

        elif appeal_validators_timeout and consensus_result == ConsensusResult.TIMEOUT:
            return "validators_timeout", effects

        else:
            # Appeal successful: reset state
            effects.append(
                SetTransactionResultEffect(
                    tx_hash=tx_hash, consensus_data_dict=consensus_data_dict
                )
            )
            effects.append(SetAppealFailedEffect(tx_hash=tx_hash, count=0))

            consensus_round = (
                ConsensusRound.VALIDATOR_APPEAL_SUCCESSFUL
                if appealed
                else ConsensusRound.VALIDATOR_TIMEOUT_APPEAL_SUCCESSFUL
            )
            effects.append(
                UpdateConsensusHistoryEffect(
                    tx_hash=tx_hash,
                    consensus_round=consensus_round,
                    leader_receipt=None,
                    validation_results=validation_results,
                )
            )
            effects.append(ResetAppealProcessingTimeEffect(tx_hash=tx_hash))
            effects.append(SetTimestampAppealEffect(tx_hash=tx_hash, value=None))

            return ConsensusRound.VALIDATOR_APPEAL_SUCCESSFUL, effects

    else:
        # Not appealed
        if consensus_result == ConsensusResult.MAJORITY_AGREE:
            return "accepted", effects

        elif consensus_result == ConsensusResult.TIMEOUT:
            return "validators_timeout", effects

        elif consensus_result in (
            ConsensusResult.MAJORITY_DISAGREE,
            ConsensusResult.NO_MAJORITY,
        ):
            if rotation_count >= config_rotation_rounds:
                return "undetermined", effects

            else:
                # Rotation needed — rotation-success effects
                effects.append(IncreaseRotationCountEffect(tx_hash=tx_hash))
                effects.append(
                    SendMessageEffect(
                        event_name="consensus_event",
                        event_type="info",
                        event_scope="Consensus",
                        message="Majority disagreement, rotating the leader",
                        data={"transaction_hash": tx_hash},
                        tx_hash=tx_hash,
                    )
                )

                consensus_round = (
                    ConsensusRound.LEADER_ROTATION_APPEAL
                    if appeal_undetermined
                    else ConsensusRound.LEADER_ROTATION
                )
                effects.append(
                    UpdateConsensusHistoryEffect(
                        tx_hash=tx_hash,
                        consensus_round=consensus_round,
                        leader_receipt=leader_receipt,
                        validation_results=validation_results,
                    )
                )

                return "rotate", effects

        else:
            raise ValueError("Invalid consensus result")


# ── PendingState ──────────────────────────────────────────────────


def decide_pending_pre(
    tx_hash: str,
    appeal_leader_timeout: bool,
    appeal_undetermined: bool,
) -> list[Effect]:
    """Return the always-emitted effects for PendingState entry.

    Returns:
        effects: [AddTimestamp, ResetRotationCount, optional SendMessage]
    """
    from backend.consensus.effects import ResetRotationCountEffect

    effects: list[Effect] = []
    effects.append(AddTimestampEffect(tx_hash=tx_hash, state_name="PENDING"))
    effects.append(ResetRotationCountEffect(tx_hash=tx_hash))

    return effects


def decide_pending_activate(
    appeal_undetermined: bool,
    appeal_leader_timeout: bool,
) -> bool:
    """Determine whether the transaction should be activated in ProposingState."""
    return not (appeal_undetermined or appeal_leader_timeout)


# ── ProposingState ────────────────────────────────────────────────


def prepare_proposing(
    tx_hash: str,
    activate: bool,
    leader: dict,
    remaining_validators: list[dict],
) -> list[Effect]:
    """Return pre-execution effects for ProposingState.

    Returns:
        effects: [AddTimestamp, StatusUpdate, optional EmitRollupEvent(activated)]
    """
    effects: list[Effect] = []
    effects.append(AddTimestampEffect(tx_hash=tx_hash, state_name="PROPOSING"))
    effects.append(StatusUpdateEffect(tx_hash=tx_hash, new_status="PROPOSING"))

    if activate:
        effects.append(
            EmitRollupEventEffect(
                event_name="emitTransactionActivated",
                account=leader,
                tx_hash=tx_hash,
                extra_args=(
                    leader["address"],
                    [leader["address"]] + [v["address"] for v in remaining_validators],
                ),
            )
        )

    return effects


def decide_post_proposal(
    tx_hash: str,
    leader_receipt_result: bytes,
    leader_receipt_timed_out: bool,
    execution_mode_leader_only: bool,
    appeal_leader_timeout: bool,
    leader_address: str,
    leader: dict,
    remaining_validators: list[dict],
    consensus_data_dict: dict,
) -> tuple[str, list[Effect]]:
    """Decide what happens after leader execution in ProposingState.

    Args:
        leader_receipt_timed_out: True if leader result is VM_ERROR + timeout.
        execution_mode_leader_only: True if execution mode is LEADER_ONLY.

    Returns:
        (next_state, effects) where next_state is:
        - "leader_timeout": leader timed out
        - "accepted_leader_only": LEADER_ONLY mode, skip validation
        - "committing": normal flow to CommittingState
    """
    effects: list[Effect] = []

    # Store initial result
    effects.append(
        SetTransactionResultEffect(
            tx_hash=tx_hash, consensus_data_dict=consensus_data_dict
        )
    )

    if leader_receipt_timed_out:
        return "leader_timeout", effects

    # Successful leader timeout appeal effects
    if appeal_leader_timeout:
        effects.append(SetTimestampAwaitingFinalizationEffect(tx_hash=tx_hash))
        effects.append(ResetAppealProcessingTimeEffect(tx_hash=tx_hash))
        effects.append(SetTimestampAppealEffect(tx_hash=tx_hash, value=None))

    # Clear leader timeout validators
    effects.append(SetLeaderTimeoutValidatorsEffect(tx_hash=tx_hash, validators=[]))

    # Receipt proposed event
    effects.append(
        EmitRollupEventEffect(
            event_name="emitTransactionReceiptProposed",
            account=leader,
            tx_hash=tx_hash,
        )
    )

    if execution_mode_leader_only:
        # LEADER_ONLY: set leader vote as AGREE, store result
        effects.append(
            SetTransactionResultEffect(
                tx_hash=tx_hash, consensus_data_dict=consensus_data_dict
            )
        )
        return "accepted_leader_only", effects

    return "committing", effects


# ── CommittingState ───────────────────────────────────────────────


def prepare_committing(
    tx_hash: str,
) -> list[Effect]:
    """Return pre-execution effects for CommittingState."""
    effects: list[Effect] = []
    effects.append(AddTimestampEffect(tx_hash=tx_hash, state_name="COMMITTING"))
    effects.append(StatusUpdateEffect(tx_hash=tx_hash, new_status="COMMITTING"))
    return effects


def decide_post_committing(
    tx_hash: str,
    validators_to_emit: list[dict],
) -> list[Effect]:
    """Return post-execution effects for CommittingState.

    Generates vote-committed rollup events for each validator.
    """
    effects: list[Effect] = []
    num_validators = len(validators_to_emit)
    for i, validator in enumerate(validators_to_emit):
        effects.append(
            EmitRollupEventEffect(
                event_name="emitVoteCommitted",
                account=validator,
                tx_hash=tx_hash,
                extra_args=(
                    validator["address"],
                    i == num_validators - 1,
                ),
            )
        )
    effects.append(SetTimestampLastVoteEffect(tx_hash=tx_hash))
    return effects


# ── ConsensusAlgorithm caller helpers ────────────────────────────


def should_rollback_after_accepted(
    consensus_history: dict | None,
) -> bool:
    """Determine whether to rollback after a transaction is accepted.

    Returns True if the last consensus round was
    VALIDATOR_TIMEOUT_APPEAL_SUCCESSFUL.
    """
    if consensus_history is None:
        return False
    results = consensus_history.get("consensus_results")
    if not results:
        return False
    last_round = results[-1].get("consensus_round")
    return last_round == ConsensusRound.VALIDATOR_TIMEOUT_APPEAL_SUCCESSFUL.value


def has_appeal_capacity(
    num_involved_validators: int,
    num_used_leader_addresses: int,
    num_total_validators: int,
) -> bool:
    """Check whether there are enough validators to process an appeal.

    Returns True if new validators can be added.
    """
    return num_involved_validators + num_used_leader_addresses < num_total_validators
