"""Test the GENVM_DEBUG_MODE env-var gate around the executor's
`--debug-mode` flag.

`--debug-mode` enables `:latest` and `:test` runner version aliases in
the genvm executor. Those aliases float across deploys and break
determinism, so prd must have them disabled. The gate defaults to true
(dev/stg keeps the convenience) but prd manifests should set
GENVM_DEBUG_MODE=false.
"""

import importlib

import pytest


@pytest.fixture
def base_module():
    """Reload backend.node.base so each test sees fresh env-var
    evaluation. `_genvm_extra_args` reads `os.getenv` at call time, so
    reload isn't strictly required — but it guards against any future
    module-level memoization regression."""
    from backend.node import base

    importlib.reload(base)
    return base


def test_debug_mode_enabled_by_default(monkeypatch, base_module):
    """Unset env var → include --debug-mode (dev/stg convenience)."""
    monkeypatch.delenv("GENVM_DEBUG_MODE", raising=False)
    assert base_module._genvm_extra_args() == ["--debug-mode"]


def test_debug_mode_enabled_when_true(monkeypatch, base_module):
    for value in ("true", "TRUE", "1", "yes", "on"):
        monkeypatch.setenv("GENVM_DEBUG_MODE", value)
        assert base_module._genvm_extra_args() == [
            "--debug-mode"
        ], f"GENVM_DEBUG_MODE={value!r} should enable --debug-mode"


def test_debug_mode_disabled_when_false(monkeypatch, base_module):
    """Prd setting: GENVM_DEBUG_MODE=false strips --debug-mode so the
    executor rejects `py-genlayer:latest` / `:test` runner aliases."""
    for value in ("false", "FALSE", "0", "no", "off", "anything-else"):
        monkeypatch.setenv("GENVM_DEBUG_MODE", value)
        assert (
            base_module._genvm_extra_args() == []
        ), f"GENVM_DEBUG_MODE={value!r} should disable --debug-mode"
