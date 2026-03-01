"""Exhaustive tests for AcceptedState and FinalizingState decision functions.

These tests call pure functions directly — no mocks, no async, no context objects.
Each test verifies the exact list of effects returned for a specific branch.
"""

import pytest
from backend.consensus.decisions import decide_accepted, decide_finalizing
from backend.consensus.effects import (
    AddTimestampEffect,
    StatusUpdateEffect,
    SendMessageEffect,
    SetTransactionResultEffect,
    UpdateConsensusHistoryEffect,
    SetTimestampAwaitingFinalizationEffect,
    SetAppealUndeterminedEffect,
    SetAppealLeaderTimeoutEffect,
    SetAppealEffect,
    SetAppealFailedEffect,
    SetAppealProcessingTimeEffect,
    ResetAppealProcessingTimeEffect,
    SetTimestampAppealEffect,
    SetContractSnapshotEffect,
    RegisterContractEffect,
    UpdateContractStateEffect,
    EmitRollupEventEffect,
)
from backend.consensus.types import ConsensusRound


# ── Helpers ────────────────────────────────────────────────────────


def _effect_types(effects):
    """Return a list of effect class names for quick structural checks."""
    return [type(e).__name__ for e in effects]


def _find_effect(effects, effect_type):
    """Find the first effect of a given type."""
    for e in effects:
        if isinstance(e, effect_type):
            return e
    return None


def _find_effects(effects, effect_type):
    """Find all effects of a given type."""
    return [e for e in effects if isinstance(e, effect_type)]


# ── Common kwargs builders ────────────────────────────────────────


def _base_accepted_kwargs(**overrides):
    """Build default kwargs for decide_accepted, override as needed."""
    defaults = dict(
        tx_hash="0xabc",
        appeal_undetermined=False,
        appealed=False,
        appeal_leader_timeout=False,
        appeal_failed=0,
        consensus_data_dict={"votes": {}, "leader_receipt": None},
        leader_receipt_list=["receipt_obj"],
        validation_results=[{"validator": "0xval1", "vote": "agree"}],
        redacted_consensus_data={"redacted": True},
        has_contract_snapshot=False,
        contract_snapshot_dict={"state": {}},
        execution_result_success=True,
        tx_type_deploy=False,
        accepted_contract_state={"slot1": b"data"},
        contract_address=None,
        contract_code=None,
        code_slot_b64=None,
        to_address="0xcontract",
        leader_node_config={"address": "0xleader"},
    )
    defaults.update(overrides)
    return defaults


def _base_finalizing_kwargs(**overrides):
    """Build default kwargs for decide_finalizing, override as needed."""
    defaults = dict(
        tx_hash="0xabc",
        tx_status_accepted=True,
        execution_result_success=True,
        leader_node_config={"address": "0xleader"},
    )
    defaults.update(overrides)
    return defaults


# ═══════════════════════════════════════════════════════════════════
# decide_accepted
# ═══════════════════════════════════════════════════════════════════


class TestDecideAcceptedNormal:
    """Normal acceptance path: not appealed, not appeal_undetermined."""

    def test_consensus_round_is_accepted(self):
        pre, post, cr, rv = decide_accepted(**_base_accepted_kwargs())
        assert cr == ConsensusRound.ACCEPTED

    def test_return_value_is_accepted(self):
        _, _, _, rv = decide_accepted(**_base_accepted_kwargs())
        assert rv == ConsensusRound.ACCEPTED

    def test_timestamp_effect_first(self):
        pre, _, _, _ = decide_accepted(**_base_accepted_kwargs())
        assert isinstance(pre[0], AddTimestampEffect)
        assert pre[0].state_name == "ACCEPTED"

    def test_sets_timestamp_awaiting_finalization(self):
        pre, _, _, _ = decide_accepted(**_base_accepted_kwargs())
        assert _find_effect(pre, SetTimestampAwaitingFinalizationEffect) is not None

    def test_sets_transaction_result(self):
        data = {"votes": {"v1": "agree"}}
        pre, _, _, _ = decide_accepted(
            **_base_accepted_kwargs(consensus_data_dict=data)
        )
        e = _find_effect(pre, SetTransactionResultEffect)
        assert e is not None
        assert e.consensus_data_dict == data

    def test_updates_consensus_history_with_leader_receipt(self):
        pre, _, _, _ = decide_accepted(**_base_accepted_kwargs())
        e = _find_effect(pre, UpdateConsensusHistoryEffect)
        assert e is not None
        assert e.leader_receipt == ["receipt_obj"]
        assert e.new_status == "ACCEPTED"

    def test_sends_consensus_reached_message(self):
        pre, _, _, _ = decide_accepted(**_base_accepted_kwargs())
        e = _find_effect(pre, SendMessageEffect)
        assert e is not None
        assert e.message == "Reached consensus"
        assert e.event_type == "success"

    def test_saves_contract_snapshot_when_missing(self):
        pre, _, _, _ = decide_accepted(
            **_base_accepted_kwargs(
                has_contract_snapshot=False, contract_snapshot_dict={"s": 1}
            )
        )
        e = _find_effect(pre, SetContractSnapshotEffect)
        assert e is not None
        assert e.snapshot_dict == {"s": 1}

    def test_no_contract_snapshot_when_already_saved(self):
        pre, _, _, _ = decide_accepted(
            **_base_accepted_kwargs(has_contract_snapshot=True)
        )
        assert _find_effect(pre, SetContractSnapshotEffect) is None

    def test_post_effects_contain_status_update(self):
        _, post, _, _ = decide_accepted(**_base_accepted_kwargs())
        e = _find_effect(post, StatusUpdateEffect)
        assert e is not None
        assert e.new_status == "ACCEPTED"
        assert e.update_current_status_changes is False


