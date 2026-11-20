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


def test_override_flag_replaces_process_env(tmp_path, monkeypatch):
    (tmp_path / ".env.default").write_text("D=file\n")
    monkeypatch.setenv("D", "process")

    load_env(tmp_path, override=True)

    assert os.environ["D"] == "file"


def test_repository_defaults_file_exists():
    assert (ROOT_DIR / ".env.default").is_file()
