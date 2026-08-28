from backend.consensus.types import ConsensusResult
from backend.node.types import Vote


def determine_consensus_from_votes(
    votes_list: list[str], electorate_size: int | None = None
) -> ConsensusResult:
    """
    Determine consensus from a list of votes using actual majority (>50%).

    Studio's local IDLE sentinel is revealed on-chain as a Timeout ballot.

    Args:
        votes_list: List of vote strings

    Returns:
        ConsensusResult: The consensus result
    """
    # Terminal normal rounds deliberately keep the strict-majority threshold
    # of the full frozen electorate even though prior normal leaders are not
    # seated. Ordinary rounds use their local committee size.
    total = (
        len(votes_list)
        if electorate_size is None
        else max(len(votes_list), int(electorate_size))
    )
    majority = total / 2  # need strictly more than half

    agree_count = votes_list.count(Vote.AGREE.value)
    disagree_count = votes_list.count(Vote.DISAGREE.value)
    timeout_count = votes_list.count(Vote.TIMEOUT.value)
    deterministic_violation_count = votes_list.count(Vote.DETERMINISTIC_VIOLATION.value)
    idle_count = votes_list.count(Vote.IDLE.value)

    # RevealingState classifies local IDLE as the protocol's Timeout vote for
    # the rollup. The local decision must use that same classified ballot.
    effective_timeout = timeout_count + idle_count

    if agree_count > majority:
        return ConsensusResult.MAJORITY_AGREE
    elif disagree_count > majority:
        return ConsensusResult.MAJORITY_DISAGREE
    elif effective_timeout > majority:
        return ConsensusResult.TIMEOUT
    elif deterministic_violation_count > majority:
        return ConsensusResult.DETERMINISTIC_VIOLATION
    else:
        return ConsensusResult.NO_MAJORITY
