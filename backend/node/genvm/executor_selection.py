"""Compatibility routing for the retained v0.2 text-contract executor."""

import datetime
import functools
import io
import json
import os
import re
import zipfile
from pathlib import Path

from . import chainforks

# Match the selector used to backfill pre-existing v0.2 contracts. Let the
# manager choose the newest available patch in this line, not a hardcoded
# directory or the newest active semver-major-0 executor.
LEGACY_EXECUTOR_SELECTOR = r"re:^v0\.2\."
LEGACY_LINE_ACTIVATION = chainforks.fork_activation("genvm/executor/v0.2")
_LEGACY_HEADER = re.compile(
    rb"(?:#|//|--)\s*v0\.2\.(?:[0-9]+|\*)(?:-[0-9A-Za-z.-]+)?\s*"
)
_COMMENT_PREFIXES = (b"//", b"#", b"--")
_MAX_RUNNER_JSON_BYTES = 64 * 1024


def legacy_executor_selector_for_code(code: bytes) -> str | None:
    """Select the retained v0.2 line for code that only it can run.

    An explicit v0.2 version in the first comment (GenVM reads #, // and --
    comments only at the start of the source) selects it. So does a runner
    spec (zip runner.json, else the leading comment block) declaring a runner
    shipped only by the retained line: the manager sends every other major-0
    contract to the newest line, which rejects such runners as malformed.
    Anything else keeps the normal manager selection, and GenVM remains
    responsible for validating code.
    """
    first_line = code.partition(b"\n")[0]
    if _LEGACY_HEADER.fullmatch(first_line):
        return LEGACY_EXECUTOR_SELECTOR
    legacy_only = _legacy_only_runners()
    if legacy_only and not legacy_only.isdisjoint(_declared_runners(code)):
        return LEGACY_EXECUTOR_SELECTOR
    return None


def uses_legacy_storage(selector: str | None) -> bool:
    """Recognize retained-line pins written by deployments and migration.

    Explicit version pins and the canonical line selector are stable storage
    identities. Do not guess the result of an arbitrary user regex: it may
    match several executor lines.
    """
    return bool(selector) and (
        selector == LEGACY_EXECUTOR_SELECTOR or selector.startswith("v0.2.")
    )


def clamp_to_legacy_line_activation(
    timestamp: datetime.datetime, selector: str | None
) -> datetime.datetime:
    """Keep a v0.2 line selector resolvable: the manager drops the line before its fork."""
    if selector == LEGACY_EXECUTOR_SELECTOR and timestamp < LEGACY_LINE_ACTIVATION:
        return LEGACY_LINE_ACTIVATION
    return timestamp


def _declared_runners(code: bytes) -> set[str]:
    """Runner ids named by `Depends` actions in the code's runner spec."""
    spec = _runner_spec(code)
    if spec is None:
        return set()
    try:
        parsed = json.loads(spec)
    except (ValueError, RecursionError):
        return set()
    return _collect_depends(parsed)


def _runner_spec(code: bytes) -> bytes | None:
    """Extract runner.json the way GenVM does: from a zip, else the header."""
    try:
        with zipfile.ZipFile(io.BytesIO(code)) as archive:
            info = archive.getinfo("runner.json")
            if info.file_size > _MAX_RUNNER_JSON_BYTES:
                return None
            return archive.read(info)
    except Exception:
        # Encrypted, unsupported or corrupt archives are GenVM's to reject.
        pass

    prefix = next((p for p in _COMMENT_PREFIXES if code.startswith(p)), None)
    if prefix is None:
        return None
    parts = []
    for index, line in enumerate(code.splitlines()):
        if not line.startswith(prefix):
            break
        body = line[len(prefix) :]
        if index == 0 and body.strip().startswith(b"v"):
            continue
        parts.append(body)
    return b"".join(parts)


def _collect_depends(root: object) -> set[str]:
    found: set[str] = set()
    pending = [root]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "Depends" and isinstance(value, str):
                    found.add(value)
                else:
                    pending.append(value)
        elif isinstance(node, list):
            pending.extend(node)
    return found


def _legacy_only_runners() -> frozenset[str]:
    root = os.environ.get("GENVMROOT")
    return _legacy_only_runners_in(root) if root else frozenset()


@functools.lru_cache(maxsize=4)
def _legacy_only_runners_in(root: str) -> frozenset[str]:
    """Runner ids in the installed v0.2 registries but in no newer line."""
    legacy: set[str] = set()
    newer: set[str] = set()
    for registry in Path(root).glob("executor/*/data/all.json"):
        try:
            runners = json.loads(registry.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(runners, dict):
            continue
        ids = {
            f"{name}:{runner_hash}"
            for name, entry in runners.items()
            for runner_hash in _registry_hashes(entry)
        }
        line = registry.parent.parent.name
        (legacy if line.startswith("v0.2.") else newer).update(ids)
    return frozenset(legacy - newer)


def _registry_hashes(entry: object) -> list[str]:
    # v0.2 lists hashes; v0.3+ maps them under "versions" next to "latest".
    if isinstance(entry, list):
        hashes = entry
    elif isinstance(entry, dict):
        versions = entry.get("versions")
        hashes = [*versions] if isinstance(versions, dict) else []
        hashes.append(entry.get("latest"))
    else:
        return []
    return [h for h in hashes if isinstance(h, str)]