class TestDecideAcceptedDeploy:
    """Normal acceptance path for deploy transactions."""

    def _deploy_kwargs(self, **overrides):
        base = _base_accepted_kwargs(
            tx_type_deploy=True,
            contract_address="0xnewcontract",
            contract_code="print('hello')",
            code_slot_b64="Y29kZQ==",
            accepted_contract_state={"Y29kZQ==": b"code_data", "other": b"val"},
        )
        base.update(overrides)
        return base

    def test_registers_new_contract(self):
        pre, _, _, _ = decide_accepted(**self._deploy_kwargs())
        e = _find_effect(pre, RegisterContractEffect)
        assert e is not None
        assert e.contract_data["id"] == "0xnewcontract"
        assert "code" in e.contract_data["data"]

    def test_contract_data_has_correct_state_structure(self):
        pre, _, _, _ = decide_accepted(**self._deploy_kwargs())
        e = _find_effect(pre, RegisterContractEffect)
        state = e.contract_data["data"]["state"]
        assert "accepted" in state
        assert "finalized" in state
        assert state["accepted"] == {"Y29kZQ==": b"code_data", "other": b"val"}
        # Finalized only has the code slot
        assert "Y29kZQ==" in state["finalized"]

    def test_sends_deployed_contract_message(self):
        pre, _, _, _ = decide_accepted(**self._deploy_kwargs())
        msgs = _find_effects(pre, SendMessageEffect)
        deploy_msg = [m for m in msgs if m.event_name == "deployed_contract"]
        assert len(deploy_msg) == 1
        assert deploy_msg[0].event_scope == "GenVM"

    def test_no_update_contract_state_effect(self):
        pre, _, _, _ = decide_accepted(**self._deploy_kwargs())
        assert _find_effect(pre, UpdateContractStateEffect) is None


class TestDecideAcceptedRunContract:
    """Normal acceptance path for run-contract (non-deploy) transactions."""

    def test_updates_contract_state(self):
        pre, _, _, _ = decide_accepted(
            **_base_accepted_kwargs(
                execution_result_success=True,
                accepted_contract_state={"key": "val"},
            )
        )
        e = _find_effect(pre, UpdateContractStateEffect)
        assert e is not None
        assert e.address == "0xcontract"
        assert e.accepted_state == {"key": "val"}

    def test_no_register_contract_effect(self):
        pre, _, _, _ = decide_accepted(**_base_accepted_kwargs())
        assert _find_effect(pre, RegisterContractEffect) is None


class TestDecideAcceptedExecutionError:
    """Accepted but execution failed — no contract write effects."""

    def test_no_contract_write_on_error(self):
        pre, _, _, _ = decide_accepted(
            **_base_accepted_kwargs(execution_result_success=False)
        )
        assert _find_effect(pre, RegisterContractEffect) is None
        assert _find_effect(pre, UpdateContractStateEffect) is None

    def test_still_records_timestamp_and_result(self):
        pre, _, _, _ = decide_accepted(
            **_base_accepted_kwargs(execution_result_success=False)
        )
        assert _find_effect(pre, AddTimestampEffect) is not None
        assert _find_effect(pre, SetTransactionResultEffect) is not None


