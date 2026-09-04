"""Nested cross-major call routing: `Host.resolve_call_contract_executor`.

A call target that is pinned to an executor version (its snapshot carries a
`reroute_to`) answers the genvm's resolve query with that version, so the callee
keeps running on its own line. Unpinned targets answer None.
"""

import pytest

import backend.node.genvm.origin.calldata as gvm_calldata
from backend.node.genvm.base import Host
from backend.node.genvm.origin import base_host, host_fns
from backend.node.genvm.origin.public_abi import StorageView
from backend.node.types import Address


class _FakeStateProxy:
    def __init__(self, pins: dict[str, str | None]):
        self._pins = pins

    def storage_read(self, *_args):  # pragma: no cover - unused here
        raise AssertionError("storage_read should not be called")

    def get_balance(self, _addr):  # pragma: no cover - unused here
        raise AssertionError("get_balance should not be called")

    def genvm_executor_selector_for(self, addr: Address) -> str | None:
        return self._pins.get(addr.as_hex.lower())


ADDR_PINNED = Address("0x" + "11" * 20)
ADDR_UNPINNED = Address("0x" + "22" * 20)


def _host(pins):
    host = Host.__new__(Host)
    host._state_proxy = _FakeStateProxy(pins)
    return host


@pytest.mark.asyncio
async def test_resolve_names_the_pinned_line_itself():
    # Not a major: every line released so far is semver major 0, so a major
    # would resolve to the newest line whichever one the pin meant.
    host = _host({ADDR_PINNED.as_hex.lower(): "v0.2.17"})
    res = await host.resolve_call_contract_executor(ADDR_PINNED, StorageView.DEFAULT, 6)
    assert res is not None
    assert gvm_calldata.decode(res) == {"kind": "version", "version": "v0.2.17"}


@pytest.mark.asyncio
async def test_resolve_returns_none_for_unpinned_target():
    host = _host({ADDR_PINNED.as_hex.lower(): "v0.2.17"})
    res = await host.resolve_call_contract_executor(
        ADDR_UNPINNED, StorageView.DEFAULT, 6
    )
    assert res is None


@pytest.mark.asyncio
async def test_resolve_reports_an_unusable_stored_pin_as_a_host_error():
    # Submit-time validation keeps these out of the database, so reaching here
    # means a stored pin went bad. It has to come back as a host error the
    # genvm can fail the call on: a bare exception escapes `host_loop_on`, kills
    # the host task, and the retry loop then re-runs the contract until the
    # transaction's time budget is gone.
    host = _host({ADDR_PINNED.as_hex.lower(): "banana"})
    with pytest.raises(base_host.HostException) as exc:
        await host.resolve_call_contract_executor(ADDR_PINNED, StorageView.DEFAULT, 6)
    assert exc.value.error_code != host_fns.Errors.OK


@pytest.mark.asyncio
async def test_resolve_treats_empty_pin_as_unpinned():
    host = _host({ADDR_PINNED.as_hex.lower(): ""})
    res = await host.resolve_call_contract_executor(ADDR_PINNED, StorageView.DEFAULT, 6)
    assert res is None


@pytest.mark.asyncio
async def test_resolve_accepts_a_regex_selector():
    # Same grammar the migration backfill writes for pre-existing v0.2
    # contracts: a `re:`-prefixed pattern, not an exact version.
    host = _host({ADDR_PINNED.as_hex.lower(): r"re:^v0\.2\."})
    res = await host.resolve_call_contract_executor(ADDR_PINNED, StorageView.DEFAULT, 6)
    assert res is not None
    assert gvm_calldata.decode(res) == {"kind": "version", "version": r"re:^v0\.2\."}


@pytest.mark.asyncio
async def test_resolve_rejects_a_stored_pin_with_an_invalid_regex():
    host = _host({ADDR_PINNED.as_hex.lower(): "re:("})
    with pytest.raises(base_host.HostException) as exc:
        await host.resolve_call_contract_executor(ADDR_PINNED, StorageView.DEFAULT, 6)
    assert exc.value.error_code != host_fns.Errors.OK
