import os

from backend.env_loader import ROOT_DIR, load_env


def test_env_overrides_default_and_process_env_wins(tmp_path, monkeypatch):
    (tmp_path / ".env.default").write_text("A=default\nB=default\nC=default\n")
    (tmp_path / ".env").write_text("B=override\n")
    for key in "ABC":
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("C", "process")

    load_env(tmp_path)

    assert os.environ["A"] == "default"
    assert os.environ["B"] == "override"
    assert os.environ["C"] == "process"


def test_missing_env_file_is_fine(tmp_path, monkeypatch):
    (tmp_path / ".env.default").write_text("ONLY_DEFAULT=1\n")
    monkeypatch.delenv("ONLY_DEFAULT", raising=False)

    load_env(tmp_path)

    assert os.environ["ONLY_DEFAULT"] == "1"




def test_repository_defaults_file_exists():
    assert (ROOT_DIR / ".env.default").is_file()


def test_references_expand_across_files(tmp_path, monkeypatch):
    (tmp_path / ".env.default").write_text(
        "DXP25_PORT=4000\nDXP25_BASE=http://default:${DXP25_PORT}\n"
    )
    (tmp_path / ".env").write_text(
        "DXP25_URL=http://localhost:${DXP25_PORT}\nDXP25_MISSING=${NOPE_DXP25:-fb}\n"
    )
    for key in ("DXP25_PORT", "DXP25_BASE", "DXP25_URL", "DXP25_MISSING"):
        monkeypatch.delenv(key, raising=False)

    load_env(tmp_path)

    assert os.environ["DXP25_URL"] == "http://localhost:4000"
    assert os.environ["DXP25_BASE"] == "http://default:4000"
    assert os.environ["DXP25_MISSING"] == "fb"


def test_reference_uses_override_value(tmp_path, monkeypatch):
    (tmp_path / ".env.default").write_text("DXP25_P=4000\nDXP25_U=http://h:${DXP25_P}\n")
    (tmp_path / ".env").write_text("DXP25_P=4999\n")
    monkeypatch.delenv("DXP25_P", raising=False)
    monkeypatch.delenv("DXP25_U", raising=False)

    load_env(tmp_path)

    assert os.environ["DXP25_U"] == "http://h:4999"


def test_env_example_matches_env_default():
    # `.env.example` is kept for tooling that copies it (CLI, CI); it must not drift.
    assert (ROOT_DIR / ".env.example").read_text() == (
        ROOT_DIR / ".env.default"
    ).read_text()


def _hardhat_chain_id(root, env=None):
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        import pytest

        pytest.skip("node is not installed")
    out = subprocess.run(
        [
            node,
            "-e",
            "const {readChainIdFromDotenv: r} = require(process.argv[1]);"
            "console.log(JSON.stringify(r(process.argv[2])));",
            str(ROOT_DIR / "hardhat" / "read-dotenv.js"),
            str(root),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    import json

    return json.loads(out)


def test_hardhat_layer_beats_key_priority(tmp_path):
    (tmp_path / ".env.default").write_text("GENLAYER_CHAIN_ID='61127'\n")
    (tmp_path / ".env").write_text("HARDHAT_CHAIN_ID=61234  # override\n")
    assert _hardhat_chain_id(tmp_path) == "61234"


def test_hardhat_key_priority_within_layer(tmp_path):
    (tmp_path / ".env").write_text("HARDHAT_CHAIN_ID=7001\nGENLAYER_CHAIN_ID=\"7002\"\n")
    assert _hardhat_chain_id(tmp_path) == "7002"


def test_hardhat_falls_back_to_default_layer(tmp_path):
    (tmp_path / ".env.default").write_text("GENLAYER_CHAIN_ID='61127'\n")
    (tmp_path / ".env").write_text("OTHER=1\n")
    assert _hardhat_chain_id(tmp_path) == "61127"
    assert _hardhat_chain_id(tmp_path / "missing") is None
