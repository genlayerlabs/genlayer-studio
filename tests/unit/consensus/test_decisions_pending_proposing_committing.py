"""Exhaustive tests for PendingState, ProposingState, and CommittingState decision functions.

Tests call pure functions directly — no mocks, no async, no context objects.
"""

import pytest
from backend.consensus.decisions import (
    decide_pending_pre,
    decide_pending_activate,
    prepare_proposing,
    decide_post_proposal,
    prepare_committing,
    decide_post_committing,
)
from backend.consensus.effects import (
    AddTimestampEffect,
    StatusUpdateEffect,
    EmitRollupEventEffect,
    SetTransactionResultEffect,
    SetTimestampAwaitingFinalizationEffect,
    ResetAppealProcessingTimeEffect,
    SetTimestampAppealEffect,
    SetLeaderTimeoutValidatorsEffect,
    SetTimestampLastVoteEffect,
    ResetRotationCountEffect,
)


# ── Helpers ────────────────────────────────────────────────────────


def _effect_types(effects):
    return [type(e).__name__ for e in effects]


def _find_effect(effects, effect_type):
    for e in effects:
        if isinstance(e, effect_type):
            return e
    return None


def _find_effects(effects, effect_type):
    return [e for e in effects if isinstance(e, effect_type)]


# ═══════════════════════════════════════════════════════════════════
# decide_pending_pre
# ═══════════════════════════════════════════════════════════════════


class TestDecidePendingPre:
    def test_always_emits_timestamp(self):
        effects = decide_pending_pre(
            tx_hash="0xabc",
            appeal_leader_timeout=False,
            appeal_undetermined=False,
        )
        ts = _find_effect(effects, AddTimestampEffect)
        assert ts is not None
        assert ts.state_name == "PENDING"

    def test_always_emits_reset_rotation_count(self):
        effects = decide_pending_pre(
            tx_hash="0xabc",
            appeal_leader_timeout=False,
            appeal_undetermined=False,
        )
        assert _find_effect(effects, ResetRotationCountEffect) is not None

    def test_tx_hash_propagated(self):
        effects = decide_pending_pre(
            tx_hash="0xdeadbeef",
            appeal_leader_timeout=True,
            appeal_undetermined=False,
        )
        for e in effects:
            assert e.tx_hash == "0xdeadbeef"

    def test_returns_exactly_two_effects(self):
        effects = decide_pending_pre(
            tx_hash="0x1",
            appeal_leader_timeout=False,
            appeal_undetermined=False,
        )
        assert len(effects) == 2

    def test_appeal_flags_dont_change_effect_count(self):
        effects = decide_pending_pre(
            tx_hash="0x1",
            appeal_leader_timeout=True,
            appeal_undetermined=True,
        )
        assert len(effects) == 2


# ═══════════════════════════════════════════════════════════════════
# decide_pending_activate
# ═══════════════════════════════════════════════════════════════════


class TestDecidePendingActivate:
    def test_normal_transaction_activates(self):
        assert (
            decide_pending_activate(
                appeal_undetermined=False, appeal_leader_timeout=False
            )
            is True
        )

    def test_appeal_undetermined_does_not_activate(self):
        assert (
            decide_pending_activate(
                appeal_undetermined=True, appeal_leader_timeout=False
            )
            is False
        )

    def test_appeal_leader_timeout_does_not_activate(self):
        assert (
            decide_pending_activate(
                appeal_undetermined=False, appeal_leader_timeout=True
            )
            is False
        )

    def test_both_appeals_does_not_activate(self):
        assert (
            decide_pending_activate(
                appeal_undetermined=True, appeal_leader_timeout=True
            )
            is False
        )


# ═══════════════════════════════════════════════════════════════════
# prepare_proposing
# ═══════════════════════════════════════════════════════════════════


