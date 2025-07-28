from backend.consensus.types import ConsensusResult
from backend.node.types import Vote


def determine_consensus_from_votes(votes_list: list[str]) -> ConsensusResult:
    """
    Determine consensus from a list of votes.

    Args:
        votes_list: List of vote strings

    Returns:
        ConsensusResult: The consensus result
    """
    agree_count = votes_list.count(Vote.AGREE.value)
    disagree_count = votes_list.count(Vote.DISAGREE.value)
    timeout_count = votes_list.count(Vote.TIMEOUT.value)

    if timeout_count > agree_count and timeout_count > disagree_count:
        consensus_result = ConsensusResult.TIMEOUT
    elif agree_count > disagree_count and agree_count > timeout_count:
        consensus_result = ConsensusResult.MAJORITY_AGREE
    elif disagree_count > agree_count and disagree_count > timeout_count:
        consensus_result = ConsensusResult.MAJORITY_DISAGREE
    else:
        consensus_result = ConsensusResult.NO_MAJORITY

    return consensus_result
