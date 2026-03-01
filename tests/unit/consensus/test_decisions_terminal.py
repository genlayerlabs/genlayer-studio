"""Exhaustive tests for terminal state decision functions.

These tests call pure functions directly — no mocks, no async, no context objects.
Each test verifies the exact list of effects returned for a specific branch.
"""

import pytest
from backend.consensus.decisions import (
    decide_undetermined,
    decide_leader_timeout,
    decide_validators_timeout,
)
from backend.consensus.effects import (
    AddTimestampEffect,
    StatusUpdateEffect,
    SendMessageEffect,
    SetTransactionResultEffect,
    UpdateConsensusHistoryEffect,
    SetTimestampAwaitingFinalizationEffect,
    SetAppealUndeterminedEffect,
    SetAppealFailedEffect,
    SetAppealProcessingTimeEffect,
    ResetAppealProcessingTimeEffect,
    SetTimestampAppealEffect,
    SetContractSnapshotEffect,
    SetLeaderTimeoutValidatorsEffect,
    SetAppealLeaderTimeoutEffect,
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


# ═══════════════════════════════════════════════════════════════════
# UndeterminedState
# ═══════════════════════════════════════════════════════════════════


class TestDecideUndetermined:
    """Two branches: normal undetermined vs appeal_undetermined (appeal failed)."""

    def _base_kwargs(self, **overrides):
        defaults = dict(
            tx_hash="0xabc",
            appeal_undetermined=False,
            appeal_failed=0,
            has_contract_snapshot=False,
            contract_snapshot_dict={"state": {}},
            consensus_data_dict={"votes": {}, "leader_receipt": None, "validators": []},
            timestamp_appeal=None,
            leader_receipt=None,
            validators=[],
            redacted_consensus_data={"votes": {}},
        )
        defaults.update(overrides)
        return defaults

    def test_normal_undetermined_round(self):
        effects, round_ = decide_undetermined(**self._base_kwargs())
        assert round_ == ConsensusRound.UNDETERMINED

    def test_normal_undetermined_has_timestamp(self):
        effects, _ = decide_undetermined(**self._base_kwargs())
        assert effects[0] == AddTimestampEffect(
            tx_hash="0xabc", state_name="UNDETERMINED"
        )

    def test_normal_undetermined_sends_failure_message(self):
        effects, _ = decide_undetermined(**self._base_kwargs())
        msg = _find_effect(effects, SendMessageEffect)
        assert msg is not None
        assert msg.event_type == "error"
        assert "Failed to reach consensus" in msg.message

    def test_normal_undetermined_sets_awaiting_finalization(self):
        effects, _ = decide_undetermined(**self._base_kwargs())
        assert _find_effect(effects, SetTimestampAwaitingFinalizationEffect) is not None

    def test_normal_undetermined_no_appeal_effects(self):
        effects, _ = decide_undetermined(**self._base_kwargs())
        assert _find_effect(effects, SetAppealUndeterminedEffect) is None
        assert _find_effect(effects, SetAppealFailedEffect) is None

    def test_normal_undetermined_saves_contract_snapshot(self):
        effects, _ = decide_undetermined(**self._base_kwargs())
        snap = _find_effect(effects, SetContractSnapshotEffect)
        assert snap is not None
        assert snap.snapshot_dict == {"state": {}}

    def test_normal_undetermined_skips_snapshot_if_already_saved(self):
        effects, _ = decide_undetermined(
            **self._base_kwargs(has_contract_snapshot=True)
        )
        assert _find_effect(effects, SetContractSnapshotEffect) is None

    def test_normal_undetermined_sets_result(self):
        data = {"votes": {"0x1": "AGREE"}, "leader_receipt": None, "validators": []}
        effects, _ = decide_undetermined(**self._base_kwargs(consensus_data_dict=data))
        result = _find_effect(effects, SetTransactionResultEffect)
        assert result is not None
        assert result.consensus_data_dict == data

    def test_normal_undetermined_no_appeal_processing_time(self):
        effects, _ = decide_undetermined(**self._base_kwargs(timestamp_appeal=None))
        assert _find_effect(effects, SetAppealProcessingTimeEffect) is None

    def test_normal_undetermined_with_timestamp_appeal_sets_processing_time(self):
        effects, _ = decide_undetermined(**self._base_kwargs(timestamp_appeal=12345))
        assert _find_effect(effects, SetAppealProcessingTimeEffect) is not None

    def test_normal_undetermined_updates_consensus_history(self):
        effects, _ = decide_undetermined(**self._base_kwargs())
        hist = _find_effect(effects, UpdateConsensusHistoryEffect)
        assert hist is not None
        assert hist.consensus_round == ConsensusRound.UNDETERMINED
        assert hist.new_status == "UNDETERMINED"

    def test_normal_undetermined_updates_status(self):
        effects, _ = decide_undetermined(**self._base_kwargs())
        status = _find_effect(effects, StatusUpdateEffect)
        assert status is not None
        assert status.new_status == "UNDETERMINED"
        assert status.update_current_status_changes is False

    # ── appeal_undetermined path ──

    def test_appeal_undetermined_round(self):
        effects, round_ = decide_undetermined(
            **self._base_kwargs(appeal_undetermined=True, appeal_failed=1)
        )
        assert round_ == ConsensusRound.LEADER_APPEAL_FAILED

    def test_appeal_undetermined_no_awaiting_finalization(self):
        effects, _ = decide_undetermined(**self._base_kwargs(appeal_undetermined=True))
        assert _find_effect(effects, SetTimestampAwaitingFinalizationEffect) is None

    def test_appeal_undetermined_clears_appeal_flag(self):
        effects, _ = decide_undetermined(**self._base_kwargs(appeal_undetermined=True))
        appeal = _find_effect(effects, SetAppealUndeterminedEffect)
        assert appeal is not None
        assert appeal.value is False

    def test_appeal_undetermined_increments_appeal_failed(self):
        effects, _ = decide_undetermined(
            **self._base_kwargs(appeal_undetermined=True, appeal_failed=2)
        )
        failed = _find_effect(effects, SetAppealFailedEffect)
        assert failed is not None
        assert failed.count == 3

    def test_appeal_undetermined_consensus_history_round(self):
        effects, _ = decide_undetermined(**self._base_kwargs(appeal_undetermined=True))
        hist = _find_effect(effects, UpdateConsensusHistoryEffect)
        assert hist.consensus_round == ConsensusRound.LEADER_APPEAL_FAILED


# ═══════════════════════════════════════════════════════════════════
# LeaderTimeoutState
# ═══════════════════════════════════════════════════════════════════


class TestDecideLeaderTimeout:
    """Three branches: normal, appeal_undetermined, appeal_leader_timeout."""

    def _base_kwargs(self, **overrides):
        defaults = dict(
            tx_hash="0xabc",
            appeal_undetermined=False,
            appeal_leader_timeout=False,
            has_contract_snapshot=False,
            contract_snapshot_dict={"state": {}},
            leader_receipt=[{"node_config": {"address": "0xleader"}}],
            remaining_validators=[{"address": "0xval1"}],
            leader={"address": "0xleader"},
        )
        defaults.update(overrides)
        return defaults

    # ── normal path ──

    def test_normal_round(self):
        effects, round_ = decide_leader_timeout(**self._base_kwargs())
        assert round_ == ConsensusRound.LEADER_TIMEOUT

    def test_normal_has_timestamp(self):
        effects, _ = decide_leader_timeout(**self._base_kwargs())
        assert effects[0] == AddTimestampEffect(
            tx_hash="0xabc", state_name="LEADER_TIMEOUT"
        )

    def test_normal_saves_contract_snapshot(self):
        effects, _ = decide_leader_timeout(**self._base_kwargs())
        assert _find_effect(effects, SetContractSnapshotEffect) is not None

    def test_normal_skips_snapshot_if_present(self):
        effects, _ = decide_leader_timeout(
            **self._base_kwargs(has_contract_snapshot=True)
        )
        assert _find_effect(effects, SetContractSnapshotEffect) is None

    def test_normal_sets_awaiting_finalization(self):
        effects, _ = decide_leader_timeout(**self._base_kwargs())
        assert _find_effect(effects, SetTimestampAwaitingFinalizationEffect) is not None

    def test_normal_saves_leader_timeout_validators(self):
        validators = [{"address": "0xval1"}, {"address": "0xval2"}]
        effects, _ = decide_leader_timeout(
            **self._base_kwargs(remaining_validators=validators)
        )
        ltv = _find_effect(effects, SetLeaderTimeoutValidatorsEffect)
        assert ltv is not None
        assert ltv.validators == validators

    def test_normal_updates_consensus_history(self):
        effects, _ = decide_leader_timeout(**self._base_kwargs())
        hist = _find_effect(effects, UpdateConsensusHistoryEffect)
        assert hist.consensus_round == ConsensusRound.LEADER_TIMEOUT
        assert hist.validation_results == []
        assert hist.new_status == "LEADER_TIMEOUT"

    def test_normal_updates_status(self):
        effects, _ = decide_leader_timeout(**self._base_kwargs())
        status = _find_effect(effects, StatusUpdateEffect)
        assert status.new_status == "LEADER_TIMEOUT"
        assert status.update_current_status_changes is False

    def test_normal_emits_rollup_event(self):
        effects, _ = decide_leader_timeout(**self._base_kwargs())
        rollup = _find_effect(effects, EmitRollupEventEffect)
        assert rollup is not None
        assert rollup.event_name == "emitTransactionLeaderTimeout"
        assert rollup.account == {"address": "0xleader"}

    # ── appeal_undetermined path ──

    def test_appeal_undetermined_round(self):
        effects, round_ = decide_leader_timeout(
            **self._base_kwargs(appeal_undetermined=True)
        )
        assert round_ == ConsensusRound.LEADER_APPEAL_SUCCESSFUL

    def test_appeal_undetermined_sets_awaiting_finalization(self):
        effects, _ = decide_leader_timeout(
            **self._base_kwargs(appeal_undetermined=True)
        )
        assert _find_effect(effects, SetTimestampAwaitingFinalizationEffect) is not None

    def test_appeal_undetermined_resets_appeal_processing_time(self):
        effects, _ = decide_leader_timeout(
            **self._base_kwargs(appeal_undetermined=True)
        )
        assert _find_effect(effects, ResetAppealProcessingTimeEffect) is not None

    def test_appeal_undetermined_clears_timestamp_appeal(self):
        effects, _ = decide_leader_timeout(
            **self._base_kwargs(appeal_undetermined=True)
        )
        ts = _find_effect(effects, SetTimestampAppealEffect)
        assert ts is not None
        assert ts.value is None

    # ── appeal_leader_timeout path ──

    def test_appeal_leader_timeout_round(self):
        effects, round_ = decide_leader_timeout(
            **self._base_kwargs(appeal_leader_timeout=True)
        )
        assert round_ == ConsensusRound.LEADER_TIMEOUT_APPEAL_FAILED

    def test_appeal_leader_timeout_sets_processing_time(self):
        effects, _ = decide_leader_timeout(
            **self._base_kwargs(appeal_leader_timeout=True)
        )
        assert _find_effect(effects, SetAppealProcessingTimeEffect) is not None

    def test_appeal_leader_timeout_no_awaiting_finalization(self):
        effects, _ = decide_leader_timeout(
            **self._base_kwargs(appeal_leader_timeout=True)
        )
        assert _find_effect(effects, SetTimestampAwaitingFinalizationEffect) is None


# ═══════════════════════════════════════════════════════════════════
# ValidatorsTimeoutState
# ═══════════════════════════════════════════════════════════════════


class TestDecideValidatorsTimeout:
    """Three main branches plus appeal_leader_timeout cross-cut."""

    def _base_kwargs(self, **overrides):
        defaults = dict(
            tx_hash="0xabc",
            appeal_undetermined=False,
            appeal_validators_timeout=False,
            appeal_leader_timeout=False,
            appeal_failed=0,
            has_contract_snapshot=False,
            contract_snapshot_dict={"state": {}},
            consensus_data_dict={"votes": {}, "leader_receipt": None, "validators": []},
            leader_receipt=[{"node_config": {"address": "0xleader"}}],
            validation_results=["result1"],
        )
        defaults.update(overrides)
        return defaults

    # ── normal path ──

    def test_normal_round(self):
        effects, round_ = decide_validators_timeout(**self._base_kwargs())
        assert round_ == ConsensusRound.VALIDATORS_TIMEOUT

    def test_normal_has_timestamp(self):
        effects, _ = decide_validators_timeout(**self._base_kwargs())
        assert effects[0] == AddTimestampEffect(
            tx_hash="0xabc", state_name="VALIDATORS_TIMEOUT"
        )

    def test_normal_sets_awaiting_finalization(self):
        effects, _ = decide_validators_timeout(**self._base_kwargs())
        assert _find_effect(effects, SetTimestampAwaitingFinalizationEffect) is not None

    def test_normal_sets_result(self):
        effects, _ = decide_validators_timeout(**self._base_kwargs())
        assert _find_effect(effects, SetTransactionResultEffect) is not None

    def test_normal_updates_consensus_history_with_leader_receipt(self):
        leader = [{"node_config": {"address": "0xleader"}}]
        effects, _ = decide_validators_timeout(
            **self._base_kwargs(leader_receipt=leader)
        )
        hist = _find_effect(effects, UpdateConsensusHistoryEffect)
        assert hist.leader_receipt == leader
        assert hist.new_status == "VALIDATORS_TIMEOUT"

    def test_normal_updates_status(self):
        effects, _ = decide_validators_timeout(**self._base_kwargs())
        status = _find_effect(effects, StatusUpdateEffect)
        assert status.new_status == "VALIDATORS_TIMEOUT"

    def test_normal_saves_contract_snapshot(self):
        effects, _ = decide_validators_timeout(**self._base_kwargs())
        assert _find_effect(effects, SetContractSnapshotEffect) is not None

    def test_normal_skips_snapshot_if_present(self):
        effects, _ = decide_validators_timeout(
            **self._base_kwargs(has_contract_snapshot=True)
        )
        assert _find_effect(effects, SetContractSnapshotEffect) is None

    # ── appeal_undetermined path ──

    def test_appeal_undetermined_round(self):
        effects, round_ = decide_validators_timeout(
            **self._base_kwargs(appeal_undetermined=True)
        )
        assert round_ == ConsensusRound.LEADER_APPEAL_SUCCESSFUL

    def test_appeal_undetermined_sets_awaiting_finalization(self):
        effects, _ = decide_validators_timeout(
            **self._base_kwargs(appeal_undetermined=True)
        )
        assert _find_effect(effects, SetTimestampAwaitingFinalizationEffect) is not None

    def test_appeal_undetermined_resets_appeal_processing_time(self):
        effects, _ = decide_validators_timeout(
            **self._base_kwargs(appeal_undetermined=True)
        )
        assert _find_effect(effects, ResetAppealProcessingTimeEffect) is not None

    def test_appeal_undetermined_clears_timestamp_appeal(self):
        effects, _ = decide_validators_timeout(
            **self._base_kwargs(appeal_undetermined=True)
        )
        ts = _find_effect(effects, SetTimestampAppealEffect)
        assert ts is not None
        assert ts.value is None

    def test_appeal_undetermined_clears_appeal_flag(self):
        effects, _ = decide_validators_timeout(
            **self._base_kwargs(appeal_undetermined=True)
        )
        appeal = _find_effect(effects, SetAppealUndeterminedEffect)
        assert appeal is not None
        assert appeal.value is False

    # ── appeal_validators_timeout path ──

    def test_appeal_validators_timeout_round(self):
        effects, round_ = decide_validators_timeout(
            **self._base_kwargs(appeal_validators_timeout=True, appeal_failed=1)
        )
        assert round_ == ConsensusRound.VALIDATORS_TIMEOUT_APPEAL_FAILED

    def test_appeal_validators_timeout_sets_processing_time(self):
        effects, _ = decide_validators_timeout(
            **self._base_kwargs(appeal_validators_timeout=True)
        )
        assert _find_effect(effects, SetAppealProcessingTimeEffect) is not None

    def test_appeal_validators_timeout_increments_appeal_failed(self):
        effects, _ = decide_validators_timeout(
            **self._base_kwargs(appeal_validators_timeout=True, appeal_failed=2)
        )
        failed = _find_effect(effects, SetAppealFailedEffect)
        assert failed is not None
        assert failed.count == 3

    def test_appeal_validators_timeout_no_awaiting_finalization(self):
        effects, _ = decide_validators_timeout(
            **self._base_kwargs(appeal_validators_timeout=True)
        )
        assert _find_effect(effects, SetTimestampAwaitingFinalizationEffect) is None

    def test_appeal_validators_timeout_null_leader_receipt_in_history(self):
        effects, _ = decide_validators_timeout(
            **self._base_kwargs(appeal_validators_timeout=True)
        )
        hist = _find_effect(effects, UpdateConsensusHistoryEffect)
        assert hist.leader_receipt is None

    # ── appeal_leader_timeout cross-cut ──

    def test_appeal_leader_timeout_cleared(self):
        effects, _ = decide_validators_timeout(
            **self._base_kwargs(appeal_leader_timeout=True)
        )
        lt = _find_effect(effects, SetAppealLeaderTimeoutEffect)
        assert lt is not None
        assert lt.value is False

    def test_no_appeal_leader_timeout_no_clear_effect(self):
        effects, _ = decide_validators_timeout(
            **self._base_kwargs(appeal_leader_timeout=False)
        )
        assert _find_effect(effects, SetAppealLeaderTimeoutEffect) is None
