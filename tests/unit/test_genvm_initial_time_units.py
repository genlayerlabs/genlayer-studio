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

    def get_timeout(self, _action, _timeout_type):
        return None

    def retry_delay(self, _action, _attempt_no):
        return None


class _Handler:
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [None, 3.25, 1200])
async def test_run_genvm_uses_fixed_initial_time_units_allocation(
    monkeypatch,
    timeout,
):
    captured_payloads = []

    async def fake_host_loop(_handler, cancellation, *, ctx):
        await cancellation.wait()

    async def fake_await_first_cancel_others(*_awaitables):
        for awaitable in _awaitables:
            close = getattr(awaitable, "close", None)
            if close is not None:
                close()

    def capture_payload(payload):
        captured_payloads.append(payload)
        raise RuntimeError("stop before manager request")

    monkeypatch.setattr(base_host, "host_loop", fake_host_loop)
    monkeypatch.setattr(
        base_host,
        "_await_first_cancel_others",
        fake_await_first_cancel_others,
    )
    monkeypatch.setattr(base_host.gvm_calldata, "encode", capture_payload)

    with pytest.raises(Exception, match="genvm execution failed"):
        await base_host.run_genvm(
            _Handler(),
            timeout=timeout,
            ctx=_Ctx(),
            is_sync=False,
            message={},
            host="unix://test",
            calldata=b"",
        )

    assert captured_payloads[0]["initial_time_units_allocation"] == (
        base_host.DEFAULT_INITIAL_TIME_UNITS_ALLOCATION
    )
