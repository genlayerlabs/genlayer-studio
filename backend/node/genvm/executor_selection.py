"""Compatibility routing for the retained v0.2 text-contract executor."""

import functools
import io
import json
import os
import re
import zipfile
from pathlib import Path

# Match the selector used to backfill pre-existing v0.2 contracts. Let the
# manager choose the newest available patch in this line, not a hardcoded
# directory or the newest semver-major-0 executor (which is v0.3).
LEGACY_EXECUTOR_SELECTOR = r"re:^v0\.2\."
_LEGACY_HEADER = re.compile(
    rb"(?:#|//|--)\s*v0\.2\.(?:[0-9]+|\*)(?:-[0-9A-Za-z.-]+)?\s*"
)
_COMMENT_PREFIXES = (b"//", b"#", b"--")
_MAX_RUNNER_JSON_BYTES = 64 * 1024


def legacy_executor_selector_for_code(code: bytes) -> str | None:
    """Select the retained v0.2 line for code that only it can run.

    That is a v0.2 first-line version, or a declared runner shipped only by
    the retained line: the manager sends every other major-0 contract to the
    newest line, which rejects such runners as malformed. Other code keeps the
    normal manager selection, and GenVM remains responsible for validating it.
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


def _declared_runners(code: bytes) -> set[str]:
    """Runner ids named by `Depends` actions in the code's runner spec."""
    spec = _runner_spec(code)
    if spec is None:
        return set()
    try:
        parsed = json.loads(spec)
    except ValueError:
        return set()
    found: set[str] = set()
    _collect_depends(parsed, found)
    return found


def _runner_spec(code: bytes) -> bytes | None:
    """Extract runner.json the way GenVM does: from a zip, else the header."""
    try:
        with zipfile.ZipFile(io.BytesIO(code)) as archive:
            info = archive.getinfo("runner.json")
            if info.file_size > _MAX_RUNNER_JSON_BYTES:
                return None
            return archive.read(info)
    except (zipfile.BadZipFile, KeyError, ValueError, OSError, EOFError):
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


def _collect_depends(node: object, found: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "Depends" and isinstance(value, str):
                found.add(value)
            else:
                _collect_depends(value, found)
    elif isinstance(node, list):
        for item in node:
            _collect_depends(item, found)


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
            for name, hashes in runners.items()
            if isinstance(hashes, list)
            for runner_hash in hashes
            if isinstance(runner_hash, str)
        }
        line = registry.parent.parent.name
        (legacy if line.startswith("v0.2.") else newer).update(ids)
    return frozenset(legacy - newer)
