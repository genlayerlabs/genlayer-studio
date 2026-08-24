"""What the run request tells the manager about which executor to use.

The manager picks an executor line from the request selector's `major`, so a
contract deployed against an older line must keep reaching that line on every
later call. The major lives in octet 0 of the root slot, which the host reads
through the same state proxy the run itself uses; a deploy has nothing to read
and declares `UNDEPLOYED_MAJOR` instead.

The request also has to opt into being asked where a `CallContract` runs, which
is what lets a pinned callee keep its own executor across a nested call.
"""

import functools

import pytest

import backend.node.genvm.base as genvm_base
import backend.node.genvm.origin.base_host as base_host
from backend.node.genvm.base import Context, Host
from backend.node.genvm.error_codes import GenVMInternalError
from backend.node.types import Address

ADDR = Address("0x" + "11" * 20)


class _RecordingStateProxy:
    """Serves one root slot and records what was asked of it."""

    def __init__(self, root: bytes):
        self._root = root
        self.reads: list[tuple[Address, bytes, int, int]] = []

    def storage_read(self, account: Address, slot: bytes, index: int, le: int, /):
        self.reads.append((account, slot, index, le))
        return self._root[index : index + le].ljust(le, b"\x00")

    def get_balance(self, _addr):  # pragma: no cover - unused here
        raise AssertionError("get_balance should not be called")


class _FakeManagerClient:
    """Captures the run payload and aborts before any real manager round-trip."""

    def __init__(self):
        self.payloads = []

    async def run(self, payload):
        self.payloads.append(payload)
        raise RuntimeError("stop before manager request")


def _host(state_proxy) -> Host:
    host = Host.__new__(Host)
    host._state_proxy = state_proxy
    return host


async def _run(monkeypatch, host: Host, message, **kwargs) -> _FakeManagerClient:
    """Starts a run that stops at the manager request, keeping its payload."""
    client = _FakeManagerClient()

    async def fake_host_loop(_handler, cancellation, *, ctx):
        await cancellation.wait()

    monkeypatch.setattr(base_host, "host_loop", fake_host_loop)

    with pytest.raises(
        base_host.ManagerRunNotStarted, match="stop before manager request"
    ):
        await base_host.run_genvm(
            host,
            timeout=None,
            manager_client=client,
            ctx=Context(),
            is_sync=False,
            message=message,
            host="unix://test",
            calldata=b"",
            bucket_totals=[10_000_000] * 4,
            **kwargs,
        )
    assert len(client.payloads) == 1
    return client


@pytest.mark.asyncio
async def test_call_declares_the_deployed_major(monkeypatch):
    state = _RecordingStateProxy(bytes([2]))
    client = await _run(
        monkeypatch, _host(state), {"is_init": False, "contract_address": ADDR}
    )

    assert client.payloads[0]["selector"] == {"kind": "major", "major": 2}
    assert state.reads == [(ADDR, b"\x00" * 32, 0, 1)]


@pytest.mark.asyncio
async def test_deploy_declares_the_undeployed_major(monkeypatch):
    state = _RecordingStateProxy(bytes([2]))
    client = await _run(
        monkeypatch, _host(state), {"is_init": True, "contract_address": ADDR}
    )

    assert client.payloads[0]["selector"] == {
        "kind": "major",
        "major": base_host.UNDEPLOYED_MAJOR,
    }
    # A deploy has no prior state to read a major out of.
    assert state.reads == []


@pytest.mark.asyncio
async def test_an_explicit_major_skips_the_storage_read(monkeypatch):
    state = _RecordingStateProxy(bytes([2]))
    client = await _run(
        monkeypatch,
        _host(state),
        {"is_init": False, "contract_address": ADDR},
        major=6,
    )

    assert client.payloads[0]["selector"] == {"kind": "major", "major": 6}
    assert state.reads == []


class _NoopHost:
    """Stands in for the real host: `run_genvm` never gets to use it here."""

    def __init__(self, _sock_listener, **_kwargs):
        self.sock = None

    def bind_context(self, _ctx):
        pass

    async def close_connections(self):
        pass


class _NoopManagerClient:
    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


@pytest.mark.asyncio
async def test_run_genvm_host_opts_into_cross_contract_resolution(monkeypatch):
    """Opting out is a silent downgrade, not a missing feature.

    Without this flag the manager answers `resolve_call_contract_executor`
    itself with "stay in-process", so a pinned callee's code runs on the
    caller's executor and the pin quietly stops meaning anything.
    """
    captured: dict = {}

    async def fake_run_genvm(_handler, **kwargs):
        captured.update(kwargs)
        # Cuts the run short: `run_genvm_host` re-raises this one untouched
        # instead of entering its retry backoff.
        raise GenVMInternalError(
            "stop after the run request was built",
            error_code=None,
            causes=[],
            is_fatal=False,
        )

    monkeypatch.setattr(base_host, "run_genvm", fake_run_genvm)
    monkeypatch.setattr(base_host, "ManagerClient", _NoopManagerClient)

    with pytest.raises(GenVMInternalError):
        await genvm_base.run_genvm_host(
            functools.partial(
                _NoopHost,
                state_proxy=_RecordingStateProxy(bytes([2])),
                calldata_bytes=b"",
                leader_results=None,
            ),
            timeout=5,
            is_sync=False,
            message={"is_init": False, "contract_address": ADDR},
        )

    assert captured["request_extra"] == {"hook_cross_contract_calls": True}
