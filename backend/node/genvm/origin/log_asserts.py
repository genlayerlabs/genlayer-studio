"""
Assertions over the executor's structured log records.

The manager captures every executor-emitted log line and surfaces them as
``RunHostAndProgramRes.genvm_log`` — a list of JSON objects shaped like
``{"message": ..., "target": ..., <extra fields>...}``. Capture is *unbounded*
under ``debug_mode >= safe-unbounded`` (integration tests run ``unsafe``), so no
record is evicted regardless of how chatty the run is.

The load action emits one stable ``"runner load"`` record per load, carrying:

- ``runner``      — the canonical runner id (``chain:``/``custom:``/``name:hash``);
- ``runner_load_cost``  — the flat per-load memory limiter constant;
- ``size``        — the charged content size (archive ``total_size``);
- ``status``      — ``"charged"`` (first load in this VM) or ``"cached"``
        (already in the VM's loaded set — free).

This module is deliberately message-agnostic: a matcher's ``message`` defaults
to ``"runner load"`` but may name any message, so the same machinery serves
future charge-related log lines.

Numeric fields (``size``, ``runner_load_cost``) are emitted by the executor as JSON
*strings* (the logger's default ``Display`` capture), so every comparison here
is done on the string form — an assertion may write ``4096`` or ``'4096'``
interchangeably.

Assertion schema entries may include ``match`` for subset matching over record
fields, ``runner_prefix`` to isolate runner ids such as ``custom:``, count bounds
(``count``/``min``/``max``), and ``size_is_code_len`` for init steps whose load
size must equal the raw contract code length.

`check` returns a list of human-readable failure strings (empty == all passed).
`extract` is a convenience returning the matching records for eyeballing.
"""

import typing

DEFAULT_MESSAGE = "runner load"


def _record_matches(record: dict, crit: dict, runner_prefix: str | None) -> bool:
    for k, v in crit.items():
        if str(record.get(k)) != str(v):
            return False
    if runner_prefix is not None:
        runner = record.get("runner")
        if not isinstance(runner, str) or not runner.startswith(runner_prefix):
            return False
    return True


def _select(genvm_log: list[dict], crit: dict, runner_prefix: str | None) -> list[dict]:
    crit = dict(crit)
    crit.setdefault("message", DEFAULT_MESSAGE)
    return [r for r in genvm_log if _record_matches(r, crit, runner_prefix)]


def extract(
    genvm_log: list[dict],
    *,
    match: dict | None = None,
    runner_prefix: str | None = None,
) -> list[dict]:
    """
    Return the ``genvm_log`` records matching ``match`` (defaulting to the
    ``runner load`` message) and an optional ``runner`` prefix."""
    return _select(genvm_log, match or {}, runner_prefix)


def check(
    genvm_log: list[dict],
    asserts: list[dict],
    *,
    code_len: int | None = None,
) -> list[str]:
    """Evaluate ``asserts`` against ``genvm_log``; return a list of failures."""
    errors: list[str] = []
    for i, a in enumerate(asserts):
        crit = a.get("match", {})
        runner_prefix = a.get("runner_prefix")
        matched = _select(genvm_log, crit, runner_prefix)
        n = len(matched)

        label = f"assert[{i}] match={crit}"
        if runner_prefix is not None:
            label += f" runner_prefix={runner_prefix!r}"

        if "count" in a and n != a["count"]:
            errors.append(f'{label}: expected count {a["count"]}, got {n}')
        if "min" in a and n < a["min"]:
            errors.append(f'{label}: expected at least {a["min"]}, got {n}')
        if "max" in a and n > a["max"]:
            errors.append(f'{label}: expected at most {a["max"]}, got {n}')

        if a.get("size_is_code_len"):
            if code_len is None:
                errors.append(
                    f"{label}: size_is_code_len set but this step ships no code"
                )
            else:
                bad = [r for r in matched if str(r.get("size")) != str(code_len)]
                if bad:
                    got = [r.get("size") for r in bad]
                    errors.append(
                        f"{label}: expected size == code_len {code_len}, got {got}"
                    )
    return errors


def summarize(genvm_log: list[dict]) -> list[dict]:
    """Compact view of every ``runner load`` record, for failure context."""
    out = []
    for r in _select(genvm_log, {}, None):
        out.append(
            {
                "runner": r.get("runner"),
                "runner_load_cost": r.get("runner_load_cost"),
                "size": r.get("size"),
                "status": r.get("status"),
            }
        )
    return typing.cast(list[dict], out)
