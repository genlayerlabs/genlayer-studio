"""Compatibility routing for the retained v0.2 text-contract executor."""

import re

# Match the selector used to backfill pre-existing v0.2 contracts. Let the
# manager choose the newest available patch in this line, not a hardcoded
# directory or the newest semver-major-0 executor (which is v0.3).
LEGACY_EXECUTOR_SELECTOR = r"re:^v0\.2\."
_LEGACY_HEADER = re.compile(
    rb"(?:#|//|--)\s*v0\.2\.(?:[0-9]+|\*)(?:-[0-9A-Za-z.-]+)?\s*"
)


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


def uses_legacy_storage(selector: str | None) -> bool:
    """Recognize retained-line pins written by deployments and migration.

    Explicit version pins and the canonical line selector are stable storage
    identities. Do not guess the result of an arbitrary user regex: it may
    match several executor lines.
    """
    return bool(selector) and (
        selector == LEGACY_EXECUTOR_SELECTOR or selector.startswith("v0.2.")
    )
