"""A cross-major run dials the host listener once per executor.

The genvm reroutes a nested call to another major by starting a second executor,
and that executor connects to the same unix socket the top-level run uses. The
host therefore has to keep accepting after `loop_enter` handed the first
connection to the protocol loop, and it has to serve every later connection with
the same handler state.
"""

import asyncio
import contextlib
import functools
import socket
import tempfile
from pathlib import Path

import pytest

import backend.node.genvm.base as genvm_base
import backend.node.genvm.origin.base_host as genvmhost
from backend.node.genvm.base import Context, Host


class _FakeStateProxy:
    def storage_read(self, _addr, _slot, _index, le):  # pragma: no cover - unused
        return b"\x00" * le


def _host(sock_listener: socket.socket) -> tuple[Host, Context]:
    host = Host(
        sock_listener,
        calldata_bytes=b"",
        state_proxy=_FakeStateProxy(),
        leader_results=None,
    )
    ctx = Context()
    host.bind_context(ctx)
    return host, ctx


async def _connect(path: Path) -> socket.socket:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.setblocking(False)
    await asyncio.get_event_loop().sock_connect(sock, str(path))
    return sock


@pytest.mark.asyncio
async def test_later_connections_are_served_with_the_same_handler(monkeypatch):
    served: list[tuple[object, socket.socket, object]] = []

    async def fake_host_loop_on(handler, sock, *, ctx):
        served.append((handler, sock, ctx))
        await asyncio.Event().wait()

    monkeypatch.setattr(genvmhost, "host_loop_on", fake_host_loop_on)

    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock_listener:
            sock_listener.setblocking(False)
            sock_listener.bind(str(path))
            sock_listener.listen(8)

            host, ctx = _host(sock_listener)
            cancellation = asyncio.Event()

            first = await _connect(path)
            accepted = await host.loop_enter(cancellation)
            assert accepted is host.sock

            nested = await _connect(path)
            for _ in range(100):
                if served:
                    break
                await asyncio.sleep(0.01)

            # The nested executor's connection is served; the top-level one is
            # not, because `run_genvm` drives it through `host_loop` itself.
            assert len(served) == 1
            served_handler, served_sock, served_ctx = served[0]
            # Nested calls read and write the state of the run they belong to,
            # so they are served by that run's handler, not a fresh one.
            assert served_handler is host
            assert served_ctx is ctx
            assert served_sock is not accepted

            cancellation.set()
            await host.close_connections()
            first.close()
            nested.close()

    assert host.sock is None


@pytest.mark.asyncio
async def test_accepting_stops_when_the_run_ends():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock_listener:
            sock_listener.setblocking(False)
            sock_listener.bind(str(path))
            sock_listener.listen(8)

            host, _ctx = _host(sock_listener)
            cancellation = asyncio.Event()

            first = await _connect(path)
            await host.loop_enter(cancellation)
            accept_task = host._accept_task
            assert accept_task is not None

            cancellation.set()
            await asyncio.wait_for(accept_task, timeout=5)
            assert not accept_task.cancelled()

            await host.close_connections()
            first.close()


@pytest.mark.asyncio
async def test_loop_enter_reports_a_run_that_never_connected():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock_listener:
            sock_listener.setblocking(False)
            sock_listener.bind(str(path))
            sock_listener.listen(8)

            host, _ctx = _host(sock_listener)
            cancellation = asyncio.Event()
            cancellation.set()

            with pytest.raises(Exception, match="Program failed"):
                await host.loop_enter(cancellation)
            assert host._accept_task is None


@pytest.mark.asyncio
async def test_close_connections_times_out_on_a_task_that_ignores_cancellation(
    monkeypatch,
):
    # Cancellation only requests a CancelledError at the task's next await
    # point; a task that keeps swallowing it (e.g. stuck outside the event
    # loop, or shielded) must not be able to hang shutdown forever.
    monkeypatch.setattr(genvm_base, "_CLOSE_CONNECTIONS_TIMEOUT_S", 0.05)

    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock_listener:
            sock_listener.setblocking(False)
            sock_listener.bind(str(path))
            sock_listener.listen(8)

            host, ctx = _host(sock_listener)

            async def _uncancellable():
                while True:
                    with contextlib.suppress(asyncio.CancelledError):
                        await asyncio.sleep(10)

            stuck_task = asyncio.create_task(_uncancellable())
            host._connection_tasks.append(stuck_task)

            await asyncio.wait_for(host.close_connections(), timeout=5)

            assert host.sock is None
            stuck_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stuck_task


class _NoopManagerClient:
    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _AttemptRecordingHost:
    """Records its teardown into the shared event log."""

    events: list[str] = []

    def __init__(self, _sock_listener, **_kwargs):
        self.sock = None

    def bind_context(self, _ctx):
        pass

    async def close_connections(self):
        self.events.append("closed")


@pytest.mark.asyncio
async def test_a_failed_attempt_is_torn_down_before_the_backoff(monkeypatch):
    """Cleanup belongs before the sleep, not after it.

    A failed attempt's listener and its nested connection tasks stay live for
    as long as the backoff runs if teardown waits for the `finally` that
    follows the sleep -- so a dead attempt would keep serving executors with a
    state proxy the next attempt is about to replace.
    """
    events = _AttemptRecordingHost.events
    events.clear()

    async def fake_run_genvm(_handler, **_kwargs):
        events.append("attempt")
        raise RuntimeError("attempt failed")

    async def fake_sleep(_delay):
        events.append("backoff")

    monkeypatch.setattr(genvmhost, "run_genvm", fake_run_genvm)
    monkeypatch.setattr(genvmhost, "ManagerClient", _NoopManagerClient)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    await genvm_base.run_genvm_host(
        functools.partial(
            _AttemptRecordingHost,
            state_proxy=_FakeStateProxy(),
            calldata_bytes=b"",
            leader_results=None,
        ),
        timeout=0.1,
        is_sync=False,
        message={"is_init": True},
    )

    assert events.index("closed") < events.index("backoff")
