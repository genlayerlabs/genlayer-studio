"""Exhaustive tests for RevealingState decision functions.

Tests call pure functions directly — no mocks, no async, no context objects.
"""

import pytest
from backend.consensus.decisions import decide_revealing, merge_appeal_validators
from backend.consensus.effects import (
    AddTimestampEffect,
    StatusUpdateEffect,
    EmitRollupEventEffect,
    SetTimestampLastVoteEffect,
    SetTransactionResultEffect,
    SetAppealFailedEffect,
    UpdateConsensusHistoryEffect,
    ResetAppealProcessingTimeEffect,
    SetTimestampAppealEffect,
    IncreaseRotationCountEffect,
    SendMessageEffect,
)
from backend.consensus.types import ConsensusResult, ConsensusRound


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


def _base_kwargs(**overrides):
    """Default kwargs for decide_revealing."""
    defaults = dict(
        tx_hash="0xabc",
        consensus_result=ConsensusResult.MAJORITY_AGREE,
        appealed=False,
        appeal_validators_timeout=False,
        appeal_undetermined=False,
        rotation_count=0,
        config_rotation_rounds=3,
        vote_reveal_entries=[
            ({"address": "0xval1"}, 1),
            ({"address": "0xval2"}, 1),
        ],
        consensus_data_dict={"votes": {}, "leader_receipt": None},
        leader_receipt=["receipt_obj"],
        validation_results=[{"validator": "0xval1"}],
    )
    defaults.update(overrides)
    return defaults


# ═══════════════════════════════════════════════════════════════════
# merge_appeal_validators
# ═══════════════════════════════════════════════════════════════════


class TestMergeAppealValidators:
    def test_appeal_failed_0_appends_all(self):
        merged_votes, merged_vals = merge_appeal_validators(
            existing_votes={"a": "agree"},
            current_votes={"b": "disagree"},
            existing_validators=["v1", "v2"],
            current_validation_results=["v3", "v4"],
            appeal_failed=0,
        )
        assert merged_votes == {"a": "agree", "b": "disagree"}
        assert merged_vals == ["v1", "v2", "v3", "v4"]

    def test_appeal_failed_1_overwrites_half(self):
        existing = list(range(5))  # 5 existing validators
        current = ["new1", "new2", "new3"]
        _, merged_vals = merge_appeal_validators(
            existing_votes={},
            current_votes={},
            existing_validators=existing,
            current_validation_results=current,
            appeal_failed=1,
        )
        # n = (5-1)//2 = 2, keep existing[:1], then current
        assert merged_vals == [0] + current

    def test_appeal_failed_2(self):
        existing = list(range(7))
        current = ["n1", "n2", "n3", "n4"]
        _, merged_vals = merge_appeal_validators(
            existing_votes={},
            current_votes={},
            existing_validators=existing,
            current_validation_results=current,
            appeal_failed=2,
        )
        # n = len(current) - (len(existing) + 1) = 4 - 8 = -4
        # existing[:n-1] = existing[:-5] = existing[:2] (negative index)
        n = len(current) - (len(existing) + 1)
        assert merged_vals == existing[: n - 1] + current

    def test_votes_merged_with_pipe(self):
        merged_votes, _ = merge_appeal_validators(
            existing_votes={"a": "agree", "b": "disagree"},
            current_votes={"b": "agree", "c": "timeout"},
            existing_validators=[],
            current_validation_results=[],
            appeal_failed=0,
        )
        # current_votes overrides existing for key "b"
        assert merged_votes == {"a": "agree", "b": "agree", "c": "timeout"}


# ═══════════════════════════════════════════════════════════════════
# decide_revealing — always-emitted effects
# ═══════════════════════════════════════════════════════════════════