class TestDecideAcceptedAppealUndetermined:
    """appeal_undetermined=True path: leader appeal successful."""

    def _kwargs(self, **overrides):
        base = _base_accepted_kwargs(appeal_undetermined=True)
        base.update(overrides)
        return base

    def test_consensus_round(self):
        _, _, cr, _ = decide_accepted(**self._kwargs())
        assert cr == ConsensusRound.LEADER_APPEAL_SUCCESSFUL

    def test_return_value(self):
        _, _, _, rv = decide_accepted(**self._kwargs())
        assert rv == ConsensusRound.LEADER_APPEAL_SUCCESSFUL

    def test_resets_appeal_failed_to_zero(self):
        pre, _, _, _ = decide_accepted(**self._kwargs(appeal_failed=3))
        e = _find_effect(pre, SetAppealFailedEffect)
        assert e is not None
        assert e.count == 0

    def test_resets_appeal_processing_time(self):
        pre, _, _, _ = decide_accepted(**self._kwargs())
        assert _find_effect(pre, ResetAppealProcessingTimeEffect) is not None

    def test_clears_timestamp_appeal(self):
        pre, _, _, _ = decide_accepted(**self._kwargs())
        e = _find_effect(pre, SetTimestampAppealEffect)
        assert e is not None
        assert e.value is None

    def test_sets_timestamp_awaiting_finalization(self):
        pre, _, _, _ = decide_accepted(**self._kwargs())
        assert _find_effect(pre, SetTimestampAwaitingFinalizationEffect) is not None

    def test_post_effects_clear_appeal_undetermined(self):
        _, post, _, _ = decide_accepted(**self._kwargs())
        e = _find_effect(post, SetAppealUndeterminedEffect)
        assert e is not None
        assert e.value is False

    def test_consensus_history_uses_leader_receipt(self):
        pre, _, _, _ = decide_accepted(**self._kwargs())
        e = _find_effect(pre, UpdateConsensusHistoryEffect)
        assert e.leader_receipt == ["receipt_obj"]


class TestDecideAcceptedAppealed:
    """appealed=True path: validator appeal failed."""

    def _kwargs(self, **overrides):
        base = _base_accepted_kwargs(appealed=True, appeal_failed=2)
        base.update(overrides)
        return base

    def test_consensus_round(self):
        _, _, cr, _ = decide_accepted(**self._kwargs())
        assert cr == ConsensusRound.VALIDATOR_APPEAL_FAILED

    def test_return_value_is_none(self):
        _, _, _, rv = decide_accepted(**self._kwargs())
        assert rv is None

    def test_clears_appeal(self):
        pre, _, _, _ = decide_accepted(**self._kwargs())
        e = _find_effect(pre, SetAppealEffect)
        assert e is not None
        assert e.appealed is False

    def test_increments_appeal_failed(self):
        pre, _, _, _ = decide_accepted(**self._kwargs(appeal_failed=2))
        e = _find_effect(pre, SetAppealFailedEffect)
        assert e is not None
        assert e.count == 3

    def test_sets_appeal_processing_time(self):
        pre, _, _, _ = decide_accepted(**self._kwargs())
        assert _find_effect(pre, SetAppealProcessingTimeEffect) is not None

    def test_consensus_history_has_no_leader_receipt(self):
        pre, _, _, _ = decide_accepted(**self._kwargs())
        e = _find_effect(pre, UpdateConsensusHistoryEffect)
        assert e.leader_receipt is None

    def test_emits_rollup_with_empty_messages(self):
        pre, _, _, _ = decide_accepted(**self._kwargs())
        e = _find_effect(pre, EmitRollupEventEffect)
        assert e is not None
        assert e.event_name == "emitTransactionAccepted"
        assert e.extra_args == ([],)

    def test_no_contract_write_effects(self):
        pre, _, _, _ = decide_accepted(**self._kwargs())
        assert _find_effect(pre, RegisterContractEffect) is None
        assert _find_effect(pre, UpdateContractStateEffect) is None
        assert _find_effect(pre, SetContractSnapshotEffect) is None

    def test_no_timestamp_awaiting_finalization(self):
        pre, _, _, _ = decide_accepted(**self._kwargs())
        assert _find_effect(pre, SetTimestampAwaitingFinalizationEffect) is None


