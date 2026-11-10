"""Compatibility routing for the retained v0.2 text-contract executor."""

import datetime
import functools
import io
import json
import os
import re
import typing
import zipfile
from pathlib import Path

from .origin import base_host

EXECUTOR_SELECTOR_REGEX_PREFIX: typing.Final = "re:"
"""Prefix marking a selector as a regex pattern, matching the manager's
`VERSION_REGEX_PREFIX` (genvm-manager crates/modules-interfaces/src/nested.rs)."""

# Only the manager shipping v0.4 accepts `create2`; its vendored host defaults it.
MANAGER_SPEAKS_CREATE2 = hasattr(base_host, "DEFAULT_CREATE2")
# First line whose SDK places salted children by the GhostFactory CREATE2 formula.
CREATE2_CHILD_ADDRESS_LINE: typing.Final = (0, 4)
_EXECUTOR_LINE_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(?:-[0-9A-Za-z.]+)?")

# Match the selector used to backfill pre-existing v0.2 contracts. Let the
# manager choose the newest available patch in this line, not a hardcoded
# directory or the newest semver-major-0 executor (which is v0.3).
LEGACY_EXECUTOR_SELECTOR = r"re:^v0\.2\."
# The "studio" network's genvm/executor/v0.2 date in genvm-manager's data/genvm-chainforks.json
LEGACY_LINE_ACTIVATION = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
_LEGACY_HEADER = re.compile(
    rb"(?:#|//|--)\s*v0\.2\.(?:[0-9]+|\*)(?:-[0-9A-Za-z.-]+)?\s*"
)
_COMMENT_PREFIXES = (b"//", b"#", b"--")
_MAX_RUNNER_JSON_BYTES = 64 * 1024


def legacy_executor_selector_for_code(code: bytes) -> str | None:
    """Honor an explicit v0.2 version in a text contract's first comment.

    GenVM recognizes #, // and -- comments only at the start of the source,
    with the version on the first line. Do not infer a version from a runner
    hash, imports, or later comments. Other formats/versions keep the normal
    manager selection, and GenVM remains responsible for validating code.
    """
    first_line = code.partition(b"\n")[0]
    if _LEGACY_HEADER.fullmatch(first_line):
        return LEGACY_EXECUTOR_SELECTOR
    return None


def legacy_executor_selector_for_child_code(code: bytes) -> str | None:
    """Select the retained v0.2 line for code a v0.2 contract deploys.

    New user deployments get the newest line, but contracts already pinned to
    v0.2 must keep deploying the code they hold (factories, shards). Besides a
    v0.2 header, that code may declare a runner shipped only by the retained
    line: the manager sends every other major-0 contract to the newest line,
    which rejects such runners as malformed.
    """
    if selector := legacy_executor_selector_for_code(code):
        return selector
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


def uses_create2_child_addresses(selector: str | None) -> bool:
    """Whether a contract pinned to `selector` derives salted children by CREATE2.

    Resolves the pin like the manager, against the installed executor lines but
    blind to its fork gating. An unresolvable selector assumes the newest line
    of the manager Studio talks to.
    """
    if uses_legacy_storage(selector):
        return False
    line = _executor_line(selector)
    if line is None:
        return MANAGER_SPEAKS_CREATE2
    return line >= CREATE2_CHILD_ADDRESS_LINE


def _executor_line(selector: str | None) -> tuple[int, int] | None:
    if selector and not selector.startswith(EXECUTOR_SELECTOR_REGEX_PREFIX):
        return _parse_line(selector)
    root = os.environ.get("GENVMROOT")
    installed = _installed_lines_in(root) if root else ()
    if selector:
        try:
            pattern = re.compile(selector[len(EXECUTOR_SELECTOR_REGEX_PREFIX) :])
        except re.error:
            return None
        installed = tuple(
            (version, name) for version, name in installed if pattern.search(name)
        )
    return max(installed)[0][:2] if installed else None


def _parse_line(version: str) -> tuple[int, int] | None:
    match = _EXECUTOR_LINE_RE.fullmatch(version)
    return (int(match[1]), int(match[2])) if match else None


@functools.lru_cache(maxsize=4)
def _installed_lines_in(root: str) -> tuple[tuple[tuple[int, int, int], str], ...]:
    lines = []
    for path in Path(root).glob("executor/*"):
        match = _EXECUTOR_LINE_RE.fullmatch(path.name)
        if match and path.is_dir():
            lines.append(((int(match[1]), int(match[2]), int(match[3])), path.name))
    return tuple(lines)


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
            for name, hashes in runners.items()
            if isinstance(hashes, list)
            for runner_hash in hashes
            if isinstance(runner_hash, str)
        }
        line = registry.parent.parent.name
        (legacy if line.startswith("v0.2.") else newer).update(ids)
    return frozenset(legacy - newer)
