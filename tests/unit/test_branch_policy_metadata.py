from pathlib import Path


def test_branching_guide_matches_active_train():
    root = Path(__file__).resolve().parents[2]
    active_branch = (root / "support/ci/ACTIVE_DEV_BRANCH").read_text().strip()
    guide = (root / "docs/BRANCHING.md").read_text().splitlines()

    assert active_branch.endswith("-dev")
    assert f"- Active integration branch: `{active_branch}`" in guide
    assert f"- Next stable target: `{active_branch.removesuffix('-dev')}`" in guide
