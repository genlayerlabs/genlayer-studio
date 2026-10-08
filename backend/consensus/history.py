from __future__ import annotations

import copy
from typing import Any

from backend.consensus.types import ConsensusRound

TIME_UNIT_MILLISECONDS = 1000
# Protocol mapping for node parity:
# 1 time unit (TU) == 1 second of GenVM wall-clock runtime. Studio receipts
# measure each execution as processing_time milliseconds, so TU consumption is
# ceil(processing_time_ms / 1000) per receipt. Missing, zero, negative, or
# malformed processing_time values consume 0 TU.

NON_ROUND_CONSENSUS_EVENTS = {
    ConsensusRound.LEADER_ROTATION.value,
    ConsensusRound.LEADER_ROTATION_APPEAL.value,
}

# Studio records the verdict and execution of an appeal as one history item.
# Consensus, however, advances the fee round according to the appeal shape:
# validator appeals occupy the next odd round (skipping an even gap when
# chained), while leader/leader-timeout appeals create an appeal bookkeeping
# round and execute again in the following even round.  Fee accounting must
# preserve those on-chain round numbers even though the UI history is compact.
LEADER_APPEAL_CONSENSUS_ROUNDS = {
    ConsensusRound.LEADER_APPEAL_SUCCESSFUL.value,
    ConsensusRound.LEADER_APPEAL_FAILED.value,
    ConsensusRound.LEADER_TIMEOUT_APPEAL_SUCCESSFUL.value,
    ConsensusRound.LEADER_TIMEOUT_APPEAL_FAILED.value,
}
VALIDATOR_APPEAL_CONSENSUS_ROUNDS = {
    ConsensusRound.VALIDATOR_APPEAL_SUCCESSFUL.value,
    ConsensusRound.VALIDATOR_APPEAL_FAILED.value,
    ConsensusRound.VALIDATOR_TIMEOUT_APPEAL_SUCCESSFUL.value,
    ConsensusRound.VALIDATORS_TIMEOUT_APPEAL_FAILED.value,
}
TERMINAL_VALIDATOR_APPEAL_ROUNDS = {
    ConsensusRound.VALIDATOR_APPEAL_SUCCESSFUL.value,
    ConsensusRound.VALIDATOR_TIMEOUT_APPEAL_SUCCESSFUL.value,
}

# Studio has no deployed TransactionManager, so persist the small part of its
# immutable DecisionRecord that governs exact-ID commands and appeal timing in
# consensus_history. Keeping it beside the round history makes snapshots and
# existing transaction reads carry the authority without a schema migration.
LATEST_DECISION_KEY = "latestDecision"
ACTIVE_APPEAL_BASIS_KEY = "activeAppealBasis"
# Studio processes an admitted appeal asynchronously, unlike the atomic
# on-chain call. Preserve the exact agreed transaction state so worker or
# process failures can retry the paid appeal without resetting the original
# decision or stranding its custody.
APPEAL_RECOVERY_SNAPSHOT_KEY = "appealRecoverySnapshot"
VALIDATOR_APPEAL_CONTEXT = "validatorAppeal"
LEADER_APPEAL_REPLAY_CONTEXT = "leaderAppealReplay"


