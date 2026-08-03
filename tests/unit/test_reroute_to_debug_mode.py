"""`genvm_executor_selector` is only honored by the genvm manager under
`debug_mode >= safe`.

Below that the manager ignores it *silently* and runs the manifest-resolved
executor instead, so both the RPC entry point and the run path refuse the
request rather than executing on the wrong executor.
"""

import asyncio

import pytest

from backend.node.genvm.base import run_genvm_host
from backend.protocol_rpc.endpoints import (
    _reject_genvm_executor_selector_unless_deploy,
    _validate_genvm_executor_selector,
)
from backend.protocol_rpc.exceptions import JSONRPCError


def _run_with(genvm_executor_selector, **kwargs):
    return asyncio.run(
        run_genvm_host(
            lambda sock: None,
            timeout=1,
            is_sync=True,
            message={},
            genvm_executor_selector=genvm_executor_selector,
            **kwargs,
        )
    )


def test_run_genvm_host_rejects_reroute_to_with_debug_mode_disabled():
    with pytest.raises(ValueError, match="requires debug_mode >= safe"):
        _run_with("v0.2.17", debug_mode="disabled")


def test_run_genvm_host_rejects_reroute_to_when_capture_off_implies_disabled():
    # debug_mode unset + capture_output=False resolves to "disabled" in base_host
    with pytest.raises(ValueError, match="requires debug_mode >= safe"):
        _run_with("v0.2.17", capture_output=False)


def test_validate_reroute_to_allows_missing_value(monkeypatch):
    monkeypatch.setenv("GENVM_DEBUG_MODE", "false")
    _validate_genvm_executor_selector(None)
    _validate_genvm_executor_selector({})
    _validate_genvm_executor_selector({"genvm_executor_selector": None})


def test_validate_reroute_to_allows_value_in_debug_mode(monkeypatch):
    monkeypatch.setenv("GENVM_DEBUG_MODE", "true")
    _validate_genvm_executor_selector({"genvm_executor_selector": "v0.2.17"})


def test_validate_reroute_to_rejects_value_without_debug_mode(monkeypatch):
    monkeypatch.setattr(
        "backend.protocol_rpc.endpoints._genvm_debug_mode", lambda: "disabled"
    )
    with pytest.raises(JSONRPCError) as exc:
        _validate_genvm_executor_selector({"genvm_executor_selector": "v0.2.17"})
    assert exc.value.code == -32602


@pytest.mark.parametrize("mode", ["safe", "safe-unbounded", "unsafe", "unsafe-tracing"])
def test_validate_reroute_to_allows_every_level_the_manager_honors(monkeypatch, mode):
    # The manager gates the pin on `debug_mode >= Safe`, so every level above it
    # must pass -- `safe-unbounded` in particular is what studio's own run path
    # resolves to whenever output is captured.
    monkeypatch.setattr(
        "backend.protocol_rpc.endpoints._genvm_debug_mode", lambda: mode
    )
    _validate_genvm_executor_selector({"genvm_executor_selector": "v0.2.17"})


@pytest.mark.parametrize(
    "value",
    [
        "banana",
        "",
        "v",
        "../../../etc",
        "v0.2.17/../../elsewhere",
        "v0.2.17 ",
        "v0.2.17\n",
        42,
    ],
)
def test_validate_reroute_to_rejects_values_that_are_not_versions(monkeypatch, value):
    # The pin is persisted and only read back mid-run, and the manager uses it
    # verbatim as the executor directory name. A value that is not a version is
    # a rejected transaction, not a contract that fails every call it takes part
    # in and not a path below the executors root.
    monkeypatch.setenv("GENVM_DEBUG_MODE", "true")
    sim_config = {"genvm_executor_selector": value}
    if not value:
        # Falsy values mean "unpinned" and are simply ignored.
        _validate_genvm_executor_selector(sim_config)
        return
    with pytest.raises(JSONRPCError) as exc:
        _validate_genvm_executor_selector(sim_config)
    assert exc.value.code == -32602


@pytest.mark.parametrize("value", [0, False, [], {}])
def test_validate_reroute_to_rejects_non_string_falsy_values(monkeypatch, value):
    # Only a missing key, None, or "" mean "unset". A non-string falsy value
    # like 0/False/[]/{} must not be silently treated the same way -- it has
    # to reach the type check and get rejected.
    monkeypatch.setenv("GENVM_DEBUG_MODE", "true")
    with pytest.raises(JSONRPCError) as exc:
        _validate_genvm_executor_selector({"genvm_executor_selector": value})
    assert exc.value.code == -32602


@pytest.mark.parametrize("value", [0, False, [], {}])
def test_reject_reroute_to_unless_deploy_rejects_non_string_falsy_values(value):
    with pytest.raises(JSONRPCError) as exc:
        _reject_genvm_executor_selector_unless_deploy(
            {"genvm_executor_selector": value}, is_deploy=False
        )
    assert exc.value.code == -32602


def test_validate_reroute_to_accepts_prerelease_versions(monkeypatch):
    monkeypatch.setenv("GENVM_DEBUG_MODE", "true")
    _validate_genvm_executor_selector({"genvm_executor_selector": "v0.6.0-rc2"})


def test_validate_reroute_to_accepts_a_regex_selector(monkeypatch):
    # Same grammar the manager accepts (`re:`-prefixed pattern), and what the
    # migration backfill writes for pre-existing v0.2 contracts.
    monkeypatch.setenv("GENVM_DEBUG_MODE", "true")
    _validate_genvm_executor_selector({"genvm_executor_selector": r"re:^v0\.2\."})


def test_validate_reroute_to_rejects_an_invalid_regex_selector(monkeypatch):
    monkeypatch.setenv("GENVM_DEBUG_MODE", "true")
    with pytest.raises(JSONRPCError) as exc:
        _validate_genvm_executor_selector({"genvm_executor_selector": "re:("})
    assert exc.value.code == -32602


def test_reject_reroute_to_unless_deploy_allows_it_on_deploy():
    _reject_genvm_executor_selector_unless_deploy(
        {"genvm_executor_selector": "v0.2.17"}, is_deploy=True
    )


def test_reject_reroute_to_unless_deploy_allows_missing_value_anywhere():
    _reject_genvm_executor_selector_unless_deploy(None, is_deploy=False)
    _reject_genvm_executor_selector_unless_deploy({}, is_deploy=False)
    _reject_genvm_executor_selector_unless_deploy(
        {"genvm_executor_selector": None}, is_deploy=False
    )


def test_reject_reroute_to_unless_deploy_rejects_it_on_non_deploy():
    # Accepting it here and silently dropping it would look like the pin
    # took effect when it never reached the manager at all.
    with pytest.raises(JSONRPCError) as exc:
        _reject_genvm_executor_selector_unless_deploy(
            {"genvm_executor_selector": "v0.2.17"}, is_deploy=False
        )
    assert exc.value.code == -32602
