"""Unit tests for consensus voting logic (majority + IDLE)."""

from backend.consensus.utils import determine_consensus_from_votes
from backend.consensus.types import ConsensusResult
from backend.node.types import Vote

# --- Majority logic (>50% required) ---


def test_unanimous_agree():
    votes = [Vote.AGREE.value] * 5
    assert determine_consensus_from_votes(votes) == ConsensusResult.MAJORITY_AGREE


def test_majority_agree():
    votes = [Vote.AGREE.value] * 3 + [Vote.DISAGREE.value] * 2
    assert determine_consensus_from_votes(votes) == ConsensusResult.MAJORITY_AGREE


def test_majority_disagree():
    votes = [Vote.DISAGREE.value] * 3 + [Vote.AGREE.value] * 2
    assert determine_consensus_from_votes(votes) == ConsensusResult.MAJORITY_DISAGREE


def test_majority_timeout():
    votes = [Vote.TIMEOUT.value] * 3 + [Vote.AGREE.value] * 2
    assert determine_consensus_from_votes(votes) == ConsensusResult.TIMEOUT


def test_no_majority_split():
    """2 agree, 2 disagree, 1 timeout → no majority."""
    votes = (
        [Vote.AGREE.value] * 2 + [Vote.DISAGREE.value] * 2 + [Vote.TIMEOUT.value] * 1
    )
    assert determine_consensus_from_votes(votes) == ConsensusResult.NO_MAJORITY


def test_exact_half_is_not_majority():
    """Exactly 50% is NOT a majority (need >50%)."""
    votes = [Vote.AGREE.value] * 2 + [Vote.DISAGREE.value] * 2
    assert determine_consensus_from_votes(votes) == ConsensusResult.NO_MAJORITY


def test_single_validator_agree():
    votes = [Vote.AGREE.value]
    assert determine_consensus_from_votes(votes) == ConsensusResult.MAJORITY_AGREE


def test_single_validator_disagree():
    votes = [Vote.DISAGREE.value]
    assert determine_consensus_from_votes(votes) == ConsensusResult.MAJORITY_DISAGREE


# --- IDLE is classified as TIMEOUT, matching the rollup ballot ---


def test_idle_counts_as_timeout():
    """3 IDLE out of 5 are revealed as timeout ballots."""
    votes = [Vote.AGREE.value] * 2 + [Vote.IDLE.value] * 3
    assert determine_consensus_from_votes(votes) == ConsensusResult.TIMEOUT


def test_idle_does_not_join_disagree_bucket():
    """1 IDLE, 2 DISAGREE, and 2 AGREE has no majority."""
    votes = [Vote.AGREE.value] * 2 + [Vote.DISAGREE.value] * 2 + [Vote.IDLE.value] * 1
    assert determine_consensus_from_votes(votes) == ConsensusResult.NO_MAJORITY


def test_all_idle():
    """All IDLE → all classified as timeout."""
    votes = [Vote.IDLE.value] * 5
    assert determine_consensus_from_votes(votes) == ConsensusResult.TIMEOUT


def test_idle_not_enough_for_majority():
    """1 IDLE + 1 DISAGREE vs 3 AGREE → agree wins."""
    votes = [Vote.AGREE.value] * 3 + [Vote.DISAGREE.value] * 1 + [Vote.IDLE.value] * 1
    assert determine_consensus_from_votes(votes) == ConsensusResult.MAJORITY_AGREE


def test_idle_joins_timeout_bucket():
    """2 IDLE + 2 TIMEOUT are four protocol timeout ballots."""
    votes = [Vote.IDLE.value] * 2 + [Vote.TIMEOUT.value] * 2 + [Vote.AGREE.value] * 1
    assert determine_consensus_from_votes(votes) == ConsensusResult.TIMEOUT


def test_deterministic_violation_majority():
    votes = [Vote.DETERMINISTIC_VIOLATION.value] * 3 + [Vote.AGREE.value] * 2
    assert (
        determine_consensus_from_votes(votes) == ConsensusResult.DETERMINISTIC_VIOLATION
    )
