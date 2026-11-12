import asyncio
import datetime
import json
from pathlib import Path

import pytest

from backend.node import base as node_base
from backend.node.genvm import base as genvm_base
from backend.node.genvm import chainforks, executor_selection

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEDULE = REPO_ROOT / "backend" / "node" / "genvm" / "chainforks.json"
EXECUTOR_FORKS = {
    "genvm/executor/v0.2",
    "genvm/executor/v0.3",
    "genvm/executor/v0.4",
}


def _write_schedule(path: Path, activation) -> Path:
    path.write_text(
        json.dumps(
            {
                "forks": {"genvm/executor/v0.2": {}},
                "networks": {"studio": {"forks": {"genvm/executor/v0.2": activation}}},
            }
        )
    )
    return path


def test_schedule_path_is_absolute_and_next_to_the_source(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    assert chainforks.CHAINFORKS_PATH == SCHEDULE
    assert chainforks.CHAINFORKS_PATH.is_absolute()
    assert chainforks.CHAINFORKS_PATH.is_file()


def test_schedule_defines_studio_network_with_every_executor_fork():
    schedule = json.loads(SCHEDULE.read_text())

    assert schedule["$schema"] == (
        "https://raw.githubusercontent.com/genlayerlabs/genvm-manager/refs/heads/main"
        "/docs/schemas/genvm-chainforks.json"
    )
    assert set(schedule["forks"]) == EXECUTOR_FORKS
    assert schedule["networks"] == {
        chainforks.GENVM_NETWORK: {
            "forks": {
                "genvm/executor/v0.2": "2026-01-01T00:00:00Z",
                "genvm/executor/v0.3": "always",
                "genvm/executor/v0.4": "always",
            }
        }
    }


def test_runs_target_the_scheduled_network():
    assert genvm_base.GENVM_NETWORK == chainforks.GENVM_NETWORK == "studio"


def test_manager_args_hand_over_schedule_and_network():
    assert chainforks.manager_args() == [
        "--chainforks-path",
        str(SCHEDULE),
        "--default-network",
        "studio",
    ]


def test_check_install_argv():
    assert chainforks.check_install_argv(Path("/genvm")) == [
        "/genvm/bin/genvm-modules",
        "manager",
        "check-install",
        "--chainforks-path",
        str(SCHEDULE),
        "--default-network",
        "studio",
    ]


def test_legacy_line_activation_is_read_from_the_schedule():
    assert executor_selection.LEGACY_LINE_ACTIVATION == datetime.datetime(
        2026, 1, 1, tzinfo=datetime.UTC
    )


@pytest.mark.parametrize(
    ("activation", "expected"),
    [
        ("always", datetime.datetime.min.replace(tzinfo=datetime.UTC)),
        (
            "2026-03-04T05:06:07.5+02:00",
            datetime.datetime(
                2026,
                3,
                4,
                5,
                6,
                7,
                500000,
                tzinfo=datetime.timezone(datetime.timedelta(hours=2)),
            ),
        ),
    ],
)
def test_fork_activation_values(tmp_path, activation, expected):
    path = _write_schedule(tmp_path / "s.json", activation)

    assert chainforks.fork_activation("genvm/executor/v0.2", path=path) == expected


@pytest.mark.parametrize(
    "activation",
    [
        "never",
        "2026-01-01T00:00:00",
        "2026-01-01 00:00:00Z",
        "2026-01-01T00:00Z",
        "20260101T000000Z",
        "2026-02-30T00:00:00Z",
        # Finer than datetime resolves: clamping would land before the manager's fork
        "2026-01-01T00:00:00.0000001Z",
        "tomorrow",
        5,
        None,
    ],
)
def test_fork_activation_rejects_unusable_values(tmp_path, activation):
    path = _write_schedule(tmp_path / "s.json", activation)

    with pytest.raises(ValueError):
        chainforks.fork_activation("genvm/executor/v0.2", path=path)


@pytest.mark.parametrize(
    ("fork", "network", "content"),
    [
        ("genvm/executor/v0.9", "studio", None),
        ("genvm/executor/v0.2", "dev", None),
        ("genvm/executor/v0.2", "studio", "{not json"),
        ("genvm/executor/v0.2", "studio", "[]"),
    ],
)
def test_fork_activation_rejects_missing_entries(tmp_path, fork, network, content):
    path = _write_schedule(tmp_path / "s.json", "always")
    if content is not None:
        path.write_text(content)

    with pytest.raises(ValueError):
        chainforks.fork_activation(fork, path=path, network=network)


def test_fork_activation_rejects_missing_file(tmp_path):
    with pytest.raises(ValueError):
        chainforks.fork_activation(
            "genvm/executor/v0.2", path=tmp_path / "missing.json"
        )


class _FakeProc:
    def __init__(self, returncode: int, output: bytes = b""):
        self.returncode = returncode
        self._output = output

    async def communicate(self):
        return self._output, None


@pytest.fixture
def genvm_root(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    (config / "genvm-module-llm.yaml").write_text("{}")
    (config / "genvm-module-web.yaml").write_text("{}")
    monkeypatch.setenv("GENVMROOT", str(tmp_path))
    return tmp_path


def _record_spawns(monkeypatch, check_returncode: int):
    spawned: list[list[str]] = []

    async def fake_exec(*argv, **_kwargs):
        argv = [str(a) for a in argv]
        spawned.append(argv)
        if "check-install" in argv:
            return _FakeProc(check_returncode, b"executor check failed")
        return _FakeProc(None)

    monkeypatch.setattr(asyncio.subprocess, "create_subprocess_exec", fake_exec)
    return spawned


@pytest.mark.asyncio
async def test_manager_checks_install_then_serves_studio_schedule(
    monkeypatch, genvm_root
):
    spawned = _record_spawns(monkeypatch, check_returncode=0)

    await node_base.Manager.create()

    exe = str(genvm_root / "bin" / "genvm-modules")
    assert spawned == [
        chainforks.check_install_argv(genvm_root),
        [
            exe,
            "manager",
            "--port",
            "3999",
            "--die-with-parent",
            *chainforks.manager_args(),
        ],
    ]


@pytest.mark.asyncio
async def test_manager_refuses_to_start_when_check_install_fails(
    monkeypatch, genvm_root
):
    spawned = _record_spawns(monkeypatch, check_returncode=3)

    with pytest.raises(RuntimeError, match="exit code 3") as e:
        await node_base.Manager.create()

    assert "executor check failed" in str(e.value)
    assert len(spawned) == 1


@pytest.mark.asyncio
async def test_manager_refuses_to_start_when_check_install_hangs(
    monkeypatch, genvm_root
):
    killed = []

    class _HangingProc:
        returncode = None

        async def communicate(self):
            await asyncio.Event().wait()

        def kill(self):
            killed.append(True)

        async def wait(self):
            return -9

    spawned = []

    async def fake_exec(*argv, **_kwargs):
        spawned.append(argv)
        return _HangingProc()

    monkeypatch.setattr(asyncio.subprocess, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(node_base, "_CHECK_INSTALL_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(RuntimeError, match="timed out"):
        await node_base.Manager.create()

    assert killed == [True]
    assert len(spawned) == 1
