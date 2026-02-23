from backend.consensus.types import ConsensusResult
from backend.node.types import Vote


def determine_consensus_from_votes(votes_list: list[str]) -> ConsensusResult:
    """
    Determine consensus from a list of votes using actual majority (>50%).

    IDLE votes count as DISAGREE (couldn't participate = didn't agree).

    Args:
        votes_list: List of vote strings

    Returns:
        ConsensusResult: The consensus result
    """
    total = len(votes_list)
    majority = total / 2  # need strictly more than half

    agree_count = votes_list.count(Vote.AGREE.value)
    disagree_count = votes_list.count(Vote.DISAGREE.value)
    timeout_count = votes_list.count(Vote.TIMEOUT.value)
    idle_count = votes_list.count(Vote.IDLE.value)

    # IDLE = couldn't participate → counts as disagree
    effective_disagree = disagree_count + idle_count

    if agree_count > majority:
        return ConsensusResult.MAJORITY_AGREE
    elif effective_disagree > majority:
        return ConsensusResult.MAJORITY_DISAGREE
    elif timeout_count > majority:
        return ConsensusResult.TIMEOUT
    else:
        return ConsensusResult.NO_MAJORITY
