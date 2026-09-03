import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile.backend"


def _stages(dockerfile: str) -> dict[str, str]:
    stages: dict[str, str] = {}
    for parent, name in re.findall(
        r"^FROM\s+(\S+)\s+AS\s+(\S+)\s*$", dockerfile, re.MULTILINE | re.IGNORECASE
    ):
        stages[name] = parent
    return stages


def _stage_body(dockerfile: str, stage: str) -> str:
    marker = re.search(
        rf"^FROM\s+\S+\s+AS\s+{re.escape(stage)}\s*$",
        dockerfile,
        re.MULTILINE | re.IGNORECASE,
    )
    assert marker is not None
    body = dockerfile[marker.end() :]
    return re.split(r"^FROM\s+", body, maxsplit=1, flags=re.MULTILINE)[0]


def _arg_defaults(stage: str) -> dict[str, str | None]:
    return {
        name: default
        for name, default in re.findall(
            r"^ARG\s+([A-Z][A-Z0-9_]*)(?:=(.*))?$", stage, re.MULTILINE
        )
    }


def test_backend_services_share_one_genvm_parent():
    dockerfile = DOCKERFILE.read_text()
    stages = _stages(dockerfile)

    assert stages["service-base"] == "genvm-runtime"
    assert stages["prod"] == "service-base"
    assert stages["debug"] == "service-base"
    assert stages["consensus-worker"] == "service-base"
    assert stages["base"] == "consensus-worker"

    # Acquisition belongs to the shared parent, never to a service target.
    assert dockerfile.count("download-genvm linux") == 1
    assert dockerfile.count("COPY .e2e-genvm-prebuilt/ /genvm-prebuilt/") == 1
    assert dockerfile.count("nix build -o /out-genvm") == 1
    assert not (REPO_ROOT / "docker" / "Dockerfile.consensus-worker").exists()
    assert list(stages)[-1] == "prod"
    assert "USER backend-user" in dockerfile
    assert "USER worker-user" in dockerfile


def test_default_genvm_binding_is_loaded_in_both_build_stages():
    dockerfile = DOCKERFILE.read_text()
    source_stage = _stage_body(dockerfile, "genvm-source-build")
    runtime_stage = _stage_body(dockerfile, "genvm-runtime")
    prepare_script = (
        REPO_ROOT / "scripts" / "prepare-genvm-source-build.sh"
    ).read_text()

    assert (
        dockerfile.count("COPY third_party/genvm/version /genvm-default-version") == 2
    )
    for stage in (source_stage, runtime_stage):
        assert 'effective_ref="$(< /genvm-default-version)"' in stage
        assert '"$effective_ref" =~ ^.+:[0-9a-fA-F]{7,40}$' in stage
    assert 'GENVM_REF="$(< "$REPO_ROOT/third_party/genvm/version")"' in prepare_script


def test_release_backend_builds_use_an_immutable_genvm_release():
    workflow = yaml.safe_load(
        (
            REPO_ROOT / ".github" / "workflows" / "docker-build-and-push-all.yml"
        ).read_text()
    )
    default_binding = (
        (REPO_ROOT / "third_party" / "genvm" / "version").read_text().strip()
    )

    # The distributed release jobs do not prepare or transfer the sandboxed
    # Nix closure required by a branch:SHA source binding. With no build args,
    # Docker falls back to the checked-in binding, so it must name a published,
    # immutable GenVM release. This catches release-image failures before E2E.
    for job_name in ("jsonrpc", "consensus-worker"):
        assert "build_args" not in workflow["jobs"][job_name]["with"]
    assert re.fullmatch(
        r"v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.]+)?",
        default_binding,
    ), (
        "release backend jobs provide no GenVM override; "
        "third_party/genvm/version must be an immutable release tag, not a source ref"
    )


def test_release_builds_define_optional_genvm_args_before_nounset_shells():
    dockerfile = DOCKERFILE.read_text()
    workflow = yaml.safe_load(
        (
            REPO_ROOT / ".github" / "workflows" / "docker-build-and-push-all.yml"
        ).read_text()
    )
    optional_args = {
        "GENVM_SOURCE_MODE",
        "GENVM_TAG",
        "GENVM_REF",
        "GENVM_EXECUTOR_VERSION_NAME",
    }

    # The release workflow intentionally provides no GenVM build args. Every
    # optional value read by a `set -u` RUN must therefore have a Dockerfile
    # default, or a clean release build fails before selecting its pinned GenVM.
    for job_name in ("jsonrpc", "consensus-worker"):
        assert "build_args" not in workflow["jobs"][job_name]["with"]

    for stage_name in ("genvm-source-build", "genvm-runtime"):
        defaults = _arg_defaults(_stage_body(dockerfile, stage_name))
        assert optional_args <= defaults.keys()
        assert {defaults[name] for name in optional_args} == {'""'}


def test_debug_target_installs_debugpy_with_the_pip_cache():
    dockerfile = DOCKERFILE.read_text()
    debug_stage = _stage_body(dockerfile, "debug")

    assert "--mount=type=cache,target=/root/.cache/pip" in debug_stage
    assert "pip install --cache-dir=/root/.cache/pip debugpy" in debug_stage
    assert dockerfile.count("pip install --cache-dir=/root/.cache/pip debugpy") == 1


def test_compose_builds_both_services_from_the_shared_dockerfile():
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
    jsonrpc = compose["services"]["jsonrpc"]["build"]
    worker = compose["services"]["consensus-worker"]["build"]

    assert Path(jsonrpc["dockerfile"]).name == "Dockerfile.backend"
    assert Path(worker["dockerfile"]).name == "Dockerfile.backend"
    assert jsonrpc["target"] == "prod"
    assert worker["target"].endswith(":-consensus-worker}")


def test_legacy_consensus_base_target_is_a_zero_cost_alias():
    dockerfile = DOCKERFILE.read_text()

    assert _stage_body(dockerfile, "base").strip() == ""


def test_release_builds_select_the_shared_service_targets():
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "docker-build-and-push-all.yml"
    ).read_text()

    assert workflow.count("dockerfile: docker/Dockerfile.backend") == 2
    assert "target: prod" in workflow
    assert "target: consensus-worker" in workflow
    assert "Dockerfile.consensus-worker" not in workflow
