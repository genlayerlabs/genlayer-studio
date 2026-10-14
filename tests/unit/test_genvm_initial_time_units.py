import math

import pytest

from backend.node.genvm.origin import base_host


class _NoopLogger:
    def trace(self, *args, **kwargs):
        pass

    def debug(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class _Ctx:
    logger = _NoopLogger()

    def on_genvm_success(self):
        pass

    def on_genvm_failure(self):
        pass

    def add_stat(self, _key, _value):
        pass

    def get_manager_connect_timeout(self):
        return None


class _Handler:
    pass


class _FakeManagerClient:
    """Captures the run payload and aborts before any real manager round-trip."""

    def __init__(self):
        self.payloads = []

    async def run(self, payload):
        self.payloads.append(payload)
        raise RuntimeError("stop before manager request")


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [None, 3.25, 1200])
async def test_run_genvm_scales_initial_time_units_allocation(
    monkeypatch,
    timeout,  # noqa: ASYNC109 - pytest parametrize value, not a real timeout arg
):
    async def fake_host_loop(_handler, cancellation, *, ctx):
        await cancellation.wait()

    monkeypatch.setattr(base_host, "host_loop", fake_host_loop)

    client = _FakeManagerClient()
    # run_genvm wraps a failed RUN in ManagerRunNotStarted (chaining the cause).
    with pytest.raises(
        base_host.ManagerRunNotStarted, match="stop before manager request"
    ):
        await base_host.run_genvm(
            _Handler(),
            timeout=timeout,
            manager_client=client,
            ctx=_Ctx(),
            is_sync=False,
            message={"is_init": True},
            host="unix://test",
            calldata=b"",
            bucket_totals=base_host.default_bucket_totals(3),
        )

    # Upstream now scales the allocation with the run timeout (ceil), falling
    # back to the 10-minute default when no timeout is given.
    assert client.payloads[0]["initial_time_units_allocation"] == (
        math.ceil(timeout or 10 * 60)
    )