class TestRevealingAlwaysEmitted:
    def test_timestamp_is_first(self):
        _, effects = decide_revealing(**_base_kwargs())
        assert isinstance(effects[0], AddTimestampEffect)
        assert effects[0].state_name == "REVEALING"

    def test_status_update_second(self):
        _, effects = decide_revealing(**_base_kwargs())
        assert isinstance(effects[1], StatusUpdateEffect)
        assert effects[1].new_status == "REVEALING"

    def test_vote_revealed_events(self):
        entries = [
            ({"address": "0xv1"}, 1),
            ({"address": "0xv2"}, 2),
            ({"address": "0xv3"}, 1),
        ]
        _, effects = decide_revealing(**_base_kwargs(vote_reveal_entries=entries))
        reveals = _find_effects(effects, EmitRollupEventEffect)
        vote_reveals = [r for r in reveals if r.event_name == "emitVoteRevealed"]
        assert len(vote_reveals) == 3

        # First two: is_last=False, result=IDLE
        assert vote_reveals[0].extra_args[2] is False
        assert vote_reveals[0].extra_args[3] == int(ConsensusResult.IDLE)

        # Last: is_last=True, result=consensus_result
        assert vote_reveals[2].extra_args[2] is True
        assert vote_reveals[2].extra_args[3] == int(ConsensusResult.MAJORITY_AGREE)

    def test_timestamp_last_vote(self):
        _, effects = decide_revealing(**_base_kwargs())
        assert _find_effect(effects, SetTimestampLastVoteEffect) is not None


# ═══════════════════════════════════════════════════════════════════
# decide_revealing — normal (not appealed)
# ═══════════════════════════════════════════════════════════════════


class TestRevealingNormalMajorityAgree:
    def test_next_state_is_accepted(self):
        next_state, _ = decide_revealing(
            **_base_kwargs(consensus_result=ConsensusResult.MAJORITY_AGREE)
        )
        assert next_state == "accepted"


class TestRevealingNormalTimeout:
    def test_next_state_is_validators_timeout(self):
        next_state, _ = decide_revealing(
            **_base_kwargs(consensus_result=ConsensusResult.TIMEOUT)
        )
        assert next_state == "validators_timeout"


class TestRevealingNormalDisagreeMaxRotations:
    def test_next_state_is_undetermined(self):
        next_state, _ = decide_revealing(
            **_base_kwargs(
                consensus_result=ConsensusResult.MAJORITY_DISAGREE,
                rotation_count=3,
                config_rotation_rounds=3,
            )
        )
        assert next_state == "undetermined"

    def test_no_rotation_effects(self):
        _, effects = decide_revealing(
            **_base_kwargs(
                consensus_result=ConsensusResult.MAJORITY_DISAGREE,
                rotation_count=3,
                config_rotation_rounds=3,
            )
        )
        assert _find_effect(effects, IncreaseRotationCountEffect) is None


class TestRevealingNormalDisagreeWithRotations:
    def test_next_state_is_rotate(self):
        next_state, _ = decide_revealing(
            **_base_kwargs(
                consensus_result=ConsensusResult.MAJORITY_DISAGREE,
                rotation_count=1,
                config_rotation_rounds=3,
            )
        )
        assert next_state == "rotate"

    def test_has_increase_rotation_count(self):
        _, effects = decide_revealing(
            **_base_kwargs(
                consensus_result=ConsensusResult.MAJORITY_DISAGREE,
                rotation_count=1,
                config_rotation_rounds=3,
            )
        )
        assert _find_effect(effects, IncreaseRotationCountEffect) is not None

    def test_has_rotation_message(self):
        _, effects = decide_revealing(
            **_base_kwargs(
                consensus_result=ConsensusResult.MAJORITY_DISAGREE,
                rotation_count=0,
                config_rotation_rounds=3,
            )
        )
        msg = _find_effect(effects, SendMessageEffect)
        assert msg is not None
        assert "rotating the leader" in msg.message

    def test_has_consensus_history_update(self):
        _, effects = decide_revealing(
            **_base_kwargs(
                consensus_result=ConsensusResult.MAJORITY_DISAGREE,
                rotation_count=0,
                config_rotation_rounds=3,
            )
        )
        e = _find_effect(effects, UpdateConsensusHistoryEffect)
        assert e is not None
        assert e.consensus_round == ConsensusRound.LEADER_ROTATION

    def test_appeal_undetermined_uses_leader_rotation_appeal_round(self):
        _, effects = decide_revealing(
            **_base_kwargs(
                consensus_result=ConsensusResult.MAJORITY_DISAGREE,
                rotation_count=0,
                config_rotation_rounds=3,
                appeal_undetermined=True,
            )
        )
        e = _find_effect(effects, UpdateConsensusHistoryEffect)
        assert e.consensus_round == ConsensusRound.LEADER_ROTATION_APPEAL


