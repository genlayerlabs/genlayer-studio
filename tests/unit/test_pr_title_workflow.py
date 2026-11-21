"""Regression coverage for title validation after release-train retargeting."""

from fnmatch import fnmatchcase
from pathlib import Path

import pytest
import yaml


WORKFLOW = (
    Path(__file__).resolve().parents[2] / ".github/workflows/conventional-commit-pr.yml"
)


@pytest.mark.parametrize(
    "base", ["main", "v0.123-dev", "v0.124-dev", "v0.999-dev", "v0.123"]
)
@pytest.mark.parametrize("event", ["opened", "synchronize", "reopened", "edited"])
def test_title_validation_runs_for_release_train_prs(base, event):
    # BaseLoader preserves GitHub's `on` key instead of parsing it as a boolean.
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    trigger = workflow["on"]["pull_request"]

    assert event in trigger["types"]
    assert any(fnmatchcase(base, pattern) for pattern in trigger.get("branches", ["*"]))
    assert not any(
        fnmatchcase(base, pattern) for pattern in trigger.get("branches-ignore", [])
    )
    assert "paths" not in trigger
    assert "paths-ignore" not in trigger
