"""Tests for ConsensusAlgorithm caller helper decision functions.

Tests call pure functions directly — no mocks, no async, no context objects.
"""

import pytest
from backend.consensus.decisions import (
    should_rollback_after_accepted,
    has_appeal_capacity,
)
from backend.consensus.types import ConsensusRound


# ═══════════════════════════════════════════════════════════════════
# should_rollback_after_accepted
# ═══════════════════════════════════════════════════════════════════


class TestShouldRollbackAfterAccepted:
    def test_none_history_returns_false(self):
        assert should_rollback_after_accepted(None) is False

    def test_empty_history_returns_false(self):
        assert should_rollback_after_accepted({}) is False

    def test_empty_results_returns_false(self):
        assert should_rollback_after_accepted({"consensus_results": []}) is False

    def test_accepted_round_returns_false(self):
        history = {
            "consensus_results": [{"consensus_round": ConsensusRound.ACCEPTED.value}]
        }
        assert should_rollback_after_accepted(history) is False

    def test_leader_appeal_successful_returns_false(self):
        history = {
            "consensus_results": [
                {"consensus_round": ConsensusRound.LEADER_APPEAL_SUCCESSFUL.value}
            ]
        }
        assert should_rollback_after_accepted(history) is False

    def test_validator_timeout_appeal_successful_returns_true(self):
        history = {
            "consensus_results": [
                {
                    "consensus_round": ConsensusRound.VALIDATOR_TIMEOUT_APPEAL_SUCCESSFUL.value
                }
            ]
        }
        assert should_rollback_after_accepted(history) is True

    def test_only_last_round_matters(self):
        history = {
            "consensus_results": [
                {
                    "consensus_round": ConsensusRound.VALIDATOR_TIMEOUT_APPEAL_SUCCESSFUL.value
                },
                {"consensus_round": ConsensusRound.ACCEPTED.value},
            ]
        }
        assert should_rollback_after_accepted(history) is False

    def test_last_round_is_timeout_appeal(self):
        history = {
            "consensus_results": [
                {"consensus_round": ConsensusRound.ACCEPTED.value},
                {
                    "consensus_round": ConsensusRound.VALIDATOR_TIMEOUT_APPEAL_SUCCESSFUL.value
                },
            ]
        }
        assert should_rollback_after_accepted(history) is True

    def test_missing_consensus_results_key(self):
        assert should_rollback_after_accepted({"other_key": []}) is False


# ═══════════════════════════════════════════════════════════════════
# has_appeal_capacity
# ═══════════════════════════════════════════════════════════════════


class TestHasAppealCapacity:
    def test_has_capacity(self):
        assert (
            has_appeal_capacity(
                num_involved_validators=3,
                num_used_leader_addresses=1,
                num_total_validators=10,
            )
            is True
        )

    def test_no_capacity_equal(self):
        assert (
            has_appeal_capacity(
                num_involved_validators=5,
                num_used_leader_addresses=5,
                num_total_validators=10,
            )
            is False
        )

    def test_no_capacity_exceeded(self):
        assert (
            has_appeal_capacity(
                num_involved_validators=6,
                num_used_leader_addresses=5,
                num_total_validators=10,
            )
            is False
        )

    def test_one_slot_remaining(self):
        assert (
            has_appeal_capacity(
                num_involved_validators=5,
                num_used_leader_addresses=4,
                num_total_validators=10,
            )
            is True
        )

    def test_zero_validators(self):
        assert (
            has_appeal_capacity(
                num_involved_validators=0,
                num_used_leader_addresses=0,
                num_total_validators=5,
            )
            is True
        )

    def test_all_zeros(self):
        assert (
            has_appeal_capacity(
                num_involved_validators=0,
                num_used_leader_addresses=0,
                num_total_validators=0,
            )
            is False
        )