class TestRevealingNormalNoMajority:
    def test_no_majority_max_rotations_is_undetermined(self):
        next_state, _ = decide_revealing(
            **_base_kwargs(
                consensus_result=ConsensusResult.NO_MAJORITY,
                rotation_count=3,
                config_rotation_rounds=3,
            )
        )
        assert next_state == "undetermined"

    def test_no_majority_with_rotations_is_rotate(self):
        next_state, _ = decide_revealing(
            **_base_kwargs(
                consensus_result=ConsensusResult.NO_MAJORITY,
                rotation_count=0,
                config_rotation_rounds=3,
            )
        )
        assert next_state == "rotate"


# ═══════════════════════════════════════════════════════════════════
# decide_revealing — appealed paths
# ═══════════════════════════════════════════════════════════════════


class TestRevealingAppealedMajorityAgree:
    def test_next_state_is_accepted(self):
        next_state, _ = decide_revealing(
            **_base_kwargs(
                appealed=True,
                consensus_result=ConsensusResult.MAJORITY_AGREE,
            )
        )
        assert next_state == "accepted"


class TestRevealingAppealValidatorsTimeoutTimeout:
    def test_next_state_is_validators_timeout(self):
        next_state, _ = decide_revealing(
            **_base_kwargs(
                appeal_validators_timeout=True,
                consensus_result=ConsensusResult.TIMEOUT,
            )
        )
        assert next_state == "validators_timeout"


class TestRevealingAppealSuccessful:
    def _kwargs(self, **overrides):
        base = _base_kwargs(
            appealed=True,
            consensus_result=ConsensusResult.MAJORITY_DISAGREE,
        )
        base.update(overrides)
        return base

    def test_next_state_is_appeal_successful(self):
        next_state, _ = decide_revealing(**self._kwargs())
        assert next_state == ConsensusRound.VALIDATOR_APPEAL_SUCCESSFUL

    def test_sets_transaction_result(self):
        _, effects = decide_revealing(**self._kwargs())
        assert _find_effect(effects, SetTransactionResultEffect) is not None

    def test_resets_appeal_failed_to_zero(self):
        _, effects = decide_revealing(**self._kwargs())
        e = _find_effect(effects, SetAppealFailedEffect)
        assert e is not None
        assert e.count == 0

    def test_updates_consensus_history_validator_appeal(self):
        _, effects = decide_revealing(**self._kwargs())
        e = _find_effect(effects, UpdateConsensusHistoryEffect)
        assert e is not None
        assert e.consensus_round == ConsensusRound.VALIDATOR_APPEAL_SUCCESSFUL

    def test_resets_appeal_processing_time(self):
        _, effects = decide_revealing(**self._kwargs())
        assert _find_effect(effects, ResetAppealProcessingTimeEffect) is not None

    def test_clears_timestamp_appeal(self):
        _, effects = decide_revealing(**self._kwargs())
        e = _find_effect(effects, SetTimestampAppealEffect)
        assert e is not None
        assert e.value is None

    def test_validators_timeout_appeal_uses_correct_round(self):
        _, effects = decide_revealing(
            **_base_kwargs(
                appeal_validators_timeout=True,
                consensus_result=ConsensusResult.MAJORITY_AGREE,
            )
        )
        e = _find_effect(effects, UpdateConsensusHistoryEffect)
        assert e is not None
        assert e.consensus_round == ConsensusRound.VALIDATOR_TIMEOUT_APPEAL_SUCCESSFUL

    def test_appealed_disagree_is_appeal_successful(self):
        """When appealed and result is DISAGREE, appeal succeeds."""
        next_state, _ = decide_revealing(
            **_base_kwargs(
                appealed=True,
                consensus_result=ConsensusResult.MAJORITY_DISAGREE,
            )
        )
        assert next_state == ConsensusRound.VALIDATOR_APPEAL_SUCCESSFUL


class TestRevealingInvalidResult:
    def test_raises_for_invalid_consensus_result(self):
        with pytest.raises(ValueError, match="Invalid consensus result"):
            decide_revealing(
                **_base_kwargs(
                    consensus_result=ConsensusResult.DETERMINISTIC_VIOLATION,
                )
            )