class TestPrepareProposing:
    def _leader(self):
        return {"address": "0xleader"}

    def _validators(self):
        return [{"address": "0xval1"}, {"address": "0xval2"}]

    def test_always_emits_timestamp(self):
        effects = prepare_proposing(
            tx_hash="0xabc",
            activate=False,
            leader=self._leader(),
            remaining_validators=self._validators(),
        )
        ts = _find_effect(effects, AddTimestampEffect)
        assert ts is not None
        assert ts.state_name == "PROPOSING"

    def test_always_emits_status_update(self):
        effects = prepare_proposing(
            tx_hash="0xabc",
            activate=False,
            leader=self._leader(),
            remaining_validators=self._validators(),
        )
        su = _find_effect(effects, StatusUpdateEffect)
        assert su is not None
        assert su.new_status == "PROPOSING"

    def test_no_activation_event_when_not_activated(self):
        effects = prepare_proposing(
            tx_hash="0xabc",
            activate=False,
            leader=self._leader(),
            remaining_validators=self._validators(),
        )
        activated = [
            e
            for e in effects
            if isinstance(e, EmitRollupEventEffect)
            and e.event_name == "emitTransactionActivated"
        ]
        assert len(activated) == 0
        assert len(effects) == 2

    def test_activation_event_when_activated(self):
        leader = self._leader()
        validators = self._validators()
        effects = prepare_proposing(
            tx_hash="0xabc",
            activate=True,
            leader=leader,
            remaining_validators=validators,
        )
        activated = [
            e
            for e in effects
            if isinstance(e, EmitRollupEventEffect)
            and e.event_name == "emitTransactionActivated"
        ]
        assert len(activated) == 1
        assert len(effects) == 3

    def test_activation_event_contains_all_addresses(self):
        leader = {"address": "0xL"}
        validators = [{"address": "0xV1"}, {"address": "0xV2"}]
        effects = prepare_proposing(
            tx_hash="0xabc",
            activate=True,
            leader=leader,
            remaining_validators=validators,
        )
        activated = _find_effects(effects, EmitRollupEventEffect)[0]
        leader_addr, all_addrs = activated.extra_args
        assert leader_addr == "0xL"
        assert all_addrs == ["0xL", "0xV1", "0xV2"]

    def test_activation_event_with_empty_validators(self):
        leader = {"address": "0xL"}
        effects = prepare_proposing(
            tx_hash="0xabc",
            activate=True,
            leader=leader,
            remaining_validators=[],
        )
        activated = _find_effects(effects, EmitRollupEventEffect)[0]
        _, all_addrs = activated.extra_args
        assert all_addrs == ["0xL"]


# ═══════════════════════════════════════════════════════════════════
# decide_post_proposal
# ═══════════════════════════════════════════════════════════════════


def _post_proposal_kwargs(**overrides):
    defaults = dict(
        tx_hash="0xabc",
        leader_receipt_result=b"\x00success",
        leader_receipt_timed_out=False,
        execution_mode_leader_only=False,
        appeal_leader_timeout=False,
        leader_address="0xleader",
        leader={"address": "0xleader"},
        remaining_validators=[{"address": "0xval1"}],
        consensus_data_dict={"votes": {}, "leader_receipt": None},
    )
    defaults.update(overrides)
    return defaults


class TestPostProposalLeaderTimeout:
    def test_returns_leader_timeout(self):
        next_state, _ = decide_post_proposal(
            **_post_proposal_kwargs(leader_receipt_timed_out=True)
        )
        assert next_state == "leader_timeout"

    def test_leader_timeout_has_set_result(self):
        _, effects = decide_post_proposal(
            **_post_proposal_kwargs(leader_receipt_timed_out=True)
        )
        assert _find_effect(effects, SetTransactionResultEffect) is not None

    def test_leader_timeout_no_receipt_proposed(self):
        _, effects = decide_post_proposal(
            **_post_proposal_kwargs(leader_receipt_timed_out=True)
        )
        proposed = [
            e
            for e in effects
            if isinstance(e, EmitRollupEventEffect)
            and e.event_name == "emitTransactionReceiptProposed"
        ]
        assert len(proposed) == 0


