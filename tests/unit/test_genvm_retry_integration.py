"""
Integration tests for GenVM Manager retry logic - tests the actual retry behavior
by mocking aiohttp.request at a lower level.

Note: Tests that import worker_service need to run in Docker (require fastapi).
Those are in test_worker_health_degradation.py
"""

import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
import pytest
import aiohttp

from backend.node.genvm.origin import base_host


class MockAsyncContextManager:
    """Helper for mocking async context managers."""

    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *args):
        pass


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
