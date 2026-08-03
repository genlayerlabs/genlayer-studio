"""Connection lifecycle of `ManagerClient`, and what a failed `ack` may cost.

Every other test of the manager seam substitutes a fake client, so the real
connect/reconnect code and the finally-block cleanup around a finished run are
only exercised here.
"""

import asyncio

import pytest

from backend.node.genvm.origin import base_host
from backend.node.genvm.origin import manager_api


class _FakeWs:
    def __init__(self):
        self.closed = False


class _CountingClient(base_host.ManagerClient):
    """Records how many connect/close sections overlap.

    `_close()` and `_connect()` rebuild `_ws`, `_session` and `_reader_task`
    together, so two of them running at once leave a half-built connection
    behind whichever one finishes last.
    """

    def __init__(self):
        super().__init__("http://127.0.0.1:0")
        self.in_flight = 0
        self.max_in_flight = 0
        self.connects = 0

    async def _enter(self):
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        # Yields, so a caller that is not excluded by a lock gets in here.
        await asyncio.sleep(0)

    async def _connect(self):
        await self._enter()
        self._ws = _FakeWs()
        self._generation += 1
        self.connects += 1
        self.in_flight -= 1

    async def _close(self):
        await self._enter()
        self._ws = None
        self.in_flight -= 1


@pytest.mark.asyncio
async def test_concurrent_reconnect_paths_do_not_overlap():
    client = _CountingClient()
    client._generation = 1

    # The two entry points a lost connection wakes up at once: a request-side
    # `_ensure_connected` and a waiter reacting to the disconnect event.
    await asyncio.gather(
        client._ensure_connected(),
        client._reconnect_live_runs(1),
    )

    assert client.max_in_flight == 1, "connect/close sections overlapped"
    assert client.connects == 1, "the live connection was torn down and rebuilt"


@pytest.mark.asyncio
async def test_ensure_connected_reuses_a_live_connection():
    client = _CountingClient()
    await client._ensure_connected()
    await client._ensure_connected()
    assert client.connects == 1


class _WireCallRecordingClient(base_host.ManagerClient):
    """Records whether a request actually reached the wire, so a guard that
    refuses before sending can be told apart from one that sends and fails."""

    def __init__(self, boot_id: int):
        super().__init__("http://127.0.0.1:0")
        self.boot_id = boot_id
        self.requests_sent = 0

    async def _request(self, method, _payload, *, retry_on_disconnect):
        self.requests_sent += 1
        if method == manager_api.Methods.GET_ARTIFACT:
            return {"data": b"", "total_len": 0}
        return {}


@pytest.mark.asyncio
async def test_cancel_refuses_a_stale_boot_id_without_touching_the_wire():
    # The manager restarted (client reconnected under a new boot_id) since
    # this genvm_id was handed out. Sending CANCEL with only the genvm_id
    # could hit a same-numbered but unrelated run on the new process.
    client = _WireCallRecordingClient(boot_id=2)
    with pytest.raises(base_host.ManagerRunLost):
        await client.cancel(1, 7)
    assert client.requests_sent == 0


@pytest.mark.asyncio
async def test_ack_refuses_a_stale_boot_id_without_touching_the_wire():
    client = _WireCallRecordingClient(boot_id=2)
    with pytest.raises(base_host.ManagerRunLost):
        await client.ack(1, 7)
    assert client.requests_sent == 0


@pytest.mark.asyncio
async def test_get_artifact_refuses_a_stale_boot_id_without_touching_the_wire():
    client = _WireCallRecordingClient(boot_id=2)
    with pytest.raises(base_host.ManagerRunLost):
        await client.get_artifact(1, 7, "stdout")
    assert client.requests_sent == 0


@pytest.mark.asyncio
async def test_cancel_ack_get_artifact_proceed_for_the_current_boot_id():
    client = _WireCallRecordingClient(boot_id=1)
    await client.cancel(1, 7)
    await client.get_artifact(1, 7, "stdout")
    assert client.requests_sent == 2
    # ack also needs a `total_len`-shaped-independent response; its own
    # request stub returns `{}`, which is fine since ack only reads the
    # response for its side effect of succeeding.
    await client.ack(1, 7)
    assert client.requests_sent == 3


class _AckFailingClient:
    """Reports a finished run, then fails the ack that releases it."""

    class _State:
        boot_id = 1
        genvm_id = 7

    def __init__(self, error: BaseException):
        self._error = error
        self.acked = False

    async def run(self, _payload):
        return self._State()

    async def wait_terminal(self, _state):
        return {"finished": {"artifact_sizes": {}}}

    async def ack(self, _boot_id, _genvm_id):
        self.acked = True
        raise self._error

    async def cancel(self, _boot_id, _genvm_id):
        pass


class _NoopHandler:
    async def get_contract_major(self, *_args, **_kwargs):
        return 0


class _RecordingCtx:
    class _Logger:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    logger = _Logger()

    def on_genvm_success(self):
        pass

    def on_genvm_failure(self):
        pass

    def add_stat(self, *_args, **_kwargs):
        pass

    def get_timeout(self, *_args, **_kwargs):
        return None


async def _run_with_ack_error(monkeypatch, error: BaseException):
    client = _AckFailingClient(error)

    async def _no_host_loop(_handler, cancellation, *, ctx):
        # No executor connects in this test, so the loop just waits for the
        # run to end, exactly as the real one does.
        await cancellation.wait()

    monkeypatch.setattr(base_host, "host_loop", _no_host_loop)
    res = await base_host.run_genvm(
        _NoopHandler(),
        manager_client=client,
        ctx=_RecordingCtx(),
        is_sync=True,
        message={},
        host="unix:///dev/null",
        bucket_totals=[0, 0, 0, 0],
        calldata=b"",
        major=0,
    )
    assert client.acked
    return res


@pytest.mark.asyncio
async def test_ack_socket_error_does_not_discard_a_finished_run(monkeypatch):
    # ack only releases the manager's retention of a run whose result is
    # already in hand; failing it must not turn that into an attempt error
    # that the retry loop answers by executing the contract again.
    res = await _run_with_ack_error(
        monkeypatch,
        base_host.ManagerSocketError(manager_api.Errors.INTERNAL, "boom"),
    )
    assert res.result_kind is not None


@pytest.mark.asyncio
async def test_ack_connection_loss_does_not_discard_a_finished_run(monkeypatch):
    res = await _run_with_ack_error(
        monkeypatch, base_host.ManagerConnectionLost("socket died")
    )
    assert res.result_kind is not None
