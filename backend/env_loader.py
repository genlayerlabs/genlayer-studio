"""Layered environment loading.

`.env.default` (tracked in git) is always loaded; `.env` (user overrides,
git-ignored) is loaded afterwards and wins. Variables already present in the
process environment win over both files. `${VAR}` / `${VAR:-fallback}`
references are expanded against the combined configuration, so a value in one
file may refer to a key defined (or overridden) in the other.
"""

import os
import re
from pathlib import Path

from dotenv import dotenv_values

ROOT_DIR = Path(__file__).resolve().parents[1]

_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand(value: str, lookup: dict[str, str], seen: tuple[str, ...] = ()) -> str:
    def replace(match: re.Match) -> str:
        name, fallback = match.group(1), match.group(2)
        if name in seen:  # circular reference: leave it empty rather than loop
            return fallback or ""
        resolved = lookup.get(name)
        if resolved is None or resolved == "":
            return fallback or ""
        return _expand(resolved, lookup, seen + (name,))

    return _REFERENCE.sub(replace, value)


def load_env(root: Path | None = None, override: bool = False) -> None:
    """Load `.env.default` then `.env` from `root`; os.environ wins unless `override`."""
    root = root or ROOT_DIR
    merged: dict[str, str] = {}
    for name in (".env.default", ".env"):
        path = root / name
        if path.is_file():
            for key, value in dotenv_values(path, interpolate=False).items():
                if value is not None:
                    merged[key] = value

    lookup = dict(merged)
    if not override:
        lookup.update(os.environ)
    for key, value in merged.items():
        if override or key not in os.environ:
            os.environ[key] = _expand(value, lookup)