class TestDecideAcceptedAppealLeaderTimeout:
    """appeal_leader_timeout=True path (with appealed=True or False)."""

    def test_return_value_leader_timeout_appeal_successful(self):
        _, _, _, rv = decide_accepted(
            **_base_accepted_kwargs(appeal_leader_timeout=True)
        )
        assert rv == ConsensusRound.LEADER_TIMEOUT_APPEAL_SUCCESSFUL

    def test_post_effects_clear_appeal_leader_timeout(self):
        _, post, _, _ = decide_accepted(
            **_base_accepted_kwargs(appeal_leader_timeout=True)
        )
        e = _find_effect(post, SetAppealLeaderTimeoutEffect)
        assert e is not None
        assert e.value is False

    def test_appealed_takes_precedence_for_consensus_round(self):
        """When both appealed and appeal_leader_timeout, appealed determines round."""
        _, _, cr, rv = decide_accepted(
            **_base_accepted_kwargs(
                appealed=True, appeal_leader_timeout=True, appeal_failed=1
            )
        )
        assert cr == ConsensusRound.VALIDATOR_APPEAL_FAILED
        # But return_value is overridden by appeal_leader_timeout check
        assert rv == ConsensusRound.LEADER_TIMEOUT_APPEAL_SUCCESSFUL


# ═══════════════════════════════════════════════════════════════════
# decide_finalizing
# ═══════════════════════════════════════════════════════════════════


class TestDecideFinalizingAcceptedSuccess:
    """Accepted + execution success: should_finalize_contract=True."""

    def test_should_finalize_contract(self):
        _, _, should = decide_finalizing(**_base_finalizing_kwargs())
        assert should is True

    def test_pre_effects_only_timestamp(self):
        pre, _, _ = decide_finalizing(**_base_finalizing_kwargs())
        assert len(pre) == 1
        assert isinstance(pre[0], AddTimestampEffect)
        assert pre[0].state_name == "FINALIZED"

    def test_no_rollup_in_pre_effects(self):
        pre, _, _ = decide_finalizing(**_base_finalizing_kwargs())
        assert _find_effect(pre, EmitRollupEventEffect) is None

    def test_post_effects_have_status_update(self):
        _, post, _ = decide_finalizing(**_base_finalizing_kwargs())
        assert len(post) == 1
        e = post[0]
        assert isinstance(e, StatusUpdateEffect)
        assert e.new_status == "FINALIZED"
        assert e.update_current_status_changes is True


class TestDecideFinalizingAcceptedError:
    """Accepted + execution error: should_finalize_contract=False."""

    def test_should_not_finalize(self):
        _, _, should = decide_finalizing(
            **_base_finalizing_kwargs(execution_result_success=False)
        )
        assert should is False

    def test_emits_rollup_with_empty_messages(self):
        pre, _, _ = decide_finalizing(
            **_base_finalizing_kwargs(execution_result_success=False)
        )
        e = _find_effect(pre, EmitRollupEventEffect)
        assert e is not None
        assert e.event_name == "emitTransactionFinalized"
        assert e.extra_args == ([],)
        assert e.account == {"address": "0xleader"}

    def test_still_has_timestamp(self):
        pre, _, _ = decide_finalizing(
            **_base_finalizing_kwargs(execution_result_success=False)
        )
        assert isinstance(pre[0], AddTimestampEffect)


class TestDecideFinalizingNotAccepted:
    """Not accepted (e.g., undetermined) — should_finalize_contract=False."""

    def test_should_not_finalize(self):
        _, _, should = decide_finalizing(
            **_base_finalizing_kwargs(tx_status_accepted=False)
        )
        assert should is False

    def test_emits_rollup_with_empty_messages(self):
        pre, _, _ = decide_finalizing(
            **_base_finalizing_kwargs(tx_status_accepted=False)
        )
        e = _find_effect(pre, EmitRollupEventEffect)
        assert e is not None
        assert e.event_name == "emitTransactionFinalized"

    def test_post_effects_always_have_status_update(self):
        _, post, _ = decide_finalizing(
            **_base_finalizing_kwargs(tx_status_accepted=False)
        )
        e = _find_effect(post, StatusUpdateEffect)
        assert e is not None
        assert e.new_status == "FINALIZED"


class TestDecideFinalizingNotAcceptedWithSuccess:
    """Not accepted but execution_result_success=True — still should not finalize."""

    def test_should_not_finalize(self):
        _, _, should = decide_finalizing(
            **_base_finalizing_kwargs(
                tx_status_accepted=False, execution_result_success=True
            )
        )
        assert should is False
