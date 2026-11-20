"""Layered environment loading.

`.env.default` (tracked in git) is always loaded; `.env` (user overrides,
git-ignored) is loaded afterwards and wins. Variables already present in the
process environment win over both files.
"""

import os
from pathlib import Path

from dotenv import dotenv_values

ROOT_DIR = Path(__file__).resolve().parents[1]


def load_env(root: Path | None = None, override: bool = False) -> None:
    """Load `.env.default` then `.env` from `root`; os.environ wins unless `override`."""
    root = root or ROOT_DIR
    merged: dict[str, str | None] = {}
    for name in (".env.default", ".env"):
        path = root / name
        if path.is_file():
            merged.update(dotenv_values(path))
    for key, value in merged.items():
        if value is not None and (override or key not in os.environ):
            os.environ[key] = value
