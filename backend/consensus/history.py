from __future__ import annotations

from typing import Any

from backend.consensus.types import ConsensusRound


NON_ROUND_CONSENSUS_EVENTS = {
    ConsensusRound.LEADER_ROTATION.value,
    ConsensusRound.LEADER_ROTATION_APPEAL.value,
}


def is_completed_consensus_round(entry: dict[str, Any]) -> bool:
    return str(entry.get("consensus_round") or "") not in NON_ROUND_CONSENSUS_EVENTS


def completed_consensus_rounds(
    consensus_history: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not isinstance(consensus_history, dict):
        return []
    results = consensus_history.get("consensus_results")
    if not isinstance(results, list):
        return []
    return [
        entry
        for entry in results
        if isinstance(entry, dict) and is_completed_consensus_round(entry)
    ]


def completed_consensus_round_index(consensus_history: dict[str, Any] | None) -> int:
    return max(0, len(completed_consensus_rounds(consensus_history)) - 1)


def actual_leader_rotations_by_round(
    consensus_history: dict[str, Any] | None,
) -> dict[int, int]:
    if not isinstance(consensus_history, dict):
        return {}
    results = consensus_history.get("consensus_results")
    if not isinstance(results, list):
        return {}

    rotations: dict[int, int] = {}
    pending_rotations = 0
    round_index = 0
    for entry in results:
        if not isinstance(entry, dict):
            continue
        event = str(entry.get("consensus_round") or "")
        if event in NON_ROUND_CONSENSUS_EVENTS:
            pending_rotations += 1
            continue
        rotations[round_index] = pending_rotations
        pending_rotations = 0
        round_index += 1
    return rotations