class TestPostProposalNormal:
    def test_returns_committing(self):
        next_state, _ = decide_post_proposal(**_post_proposal_kwargs())
        assert next_state == "committing"

    def test_emits_set_result(self):
        _, effects = decide_post_proposal(**_post_proposal_kwargs())
        assert _find_effect(effects, SetTransactionResultEffect) is not None

    def test_emits_receipt_proposed(self):
        _, effects = decide_post_proposal(**_post_proposal_kwargs())
        proposed = [
            e
            for e in effects
            if isinstance(e, EmitRollupEventEffect)
            and e.event_name == "emitTransactionReceiptProposed"
        ]
        assert len(proposed) == 1

    def test_emits_clear_leader_timeout_validators(self):
        _, effects = decide_post_proposal(**_post_proposal_kwargs())
        e = _find_effect(effects, SetLeaderTimeoutValidatorsEffect)
        assert e is not None
        assert e.validators == []

    def test_no_appeal_effects_when_not_appealing(self):
        _, effects = decide_post_proposal(**_post_proposal_kwargs())
        assert _find_effect(effects, SetTimestampAwaitingFinalizationEffect) is None
        assert _find_effect(effects, ResetAppealProcessingTimeEffect) is None
        assert _find_effect(effects, SetTimestampAppealEffect) is None


class TestPostProposalAppealLeaderTimeout:
    def test_returns_committing(self):
        next_state, _ = decide_post_proposal(
            **_post_proposal_kwargs(appeal_leader_timeout=True)
        )
        assert next_state == "committing"

    def test_emits_awaiting_finalization(self):
        _, effects = decide_post_proposal(
            **_post_proposal_kwargs(appeal_leader_timeout=True)
        )
        assert _find_effect(effects, SetTimestampAwaitingFinalizationEffect) is not None

    def test_emits_reset_appeal_processing_time(self):
        _, effects = decide_post_proposal(
            **_post_proposal_kwargs(appeal_leader_timeout=True)
        )
        assert _find_effect(effects, ResetAppealProcessingTimeEffect) is not None

    def test_emits_clear_timestamp_appeal(self):
        _, effects = decide_post_proposal(
            **_post_proposal_kwargs(appeal_leader_timeout=True)
        )
        e = _find_effect(effects, SetTimestampAppealEffect)
        assert e is not None
        assert e.value is None


class TestPostProposalLeaderOnly:
    def test_returns_accepted_leader_only(self):
        next_state, _ = decide_post_proposal(
            **_post_proposal_kwargs(execution_mode_leader_only=True)
        )
        assert next_state == "accepted_leader_only"

    def test_emits_receipt_proposed(self):
        _, effects = decide_post_proposal(
            **_post_proposal_kwargs(execution_mode_leader_only=True)
        )
        proposed = [
            e
            for e in effects
            if isinstance(e, EmitRollupEventEffect)
            and e.event_name == "emitTransactionReceiptProposed"
        ]
        assert len(proposed) == 1

    def test_emits_second_set_result_for_leader_only(self):
        _, effects = decide_post_proposal(
            **_post_proposal_kwargs(execution_mode_leader_only=True)
        )
        results = _find_effects(effects, SetTransactionResultEffect)
        assert len(results) == 2


# ═══════════════════════════════════════════════════════════════════
# prepare_committing
# ═══════════════════════════════════════════════════════════════════


class TestPrepareCommitting:
    def test_emits_timestamp(self):
        effects = prepare_committing(tx_hash="0xabc")
        ts = _find_effect(effects, AddTimestampEffect)
        assert ts is not None
        assert ts.state_name == "COMMITTING"

    def test_emits_status_update(self):
        effects = prepare_committing(tx_hash="0xabc")
        su = _find_effect(effects, StatusUpdateEffect)
        assert su is not None
        assert su.new_status == "COMMITTING"

    def test_returns_exactly_two_effects(self):
        effects = prepare_committing(tx_hash="0xabc")
        assert len(effects) == 2

    def test_tx_hash_propagated(self):
        effects = prepare_committing(tx_hash="0xdeadbeef")
        for e in effects:
            assert e.tx_hash == "0xdeadbeef"


