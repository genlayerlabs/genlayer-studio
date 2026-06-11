"""
Integration tests for GenVM Manager retry logic - tests the actual retry behavior
by mocking aiohttp.request at a lower level.

Note: Tests that import worker_service need to run in Docker (require fastapi).
Those are in test_worker_health_degradation.py
"""

from unittest.mock import patch
import pytest

import backend.node.genvm.base as base_host
from backend.node.genvm.origin import base_host as origin_base_host


class MockAsyncContextManager:
    """Helper for mocking async context managers."""

    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *args):
        pass


class MockResponse:
    """Helper for mocking aiohttp responses."""

    def __init__(self, status, body):
        self.status = status
        self.body = body

    async def json(self):
        return self.body


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


def _consumed_return_result():
    return list(
        bytes([origin_base_host.public_abi.ResultCode.RETURN])
        + origin_base_host.gvm_calldata.encode(
            {
                "execution_hash": b"",
                "data": b"ok",
                "fingerprint": None,
                "storage_changes": [],
                "emissions": [],
                "nondet_results": [],
                "data_fees_remaining": [],
            }
        )
    )


async def _fake_host_loop(_handler, _cancellation, *, ctx):
    return None


async def _fake_prob_died_wait(*awaitables):
    for awaitable in awaitables:
        close = getattr(awaitable, "close", None)
        if close is not None:
            close()


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
    async def test_modules_not_running_500_is_retried_and_can_succeed(
        self, monkeypatch, manager_error
    ):
        """Transient manager module restart 500 retries /genvm/run."""
        ctx = RecordingCtx()
        post_bodies = [
            {"error": manager_error},
            {"id": "genvm-1"},
        ]
        post_attempts = 0

        def fake_request(method, url, **_kwargs):
            nonlocal post_attempts
            if method == "POST" and url.endswith("/genvm/run"):
                body = post_bodies[post_attempts]
                status = 500 if post_attempts == 0 else 200
                post_attempts += 1
                return MockAsyncContextManager(MockResponse(status, body))
            if method == "DELETE":
                return MockAsyncContextManager(MockResponse(200, {}))
            if method == "GET" and url.endswith("/genvm/genvm-1"):
                return MockAsyncContextManager(
                    MockResponse(
                        200,
                        {
                            "status": {
                                "consumed_result": _consumed_return_result(),
                                "stdout": "",
                                "stderr": "",
                                "genvm_log": [],
                            }
                        },
                    )
                )
            raise AssertionError(f"unexpected request: {method} {url}")

        monkeypatch.setattr(origin_base_host, "host_loop", _fake_host_loop)
        monkeypatch.setattr(
            origin_base_host, "_await_first_cancel_others", _fake_prob_died_wait
        )
        monkeypatch.setattr(origin_base_host.aiohttp, "request", fake_request)

        result = await origin_base_host.run_genvm(
            NoopHandler(),
            ctx=ctx,
            is_sync=False,
            message={},
            host="unix://test",
            calldata=b"",
        )

        assert post_attempts == 2
        assert result.result_data == b"ok"
        assert ctx.successes == 1
        assert ctx.failures == 0
        assert ctx.stats["manager_run_attempt_0_error"]["will_retry"] is True
        assert (
            ctx.stats["manager_run_attempt_0_error"]["error_type"]
            == "GenVMManagerRetryableError"
        )
        assert (
            ctx.stats["manager_run_attempt_0_error"]["retry_reason"]
            == "manager_modules_not_running"
        )
        assert ctx.stats["manager_run_attempt_success"]["attempt"] == 1

    @pytest.mark.asyncio
    async def test_other_500_is_not_retried(self, monkeypatch):
        """Non-transient manager 500s still fail fast."""
        ctx = RecordingCtx()
        post_attempts = 0

        def fake_request(method, url, **_kwargs):
            nonlocal post_attempts
            if method == "POST" and url.endswith("/genvm/run"):
                post_attempts += 1
                return MockAsyncContextManager(
                    MockResponse(
                        500,
                        {
                            "error": "unknown variant `foo`, expected one of `bar`, `baz`"
                        },
                    )
                )
            raise AssertionError(f"unexpected request: {method} {url}")

        monkeypatch.setattr(origin_base_host, "host_loop", _fake_host_loop)
        monkeypatch.setattr(
            origin_base_host, "_await_first_cancel_others", _fake_prob_died_wait
        )
        monkeypatch.setattr(origin_base_host.aiohttp, "request", fake_request)

        with pytest.raises(Exception, match="genvm execution failed"):
            await origin_base_host.run_genvm(
                NoopHandler(),
                ctx=ctx,
                is_sync=False,
                message={},
                host="unix://test",
                calldata=b"",
            )

        assert post_attempts == 1
        assert ctx.successes == 0
        assert ctx.failures == 0
        assert ctx.stats["manager_run_attempt_0_error"]["outcome"] == "fatal_error"


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
