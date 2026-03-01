from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.consensus.effects import Effect


@dataclass(frozen=True)
class TransitionResult:
    """Describes a state transition as pure data.

    Attributes:
        next_state: Name of the next state (e.g. "accepted", "proposing") or
                    a ConsensusRound enum value, or None to terminate.
        effects: Ordered list of side effects to execute.
        context_updates: Dictionary of TransactionContext field updates to apply
                         before moving to the next state (e.g. leader, votes,
                         remaining_validators).
    """

    next_state: Any = None
    effects: list[Effect] = field(default_factory=list)
    context_updates: dict[str, Any] = field(default_factory=dict)