# ═══════════════════════════════════════════════════════════════════
# decide_post_committing
# ═══════════════════════════════════════════════════════════════════


class TestPostCommitting:
    def test_single_validator_emits_one_event(self):
        validators = [{"address": "0xval1"}]
        effects = decide_post_committing(tx_hash="0xabc", validators_to_emit=validators)
        commits = _find_effects(effects, EmitRollupEventEffect)
        assert len(commits) == 1

    def test_single_validator_is_last(self):
        validators = [{"address": "0xval1"}]
        effects = decide_post_committing(tx_hash="0xabc", validators_to_emit=validators)
        commit = _find_effects(effects, EmitRollupEventEffect)[0]
        addr, is_last = commit.extra_args
        assert addr == "0xval1"
        assert is_last is True

    def test_multiple_validators_emit_events(self):
        validators = [
            {"address": "0xval1"},
            {"address": "0xval2"},
            {"address": "0xval3"},
        ]
        effects = decide_post_committing(tx_hash="0xabc", validators_to_emit=validators)
        commits = _find_effects(effects, EmitRollupEventEffect)
        assert len(commits) == 3

    def test_only_last_validator_has_is_last_true(self):
        validators = [
            {"address": "0xval1"},
            {"address": "0xval2"},
            {"address": "0xval3"},
        ]
        effects = decide_post_committing(tx_hash="0xabc", validators_to_emit=validators)
        commits = _find_effects(effects, EmitRollupEventEffect)
        assert commits[0].extra_args[1] is False
        assert commits[1].extra_args[1] is False
        assert commits[2].extra_args[1] is True

    def test_event_names_are_vote_committed(self):
        validators = [{"address": "0xval1"}, {"address": "0xval2"}]
        effects = decide_post_committing(tx_hash="0xabc", validators_to_emit=validators)
        commits = _find_effects(effects, EmitRollupEventEffect)
        for c in commits:
            assert c.event_name == "emitVoteCommitted"

    def test_has_timestamp_last_vote(self):
        effects = decide_post_committing(
            tx_hash="0xabc",
            validators_to_emit=[{"address": "0xval1"}],
        )
        assert _find_effect(effects, SetTimestampLastVoteEffect) is not None

    def test_empty_validators_only_timestamp(self):
        effects = decide_post_committing(tx_hash="0xabc", validators_to_emit=[])
        commits = _find_effects(effects, EmitRollupEventEffect)
        assert len(commits) == 0
        assert _find_effect(effects, SetTimestampLastVoteEffect) is not None

    def test_tx_hash_propagated(self):
        effects = decide_post_committing(
            tx_hash="0xdeadbeef",
            validators_to_emit=[{"address": "0xval1"}],
        )
        for e in effects:
            assert e.tx_hash == "0xdeadbeef"

    def test_validator_addresses_in_events(self):
        validators = [{"address": "0xA"}, {"address": "0xB"}]
        effects = decide_post_committing(tx_hash="0xabc", validators_to_emit=validators)
        commits = _find_effects(effects, EmitRollupEventEffect)
        assert commits[0].extra_args[0] == "0xA"
        assert commits[1].extra_args[0] == "0xB"

    def test_validator_accounts_in_events(self):
        validators = [
            {"address": "0xA", "model": "gpt-4"},
            {"address": "0xB", "model": "claude"},
        ]
        effects = decide_post_committing(tx_hash="0xabc", validators_to_emit=validators)
        commits = _find_effects(effects, EmitRollupEventEffect)
        assert commits[0].account == {"address": "0xA", "model": "gpt-4"}
        assert commits[1].account == {"address": "0xB", "model": "claude"}
