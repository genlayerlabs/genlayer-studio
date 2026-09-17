import subprocess
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
POLICY_SCRIPT = REPOSITORY_ROOT / ".github/scripts/resolve-release-policy.sh"


def resolve(tag: str, field: str) -> str:
    return subprocess.run(
        [str(POLICY_SCRIPT), tag, field],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_stable_release_uses_stable_branch_latest_and_release_lane():
    assert resolve("v0.123.0", "version-branch") == "v0.123"
    assert resolve("v0.123.0", "channel") == "stable"
    assert resolve("v0.123.0", "push-latest") == "true"
    assert resolve("v0.123.0", "deployment-event") == "release-backend"


def test_prerelease_uses_dev_branch_without_latest_and_preview_lane():
    assert resolve("v0.123.0-rc.1", "version-branch") == "v0.123-dev"
    assert resolve("v0.123.0-rc.1", "channel") == "preview"
    assert resolve("v0.123.0-rc.1", "push-latest") == "false"
    assert resolve("v0.123.0-rc.1", "deployment-event") == "release-backend-preview"


def test_invalid_release_tags_are_rejected():
    result = subprocess.run(
        [str(POLICY_SCRIPT), "v0.123", "version-branch"],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "Invalid release tag" in result.stderr


def test_release_workflow_bakes_version_and_uses_resolved_policy():
    release_workflow = (
        REPOSITORY_ROOT / ".github/workflows/release-from-tag.yml"
    ).read_text()
    build_workflow = (
        REPOSITORY_ROOT / ".github/workflows/docker-build-and-push-all.yml"
    ).read_text()
    image_workflow = (
        REPOSITORY_ROOT / ".github/workflows/docker-build-and-push-image.yml"
    ).read_text()
    frontend_dockerfile = (REPOSITORY_ROOT / "docker/Dockerfile.frontend").read_text()

    frontend_job = build_workflow.split("\n  frontend:", 1)[1].split(
        "\n  database-migration:", 1
    )[0]
    jsonrpc_job = build_workflow.split("\n  jsonrpc:", 1)[1].split("\n  frontend:", 1)[
        0
    ]

    assert "resolve-release-policy.sh" in release_workflow
    assert (
        "push_latest: ${{ needs.validate-tag.outputs.push_latest == 'true' }}"
        in release_workflow
    )
    assert '-f "event_type=${DEPLOYMENT_EVENT}"' in release_workflow
    assert "VITE_APP_VERSION=${{ inputs.image_tag }}" in frontend_job
    assert "VITE_APP_VERSION" not in jsonrpc_job
    assert '[[ "${IMAGE_TAG}" =~ ^v[0-9]+\\.[0-9]+\\.[0-9]+- ]]' in image_workflow
    assert 'ARG VITE_APP_VERSION=""' in frontend_dockerfile
