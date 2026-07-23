"""Test the GENVM_DEBUG_MODE env-var gate around the genvm-manager
`debug_mode` run-request level.

The `unsafe` level enables `:latest` and `:test` runner version aliases
in the genvm executor. Those aliases float across deploys and break
determinism, so prd must have them disabled. The gate defaults to true
(dev/stg keeps the convenience via `unsafe`) but prd manifests should
set GENVM_DEBUG_MODE=false, which drops to the consensus-safe `safe`
level so the aliases fail fast.
"""

import importlib

import pytest


@pytest.fixture
def base_module():
    """Reload backend.node.base so each test sees fresh env-var
    evaluation. `_genvm_debug_mode` reads `os.getenv` at call time, so
    reload isn't strictly required — but it guards against any future
    module-level memoization regression."""
    from backend.node import base

    importlib.reload(base)
    return base


def test_debug_mode_enabled_by_default(monkeypatch, base_module):
    """Unset env var → 'unsafe' (dev/stg convenience: aliases + capture)."""
    monkeypatch.delenv("GENVM_DEBUG_MODE", raising=False)
    assert base_module._genvm_debug_mode() == "unsafe"


def test_debug_mode_enabled_when_true(monkeypatch, base_module):
    for value in ("true", "TRUE", "1", "yes", "on"):
        monkeypatch.setenv("GENVM_DEBUG_MODE", value)
        assert (
            base_module._genvm_debug_mode() == "unsafe"
        ), f"GENVM_DEBUG_MODE={value!r} should enable 'unsafe'"


def test_debug_mode_disabled_when_false(monkeypatch, base_module):
    """Prd setting: GENVM_DEBUG_MODE=false → 'safe' so the executor rejects
    `py-genlayer:9b8kjyda2ycxyq4ea6g4yfpnydxhd52gqba5rb8dw7krkh5mn9p0` / `:test` runner aliases.
    """
    for value in ("false", "FALSE", "0", "no", "off", "anything-else"):
        monkeypatch.setenv("GENVM_DEBUG_MODE", value)
        assert (
            base_module._genvm_debug_mode() == "safe"
        ), f"GENVM_DEBUG_MODE={value!r} should disable to 'safe'"
