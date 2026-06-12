from __future__ import annotations

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
    consensus_round: str,
    leader_timeunits: int,
    validator_timeunits: int,
    max_validator_timeunits: int,
) -> tuple[int, int]:
    per_round.append(
        _round_entry(
            round_index=len(per_round),
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
        leader_used, validator_used = _record_round(
            per_round,
            consensus_round=consensus_round,
            leader_timeunits=leader_timeunits,
            validator_timeunits=validator_timeunits,
            max_validator_timeunits=max_validator_timeunits,
        )
        leader_timeunits_used += leader_used
        validator_timeunits_used += validator_used

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
        leader_used, validator_used = _record_round(
            per_round,
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