def latest_decision_metadata(
    consensus_history: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(consensus_history, dict):
        return None
    value = consensus_history.get(LATEST_DECISION_KEY)
    return value if isinstance(value, dict) else None


def current_decision_id(consensus_history: dict[str, Any] | None) -> int:
    """Return Studio's equivalent of the latest materialized DecisionId."""

    metadata = latest_decision_metadata(consensus_history)
    if metadata is not None:
        try:
            decision_id = int(metadata.get("decisionId") or 0)
        except (TypeError, ValueError):
            decision_id = 0
        if decision_id > 0:
            return decision_id
    round_index = completed_consensus_round_index(consensus_history)
    return 1 + ((round_index + 1) // 2)


def prepare_appeal_decision_basis(
    consensus_history: dict[str, Any] | None,
    *,
    expected_decision_id: int,
    submitted_at: int,
    appeal_deadline: int,
    retention_bps: int,
    appeal_context: str | None = None,
) -> dict[str, Any]:
    """Freeze the appeal authority and validator-failure window at submission."""

    updated = copy.deepcopy(consensus_history or {})
    decision_id = current_decision_id(updated)
    if decision_id != int(expected_decision_id):
        raise ValueError("DecisionBasisMismatch")
    remaining = int(appeal_deadline) - int(submitted_at)
    if remaining <= 0:
        raise ValueError("CanNotAppeal")
    if appeal_context is None:
        decision = latest_decision_metadata(updated) or {}
        decision_status = str(decision.get("status") or "").upper()
        if not decision_status:
            rounds = completed_consensus_rounds(updated)
            if rounds:
                decision_status = str(rounds[-1].get("consensus_round") or "").upper()
        if decision_status in {
            "ACCEPTED",
            "VALIDATORS_TIMEOUT",
            "VALIDATORS TIMEOUT",
        }:
            appeal_context = VALIDATOR_APPEAL_CONTEXT
        elif decision_status in {
            "UNDETERMINED",
            "LEADER_TIMEOUT",
            "LEADER TIMEOUT",
        }:
            appeal_context = LEADER_APPEAL_REPLAY_CONTEXT
    if appeal_context not in {
        VALIDATOR_APPEAL_CONTEXT,
        LEADER_APPEAL_REPLAY_CONTEXT,
    }:
        raise ValueError("DecisionBasisMismatch")
    retention_bps = min(10_000, max(0, int(retention_bps)))
    next_window = (remaining * retention_bps) // 10_000
    if next_window == 0:
        next_window = 1
    updated[ACTIVE_APPEAL_BASIS_KEY] = {
        "decisionId": decision_id,
        "context": appeal_context,
        "submittedAt": int(submitted_at),
        "nextAppealWindow": next_window,
    }
    return updated


def materialize_decision_metadata(
    consensus_history: dict[str, Any] | None,
    *,
    status: str,
    materialized_at: int,
    default_appeal_window: int,
) -> dict[str, Any]:
    """Advance DecisionId and bind one immutable Studio decision window."""

    updated = copy.deepcopy(consensus_history or {})
    round_index = completed_consensus_round_index(updated)
    decision_id = 1 + ((round_index + 1) // 2)
    basis = updated.get(ACTIVE_APPEAL_BASIS_KEY)
    if isinstance(basis, dict):
        try:
            basis_decision_id = int(basis.get("decisionId") or 0)
            appeal_window = int(basis.get("nextAppealWindow") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("DecisionBasisMismatch") from exc
        if basis_decision_id != decision_id - 1 or appeal_window <= 0:
            raise ValueError("DecisionBasisMismatch")
        context = basis.get("context")
        if context == LEADER_APPEAL_REPLAY_CONTEXT:
            # Leader appeals clear the reduced-window override at submission,
            # regardless of whether the replay changes the application result.
            appeal_window = max(1, int(default_appeal_window))
        elif context == VALIDATOR_APPEAL_CONTEXT:
            # Only a failed validator appeal resumes the incumbent with the
            # submission-frozen reduced remainder. A successful challenge
            # creates a new decision and therefore gets a fresh full window.
            latest_appeal_round = next(
                (
                    str(entry.get("consensus_round") or "")
                    for entry in reversed(completed_consensus_rounds(updated))
                    if str(entry.get("consensus_round") or "")
                    in VALIDATOR_APPEAL_CONSENSUS_ROUNDS
                ),
                None,
            )
            if latest_appeal_round in TERMINAL_VALIDATOR_APPEAL_ROUNDS:
                appeal_window = max(1, int(default_appeal_window))
        elif context is not None:
            raise ValueError("DecisionBasisMismatch")
    else:
        appeal_window = max(1, int(default_appeal_window))

    materialized_at = int(materialized_at)
    updated[LATEST_DECISION_KEY] = {
        "decisionId": decision_id,
        "status": str(status).upper(),
        "materializedAt": materialized_at,
        "appealDeadline": materialized_at + appeal_window,
    }
    updated.pop(ACTIVE_APPEAL_BASIS_KEY, None)
    return updated


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
    entries = logical_fee_round_entries(consensus_history)
    return entries[-1][0] if entries else 0


def has_terminal_validator_appeal(
    consensus_history: dict[str, Any] | None,
) -> bool:
    """Whether a successful validator review made the next normal round final."""

    return any(
        str(entry.get("consensus_round") or "") in TERMINAL_VALIDATOR_APPEAL_ROUNDS
        for entry in completed_consensus_rounds(consensus_history)
    )


def _next_logical_fee_round(previous_round: int | None, outcome: str) -> int:
    if previous_round is None:
        return 0
    if outcome in LEADER_APPEAL_CONSENSUS_ROUNDS:
        return previous_round + 2
    if outcome in VALIDATOR_APPEAL_CONSENSUS_ROUNDS:
        return previous_round + (1 if previous_round % 2 == 0 else 2)
    return previous_round + 1


def logical_fee_round_entries(
    consensus_history: dict[str, Any] | None,
) -> list[tuple[int, dict[str, Any]]]:
    """Return compact Studio history entries keyed by on-chain fee round."""
    logical_entries: list[tuple[int, dict[str, Any]]] = []
    previous_round: int | None = None
    for entry in completed_consensus_rounds(consensus_history):
        outcome = str(entry.get("consensus_round") or "")
        logical_round = _next_logical_fee_round(previous_round, outcome)
        logical_entries.append((logical_round, entry))
        previous_round = logical_round
    return logical_entries


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
    previous_round: int | None = None
    for entry in results:
        if not isinstance(entry, dict):
            continue
        event = str(entry.get("consensus_round") or "")
        if event in NON_ROUND_CONSENSUS_EVENTS:
            pending_rotations += 1
            continue
        round_index = _next_logical_fee_round(previous_round, event)
        rotations[round_index] = pending_rotations
        pending_rotations = 0
        previous_round = round_index
    return rotations


def receipt_time_units(receipt: dict | None) -> int:
    if not isinstance(receipt, dict):
        return 0
    try:
        processing_time_ms = int(receipt.get("processing_time") or 0)
    except (TypeError, ValueError):
        return 0
    if processing_time_ms <= 0:
        return 0
    return (processing_time_ms + TIME_UNIT_MILLISECONDS - 1) // TIME_UNIT_MILLISECONDS


def _receipt_iter(receipts: Any):
    if isinstance(receipts, list):
        yield from receipts
    elif isinstance(receipts, dict):
        yield receipts


def _entry_receipts(entry: dict[str, Any]):
    yield from _receipt_iter(entry.get("leader_result"))
    yield from _receipt_iter(entry.get("validator_results"))


def _consensus_data_receipts(consensus_data: dict[str, Any]):
    yield from _receipt_iter(consensus_data.get("leader_receipt"))
    validators = consensus_data.get("validators")
    if isinstance(validators, list):
        for validator in validators:
            if isinstance(validator, dict) and "receipt" in validator:
                yield from _receipt_iter(validator.get("receipt"))
            else:
                yield from _receipt_iter(validator)
    else:
        yield from _receipt_iter(validators)


def _bucket_time_units(receipts: Any) -> tuple[int, int, int]:
    leader_timeunits = 0
    validator_timeunits = 0
    max_validator_timeunits = 0
    for receipt in receipts:
        if not isinstance(receipt, dict):
            continue
        time_units = receipt_time_units(receipt)
        mode = receipt.get("mode")
        if mode == "leader":
            leader_timeunits += time_units
        elif mode == "validator":
            validator_timeunits += time_units
            max_validator_timeunits = max(max_validator_timeunits, time_units)
    return leader_timeunits, validator_timeunits, max_validator_timeunits


def _has_receipts(receipts: list[Any]) -> bool:
    # Only receipts with a recognized execution mode carry attributable
    # time-unit consumption; mode-less dicts (e.g. partial or legacy
    # payloads) must not produce a per-round entry.
    return any(
        isinstance(receipt, dict) and receipt.get("mode") in ("leader", "validator")
        for receipt in receipts
    )


def _round_entry(
    *,
    round_index: int,
    consensus_round: str,
    leader_timeunits: int,
    validator_timeunits: int,
    max_validator_timeunits: int,
) -> dict[str, int | str]:
    return {
        "round": round_index,
        "consensus_round": consensus_round,
        "leader_timeunits": leader_timeunits,
        "validator_timeunits": validator_timeunits,
        "max_validator_timeunits": max_validator_timeunits,
    }


def _empty_pending_time_units() -> dict[str, int | str | bool]:
    return {
        "leader_timeunits": 0,
        "validator_timeunits": 0,
        "max_validator_timeunits": 0,
        "consensus_round": "",
        "has_rotation": False,
    }


def _record_round(
    per_round: list[dict[str, int | str]],
    *,
    round_index: int | None = None,
    consensus_round: str,
    leader_timeunits: int,
    validator_timeunits: int,
    max_validator_timeunits: int,
) -> tuple[int, int]:
    per_round.append(
        _round_entry(
            round_index=len(per_round) if round_index is None else round_index,
            consensus_round=consensus_round,
            leader_timeunits=leader_timeunits,
            validator_timeunits=validator_timeunits,
            max_validator_timeunits=max_validator_timeunits,
        )
    )
    return leader_timeunits, validator_timeunits


def _history_results(consensus_history: dict | None) -> list[Any]:
    results = (
        consensus_history.get("consensus_results")
        if isinstance(consensus_history, dict)
        else None
    )
    return results if isinstance(results, list) else []


def _accumulate_pending_rotation(
    pending: dict[str, int | str | bool],
    consensus_round: str,
    leader_timeunits: int,
    validator_timeunits: int,
    max_validator_timeunits: int,
) -> None:
    pending["leader_timeunits"] = int(pending["leader_timeunits"]) + leader_timeunits
    pending["validator_timeunits"] = (
        int(pending["validator_timeunits"]) + validator_timeunits
    )
    pending["max_validator_timeunits"] = max(
        int(pending["max_validator_timeunits"]), max_validator_timeunits
    )
    pending["consensus_round"] = consensus_round
    pending["has_rotation"] = True


def _consume_history_time_units(
    results: list[Any],
) -> tuple[list[dict[str, int | str]], int, int, dict[str, int | str | bool]]:
    per_round: list[dict[str, int | str]] = []
    pending = _empty_pending_time_units()
    leader_timeunits_used = 0
    validator_timeunits_used = 0
    previous_round: int | None = None

    for entry in results:
        if not isinstance(entry, dict):
            continue
        consensus_round = str(entry.get("consensus_round") or "")
        leader_timeunits, validator_timeunits, max_validator_timeunits = (
            _bucket_time_units(_entry_receipts(entry))
        )
        if consensus_round in NON_ROUND_CONSENSUS_EVENTS:
            _accumulate_pending_rotation(
                pending,
                consensus_round,
                leader_timeunits,
                validator_timeunits,
                max_validator_timeunits,
            )
            continue

        leader_timeunits += int(pending["leader_timeunits"])
        validator_timeunits += int(pending["validator_timeunits"])
        max_validator_timeunits = max(
            max_validator_timeunits, int(pending["max_validator_timeunits"])
        )
        pending = _empty_pending_time_units()
        logical_round = _next_logical_fee_round(previous_round, consensus_round)
        leader_used, validator_used = _record_round(
            per_round,
            round_index=logical_round,
            consensus_round=consensus_round,
            leader_timeunits=leader_timeunits,
            validator_timeunits=validator_timeunits,
            max_validator_timeunits=max_validator_timeunits,
        )
        leader_timeunits_used += leader_used
        validator_timeunits_used += validator_used
        previous_round = logical_round

    return per_round, leader_timeunits_used, validator_timeunits_used, pending


def _fallback_consensus_data_round(
    per_round: list[dict[str, int | str]],
    consensus_data: dict | None,
) -> tuple[int, int]:
    if not isinstance(consensus_data, dict):
        return 0, 0
    receipts = list(_consensus_data_receipts(consensus_data))
    if not _has_receipts(receipts):
        return 0, 0
    leader_timeunits, validator_timeunits, max_validator_timeunits = _bucket_time_units(
        receipts
    )
    return _record_round(
        per_round,
        consensus_round="",
        leader_timeunits=leader_timeunits,
        validator_timeunits=validator_timeunits,
        max_validator_timeunits=max_validator_timeunits,
    )


def time_unit_consumption(
    consensus_history: dict | None,
    consensus_data: dict | None,
) -> dict:
    per_round, leader_timeunits_used, validator_timeunits_used, pending = (
        _consume_history_time_units(_history_results(consensus_history))
    )

    if (
        not per_round
        and pending["leader_timeunits"] == 0
        and pending["validator_timeunits"] == 0
        and not pending["has_rotation"]
    ):
        leader_used, validator_used = _fallback_consensus_data_round(
            per_round, consensus_data
        )
        leader_timeunits_used += leader_used
        validator_timeunits_used += validator_used

    if pending["has_rotation"]:
        trailing_round = int(per_round[-1]["round"]) + 1 if per_round else 0
        leader_used, validator_used = _record_round(
            per_round,
            round_index=trailing_round,
            consensus_round=str(pending["consensus_round"]),
            leader_timeunits=int(pending["leader_timeunits"]),
            validator_timeunits=int(pending["validator_timeunits"]),
            max_validator_timeunits=int(pending["max_validator_timeunits"]),
        )
        leader_timeunits_used += leader_used
        validator_timeunits_used += validator_used

    return {
        "leader_timeunits_used": leader_timeunits_used,
        "validator_timeunits_used": validator_timeunits_used,
        "per_round": per_round,
    }
