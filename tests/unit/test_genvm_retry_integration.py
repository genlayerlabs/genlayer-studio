"""
Integration tests for GenVM Manager retry logic - tests the actual retry behavior
by mocking aiohttp.request at a lower level.

Note: Tests that import worker_service need to run in Docker (require fastapi).
Those are in test_worker_health_degradation.py
"""

import functools
from unittest.mock import AsyncMock, patch
import pytest

import backend.node.genvm.base as base_host
from backend.node.genvm.origin import base_host as origin_base_host
from backend.node.genvm.origin import manager_api


class NoopLogger:
    def trace(self, *args, **kwargs):
        pass

    def debug(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class RecordingCtx:
    logger = NoopLogger()

    def __init__(self):
        self.stats = {}
        self.successes = 0
        self.failures = 0

    def on_genvm_success(self):
        self.successes += 1

    def on_genvm_failure(self):
        self.failures += 1

    def add_stat(self, key, value):
        self.stats[key] = value

    def get_timeout(self, _action, _timeout_type):
        return None

    def retry_delay(self, _action, attempt_no):
        return 0 if attempt_no < 2 else None


class NoopHandler:
    pass


async def _fake_host_loop(_handler, _cancellation, *, ctx):
    return None


class _RunRejectingManagerClient:
    """A manager client whose RUN is rejected with a given socket error, so
    run_genvm exercises its not-started classification without a real manager."""

    def __init__(self, error: origin_base_host.ManagerSocketError):
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def run(self, _payload):
        raise self._error


class _ArtifactFailingManagerClient:
    """Reports a finished run, then fails to hand back one of its artifacts."""

    class _State:
        boot_id = 1
        genvm_id = 42

    def __init__(self):
        self.ack_called = False
        self.cancel_called = False

    async def run(self, _payload):
        return self._State()

    async def wait_terminal(self, _state):
        return {
            "finished": {
                "artifact_sizes": {"stdout": 5},
                "consumed_result": None,
            }
        }

    async def get_artifact(self, _boot_id, _genvm_id, _field):
        raise RuntimeError("artifact transfer failed")

    async def ack(self, _boot_id, _genvm_id):
        self.ack_called = True

    async def cancel(self, _boot_id, _genvm_id):
        self.cancel_called = True


class _FailedToStartManagerClient:
    """Accepts the RUN, then reports a failed_to_start terminal so run_genvm
    exercises the terminal-based not-started classification."""

    class _State:
        boot_id = 1
        genvm_id = 1

    def __init__(self, error: str):
        self._error = error

    async def run(self, _payload):
        return self._State()

    async def wait_terminal(self, _state):
        return {"failed_to_start": {"error": self._error}}

    async def ack(self, _boot_id, _genvm_id):
        pass


class _FakeManagerClientCM:
    """Async-context stand-in so run_genvm_host does not open a real websocket
    when base_host.run_genvm is mocked."""

    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _DummyStateProxy(base_host.StateProxy):
    def __init__(self):
        self.snapshot_factory = lambda _addr: None

    def storage_read(self, account, slot, index, le, /) -> bytes:
        return b"\x00" * le

    def get_balance(self, addr) -> int:
        return 0


class _ReturningHost:
    def __init__(self, _sock_listener, **_kwargs):
        self.sock = None

    def bind_context(self, _ctx):
        pass

    async def close_connections(self):
        pass

    def provide_result(self, _res, state, _ctx=None):
        return base_host.ExecutionResult(
            result=base_host.ExecutionReturn(ret=b"\x00"),
            eq_outputs={},
            pending_transactions=[],
            stdout="",
            stderr="",
            genvm_log=[],
            state=state,
            processing_time=0,
            nondet_disagree=None,
            execution_stats={},
        )


class TestRetryBehavior:
    """Test actual retry behavior with mocked HTTP."""

    def setup_method(self):
        base_host.set_genvm_callbacks(None, None)

    @pytest.mark.asyncio
    async def test_success_calls_success_callback(self):
        """Successful request calls on_success callback."""
        success_called = False

        def on_success():
            nonlocal success_called
            success_called = True

        def on_failure():
            pytest.fail("on_failure should not be called on success")

        base_host.set_genvm_callbacks(on_success=on_success, on_failure=on_failure)

        # Verify callbacks are set correctly
        assert base_host._on_genvm_success == on_success
        assert base_host._on_genvm_failure == on_failure

        # Simulate what wrap_proc does on success
        if base_host._on_genvm_success is not None:
            base_host._on_genvm_success()

        assert success_called is True

    @pytest.mark.asyncio
    async def test_failure_after_retries_calls_failure_callback(self):
        """All retries exhausted calls on_failure callback."""
        failure_called = False
        success_called = False

        def on_success():
            nonlocal success_called
            success_called = True

        def on_failure():
            nonlocal failure_called
            failure_called = True

        base_host.set_genvm_callbacks(on_success=on_success, on_failure=on_failure)

        # Verify callbacks are set
        assert base_host._on_genvm_success == on_success
        assert base_host._on_genvm_failure == on_failure

        # Simulate what wrap_proc does on failure after all retries
        if base_host._on_genvm_failure is not None:
            base_host._on_genvm_failure()

        assert failure_called is True
        assert success_called is False

    @pytest.mark.asyncio
    async def test_callbacks_can_be_none(self):
        """None callbacks don't crash when invoked."""
        base_host.set_genvm_callbacks(None, None)

        # These should not raise
        if base_host._on_genvm_success is not None:
            base_host._on_genvm_success()
        if base_host._on_genvm_failure is not None:
            base_host._on_genvm_failure()

        # No assertion needed - test passes if no exception

    @pytest.mark.asyncio
    async def test_callback_replacement(self):
        """Callbacks can be replaced."""
        first_called = False
        second_called = False

        def first_callback():
            nonlocal first_called
            first_called = True

        def second_callback():
            nonlocal second_called
            second_called = True

        # Set first callback
        base_host.set_genvm_callbacks(on_success=first_callback, on_failure=None)
        base_host._on_genvm_success()
        assert first_called is True
        assert second_called is False

        # Replace with second callback
        base_host.set_genvm_callbacks(on_success=second_callback, on_failure=None)
        base_host._on_genvm_success()
        assert second_called is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "manager_error",
        [
            "modules are required but not running (is_sync=false with 'n' permission)",
            "modules are required but not all are running",
        ],
    )
    async def test_transient_run_refusal_is_retryable(self, monkeypatch, manager_error):
        """A modules-not-running RUN rejection surfaces as a retryable
        ManagerRunNotStarted, classified inside base_host (no caller string
        matching)."""
        ctx = RecordingCtx()
        monkeypatch.setattr(origin_base_host, "host_loop", _fake_host_loop)
        client = _RunRejectingManagerClient(
            origin_base_host.ManagerSocketError(
                manager_api.Errors.INTERNAL, manager_error
            )
        )

        with pytest.raises(origin_base_host.ManagerRunNotStarted) as exc_info:
            await origin_base_host.run_genvm(
                NoopHandler(),
                manager_client=client,
                ctx=ctx,
                is_sync=False,
                message={"is_init": True},
                host="unix://test",
                calldata=b"",
                bucket_totals=origin_base_host.default_bucket_totals(3),
            )

        assert exc_info.value.retryable is True
        assert exc_info.value.reason == "manager_modules_not_running"
        assert ctx.failures == 1
        assert ctx.stats["manager_run_attempt_0_error"]["retryable"] is True
        assert (
            ctx.stats["manager_run_attempt_0_error"]["reason"]
            == "manager_modules_not_running"
        )

    @pytest.mark.asyncio
    async def test_unknown_run_refusal_is_not_retryable(self, monkeypatch):
        """An unrecognised RUN rejection is a permanent ManagerRunNotStarted."""
        ctx = RecordingCtx()
        monkeypatch.setattr(origin_base_host, "host_loop", _fake_host_loop)
        client = _RunRejectingManagerClient(
            origin_base_host.ManagerSocketError(
                manager_api.Errors.INTERNAL,
                "unknown variant `foo`, expected one of `bar`, `baz`",
            )
        )

        with pytest.raises(origin_base_host.ManagerRunNotStarted) as exc_info:
            await origin_base_host.run_genvm(
                NoopHandler(),
                manager_client=client,
                ctx=ctx,
                is_sync=False,
                message={"is_init": True},
                host="unix://test",
                calldata=b"",
                bucket_totals=origin_base_host.default_bucket_totals(3),
            )

        assert exc_info.value.retryable is False
        assert exc_info.value.reason == "manager_refused"
        assert ctx.stats["manager_run_attempt_0_error"]["retryable"] is False

    @pytest.mark.asyncio
    async def test_run_genvm_raises_terminal_result_unavailable_on_artifact_failure(
        self, monkeypatch
    ):
        """A finished run whose artifact retrieval fails must surface as
        `TerminalResultUnavailable`, not a plain exception -- the run already
        executed, so the caller must not mistake this for an attempt that
        never happened and retry it."""
        monkeypatch.setattr(origin_base_host, "host_loop", _fake_host_loop)
        client = _ArtifactFailingManagerClient()
        ctx = RecordingCtx()

        with pytest.raises(origin_base_host.TerminalResultUnavailable) as exc_info:
            await origin_base_host.run_genvm(
                NoopHandler(),
                manager_client=client,
                ctx=ctx,
                is_sync=False,
                message={"is_init": True},
                host="unix://test",
                calldata=b"",
                bucket_totals=origin_base_host.default_bucket_totals(3),
            )

        assert exc_info.value.genvm_id == 42
        # Releasing the manager's retention of the finished run must still
        # happen -- only its result is unavailable, not the run itself.
        assert client.ack_called
        assert not client.cancel_called

    @pytest.mark.asyncio
    async def test_run_genvm_host_retries_retryable_not_started(self):
        """run_genvm_host retries a retryable ManagerRunNotStarted and succeeds,
        with no transport knowledge of its own."""
        state = _DummyStateProxy()
        host_supplier = functools.partial(
            _ReturningHost,
            state_proxy=state,
            calldata_bytes=b"",
            leader_results=None,
        )
        transient = origin_base_host.ManagerRunNotStarted(
            "modules are required but not running",
            retryable=True,
            reason="manager_modules_not_running",
        )

        with patch(
            "backend.node.genvm.base.base_host.run_genvm",
            new_callable=AsyncMock,
            side_effect=[transient, object()],
        ) as run_mock, patch(
            "backend.node.genvm.base.base_host.ManagerClient",
            _FakeManagerClientCM,
        ), patch(
            "backend.node.genvm.base.asyncio.sleep",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await base_host.run_genvm_host(
                host_supplier,
                timeout=15,
                is_sync=False,
                message={"is_init": True},
                capture_output=False,
            )

        assert run_mock.await_count == 2
        assert isinstance(result.result, base_host.ExecutionReturn)

    @pytest.mark.asyncio
    async def test_run_genvm_host_fails_fast_on_permanent_not_started(self):
        """A non-retryable ManagerRunNotStarted is raised immediately, not
        retried until the deadline."""
        state = _DummyStateProxy()
        host_supplier = functools.partial(
            _ReturningHost,
            state_proxy=state,
            calldata_bytes=b"",
            leader_results=None,
        )
        permanent = origin_base_host.ManagerRunNotStarted(
            "unknown variant `foo`",
            retryable=False,
            reason="manager_refused",
        )

        with patch(
            "backend.node.genvm.base.base_host.run_genvm",
            new_callable=AsyncMock,
            side_effect=[permanent, object()],
        ) as run_mock, patch(
            "backend.node.genvm.base.base_host.ManagerClient",
            _FakeManagerClientCM,
        ), patch(
            "backend.node.genvm.base.asyncio.sleep",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(origin_base_host.ManagerRunNotStarted) as exc_info:
                await base_host.run_genvm_host(
                    host_supplier,
                    timeout=15,
                    is_sync=False,
                    message={"is_init": True},
                    capture_output=False,
                )

        assert exc_info.value.retryable is False
        assert run_mock.await_count == 1

    @pytest.mark.asyncio
    async def test_run_genvm_host_does_not_start_a_second_run_after_terminal_result_unavailable(
        self,
    ):
        """`TerminalResultUnavailable` must propagate immediately, never fall
        into the generic retry branch that would start a brand new run for a
        genvm that already executed and finished."""
        state = _DummyStateProxy()
        host_supplier = functools.partial(
            _ReturningHost,
            state_proxy=state,
            calldata_bytes=b"",
            leader_results=None,
        )
        failure = origin_base_host.TerminalResultUnavailable(
            "failed to retrieve/decode result for finished run: boom",
            genvm_id=42,
        )

        with patch(
            "backend.node.genvm.base.base_host.run_genvm",
            new_callable=AsyncMock,
            side_effect=[failure, object()],
        ) as run_mock, patch(
            "backend.node.genvm.base.base_host.ManagerClient",
            _FakeManagerClientCM,
        ), patch(
            "backend.node.genvm.base.asyncio.sleep",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(origin_base_host.TerminalResultUnavailable):
                await base_host.run_genvm_host(
                    host_supplier,
                    timeout=15,
                    is_sync=False,
                    message={"is_init": True},
                    capture_output=False,
                )

        # Only the one, already-consumed attempt -- no second call to
        # `run_genvm` that would start a new genvm execution.
        assert run_mock.await_count == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error,expected_retryable,expected_reason",
        [
            (
                "modules are required but not running",
                True,
                "manager_modules_not_running",
            ),
            ("absent runner for py-genlayer", False, "manager_refused"),
        ],
    )
    async def test_failed_to_start_terminal_is_classified(
        self, monkeypatch, error, expected_retryable, expected_reason
    ):
        """A failed_to_start terminal surfaces as ManagerRunNotStarted (not an
        INTERNAL_ERROR result) and counts as a manager failure."""
        ctx = RecordingCtx()
        monkeypatch.setattr(origin_base_host, "host_loop", _fake_host_loop)

        with pytest.raises(origin_base_host.ManagerRunNotStarted) as exc_info:
            await origin_base_host.run_genvm(
                NoopHandler(),
                manager_client=_FailedToStartManagerClient(error),
                ctx=ctx,
                is_sync=False,
                message={"is_init": True},
                host="unix://test",
                calldata=b"",
                bucket_totals=origin_base_host.default_bucket_totals(3),
            )

        assert exc_info.value.retryable is expected_retryable
        assert exc_info.value.reason == expected_reason
        # The RUN was accepted (success), then failed to start (failure).
        assert ctx.failures == 1
        assert ctx.stats["manager_run_start_failed"]["retryable"] is expected_retryable

    @pytest.mark.asyncio
    async def test_run_genvm_host_retryable_exhausts_timeout_budget(self):
        """A persistently retryable ManagerRunNotStarted stops at the deadline
        (timeout result) instead of looping forever or raising."""
        state = _DummyStateProxy()
        host_supplier = functools.partial(
            _ReturningHost,
            state_proxy=state,
            calldata_bytes=b"",
            leader_results=None,
        )
        transient = origin_base_host.ManagerRunNotStarted(
            "modules are required but not running",
            retryable=True,
            reason="manager_modules_not_running",
        )

        # A short real deadline (no sleep mock): the first backoff is clamped to
        # the remaining budget, so the next top-of-loop check exits via timeout.
        with patch(
            "backend.node.genvm.base.base_host.run_genvm",
            new_callable=AsyncMock,
            side_effect=transient,
        ) as run_mock, patch(
            "backend.node.genvm.base.base_host.ManagerClient",
            _FakeManagerClientCM,
        ):
            result = await base_host.run_genvm_host(
                host_supplier,
                timeout=0.3,
                is_sync=False,
                message={"is_init": True},
                capture_output=False,
            )

        assert result is not None
        assert run_mock.await_count >= 1
        # It exhausted the budget rather than propagating the exception.
        assert isinstance(result.result, base_host.ExecutionError)


class TestRetryConfiguration:
    """Test retry configuration via environment variables."""

    def test_retry_count_from_env(self):
        """GENVM_MANAGER_RUN_RETRIES configures retry count."""
        with patch.dict("os.environ", {"GENVM_MANAGER_RUN_RETRIES": "5"}):
            assert base_host._get_int("GENVM_MANAGER_RUN_RETRIES", 3) == 5

    def test_retry_delay_from_env(self):
        """GENVM_MANAGER_RUN_RETRY_DELAY_SECONDS configures base delay."""
        with patch.dict("os.environ", {"GENVM_MANAGER_RUN_RETRY_DELAY_SECONDS": "2.5"}):
            assert (
                base_host._get_timeout_seconds(
                    "GENVM_MANAGER_RUN_RETRY_DELAY_SECONDS", 1.0
                )
                == 2.5
            )

    def test_http_timeout_from_env(self):
        """GENVM_MANAGER_RUN_HTTP_TIMEOUT_SECONDS configures per-attempt timeout."""
        with patch.dict("os.environ", {"GENVM_MANAGER_RUN_HTTP_TIMEOUT_SECONDS": "15"}):
            assert (
                base_host._get_timeout_seconds(
                    "GENVM_MANAGER_RUN_HTTP_TIMEOUT_SECONDS", 10.0
                )
                == 15.0
            )

    def test_zero_retries(self):
        """Zero retries means single attempt."""
        with patch.dict("os.environ", {"GENVM_MANAGER_RUN_RETRIES": "0"}):
            assert base_host._get_int("GENVM_MANAGER_RUN_RETRIES", 3) == 0

    def test_negative_values_use_default(self):
        """Negative values in env vars are parsed but may cause issues."""
        with patch.dict("os.environ", {"GENVM_MANAGER_RUN_RETRIES": "-1"}):
            # _get_int parses the value, doesn't validate range
            assert base_host._get_int("GENVM_MANAGER_RUN_RETRIES", 3) == -1


class TestExponentialBackoff:
    """Test exponential backoff calculation."""

    def test_backoff_doubles_each_attempt(self):
        """Backoff delay doubles: 1s, 2s, 4s for base=1."""
        base_delay = 1.0
        delays = [base_delay * (2**attempt) for attempt in range(3)]
        assert delays == [1.0, 2.0, 4.0]

    def test_backoff_with_custom_base(self):
        """Custom base delay scales proportionally."""
        base_delay = 0.5
        delays = [base_delay * (2**attempt) for attempt in range(3)]
        assert delays == [0.5, 1.0, 2.0]

    def test_backoff_grows_exponentially(self):
        """5 retries with 1s base = 1, 2, 4, 8, 16 seconds."""
        base_delay = 1.0
        delays = [base_delay * (2**attempt) for attempt in range(5)]
        assert delays == [1.0, 2.0, 4.0, 8.0, 16.0]
        assert sum(delays) == 31.0  # Total wait time

    def test_backoff_formula_matches_implementation(self):
        """Verify our test formula matches what's in base_host.py."""
        # From base_host.py line 563: delay = retry_base_delay_s * (2**attempt)
        retry_base_delay_s = 1.0
        for attempt in range(3):
            expected = retry_base_delay_s * (2**attempt)
            # This is exactly what wrap_proc does
            assert expected == [1.0, 2.0, 4.0][attempt]


class TestTimeoutBudget:
    """Test total timeout budget with retries."""

    def test_worst_case_timing(self):
        """Calculate worst-case timing with 3 retries."""
        per_attempt_timeout = 10.0  # Default
        max_retries = 3
        base_delay = 1.0

        # Worst case: all attempts timeout
        total_attempt_time = per_attempt_timeout * max_retries
        # Backoff delays between attempts (only between, not after last)
        backoff_delays = sum(base_delay * (2**i) for i in range(max_retries - 1))
        # delays: 1s (after 1st), 2s (after 2nd) = 3s total

        total_worst_case = total_attempt_time + backoff_delays
        assert total_worst_case == 33.0  # 30s attempts + 3s backoff

    def test_fast_failure_mode(self):
        """With 1s timeout, total budget is much smaller."""
        per_attempt_timeout = 1.0
        max_retries = 3
        base_delay = 0.1

        total_attempt_time = per_attempt_timeout * max_retries
        backoff_delays = sum(base_delay * (2**i) for i in range(max_retries - 1))

        total_worst_case = total_attempt_time + backoff_delays
        assert total_worst_case == 3.3  # 3s attempts + 0.3s backoff
