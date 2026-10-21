"""`StorageView.LATEST_FINALIZED` reads, e.g. `view(state=LATEST_FINALIZED)`.

Served from the finalized state; slots with no finalized value read as zeroes.
"""

import base64
from unittest.mock import MagicMock

import pytest

from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.node.base import _SnapshotView, _StateProxyNone
from backend.node.genvm.base import Host
from backend.node.genvm.origin.public_abi import StorageView
from backend.node.types import Address

PRIMARY = "0x" + "aa" * 20
OTHER = "0x" + "bb" * 20
SLOT = b"\x07" * 32
SLOT_KEY = base64.b64encode(SLOT).decode("ascii")


def _raw(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _snapshot(address: str, states: dict) -> ContractSnapshot:
    snapshot = MagicMock(spec=ContractSnapshot)
    snapshot.contract_address = address
    snapshot.states = states
    snapshot.balance = 0
    return snapshot


def _host(primary_states: dict, other_states: dict | None = None) -> Host:
    other = _snapshot(OTHER, other_states if other_states is not None else {})
    host = Host.__new__(Host)
    host._state_proxy = _SnapshotView(
        _snapshot(PRIMARY, primary_states),
        lambda _addr: other,
        readonly=True,
        shared_decoded_value_cache={},
    )
    return host


async def _read(host: Host, view: StorageView, addr: str, le: int, offset: int = 0):
    return await host.storage_read(view, Address(addr).as_bytes, SLOT, offset, le)


@pytest.mark.asyncio
async def test_cross_contract_reads_finalized_state():
    host = _host(
        {},
        {
            "accepted": {SLOT_KEY: _raw(b"accepted")},
            "finalized": {SLOT_KEY: _raw(b"final")},
        },
    )
    assert await _read(host, StorageView.LATEST_FINALIZED, OTHER, 5) == b"final"


@pytest.mark.asyncio
async def test_primary_contract_reads_finalized_state():
    host = _host(
        {
            "accepted": {SLOT_KEY: _raw(b"accepted")},
            "finalized": {SLOT_KEY: _raw(b"final")},
        }
    )
    assert await _read(host, StorageView.LATEST_FINALIZED, PRIMARY, 5) == b"final"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "other_states",
    [
        pytest.param(
            {"accepted": {SLOT_KEY: _raw(b"accepted")}}, id="no-finalized-state"
        ),
        pytest.param(
            {"accepted": {SLOT_KEY: _raw(b"accepted")}, "finalized": {}},
            id="slot-not-finalized",
        ),
    ],
)
@pytest.mark.parametrize("addr", [PRIMARY, OTHER])
async def test_missing_finalized_value_reads_zeroes(other_states, addr):
    host = _host(other_states, other_states)
    assert await _read(host, StorageView.LATEST_FINALIZED, addr, 8) == b"\x00" * 8


@pytest.mark.asyncio
@pytest.mark.parametrize("read_before_write", [False, True])
async def test_write_does_not_leak_into_finalized_view(read_before_write):
    host = Host.__new__(Host)
    view = _SnapshotView(
        _snapshot(PRIMARY, {"accepted": {}, "finalized": {SLOT_KEY: _raw(b"old")}}),
        lambda _addr: None,
        readonly=False,
        shared_decoded_value_cache={},
    )
    host._state_proxy = view
    if read_before_write:
        assert await _read(host, StorageView.LATEST_FINALIZED, PRIMARY, 3) == b"old"
    view.storage_write(SLOT, 0, b"new")
    assert await _read(host, StorageView.DEFAULT, PRIMARY, 3) == b"new"
    assert await _read(host, StorageView.LATEST_FINALIZED, PRIMARY, 3) == b"old"


@pytest.mark.asyncio
async def test_finalized_read_pads_past_value_end():
    host = _host({}, {"finalized": {SLOT_KEY: _raw(b"abc")}})
    assert await _read(host, StorageView.LATEST_FINALIZED, OTHER, 4, offset=1) == (
        b"bc\x00\x00"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("addr", [PRIMARY, OTHER])
async def test_finalized_and_default_reads_do_not_share_cache(addr):
    states = {
        "accepted": {SLOT_KEY: _raw(b"accepted")},
        "finalized": {SLOT_KEY: _raw(b"final")},
    }
    host = _host(states, states)
    assert await _read(host, StorageView.DEFAULT, addr, 8) == b"accepted"
    assert await _read(host, StorageView.LATEST_FINALIZED, addr, 5) == b"final"
    assert await _read(host, StorageView.DEFAULT, addr, 8) == b"accepted"


@pytest.mark.asyncio
async def test_proxy_without_finalized_state_reads_zeroes():
    host = Host.__new__(Host)
    host._state_proxy = _StateProxyNone(Address(PRIMARY))
    assert await _read(host, StorageView.LATEST_FINALIZED, PRIMARY, 4) == b"\x00" * 4
