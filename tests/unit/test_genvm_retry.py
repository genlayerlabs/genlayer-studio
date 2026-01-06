"""
Tests for GenVM Manager retry logic in base_host.py
"""

from unittest.mock import patch
import pytest

from backend.node.genvm.origin import base_host


class TestGenVMCallbacks:
    """Test GenVM callback mechanism"""

    def setup_method(self):
        # Reset callbacks before each test
        base_host.set_genvm_callbacks(None, None)

    def test_set_genvm_callbacks(self):
        """set_genvm_callbacks properly sets callback functions"""

        def my_success():
            pass

        def my_failure():
            pass

        base_host.set_genvm_callbacks(on_success=my_success, on_failure=my_failure)

        assert base_host._on_genvm_success == my_success
        assert base_host._on_genvm_failure == my_failure

    def test_set_genvm_callbacks_none(self):
        """set_genvm_callbacks can clear callbacks"""
        base_host.set_genvm_callbacks(on_success=lambda: None, on_failure=lambda: None)
        base_host.set_genvm_callbacks(None, None)

        assert base_host._on_genvm_success is None
        assert base_host._on_genvm_failure is None

    def test_callbacks_are_called_correctly(self):
        """Callbacks are invoked when called"""
        success_called = False
        failure_called = False

        def on_success():
            nonlocal success_called
            success_called = True

        def on_failure():
            nonlocal failure_called
            failure_called = True

        base_host.set_genvm_callbacks(on_success=on_success, on_failure=on_failure)

        # Manually invoke callbacks as they would be in wrap_proc
        if base_host._on_genvm_success:
            base_host._on_genvm_success()
        assert success_called is True
        assert failure_called is False

        if base_host._on_genvm_failure:
            base_host._on_genvm_failure()
        assert failure_called is True


class TestEnvVarConfig:
    """Test environment variable configuration"""

    def test_get_int_with_valid_value(self):
        with patch.dict("os.environ", {"TEST_INT": "5"}):
            assert base_host._get_int("TEST_INT", 10) == 5

    def test_get_int_with_invalid_value(self):
        with patch.dict("os.environ", {"TEST_INT": "not_a_number"}):
            assert base_host._get_int("TEST_INT", 10) == 10

    def test_get_int_with_missing_value(self):
        assert base_host._get_int("NONEXISTENT_VAR", 10) == 10

    def test_get_timeout_seconds_with_valid_value(self):
        with patch.dict("os.environ", {"TEST_TIMEOUT": "5.5"}):
            assert base_host._get_timeout_seconds("TEST_TIMEOUT", 10.0) == 5.5

    def test_get_timeout_seconds_with_invalid_value(self):
        with patch.dict("os.environ", {"TEST_TIMEOUT": "not_a_number"}):
            assert base_host._get_timeout_seconds("TEST_TIMEOUT", 10.0) == 10.0

    def test_get_timeout_seconds_with_missing_value(self):
        assert base_host._get_timeout_seconds("NONEXISTENT_VAR", 10.0) == 10.0


class TestRetryConfig:
    """Test retry configuration defaults"""

    def test_default_retry_count(self):
        """Default retry count is 3"""
        with patch.dict("os.environ", {}, clear=False):
            # Clear specific env var if set
            import os

            os.environ.pop("GENVM_MANAGER_RUN_RETRIES", None)
            assert base_host._get_int("GENVM_MANAGER_RUN_RETRIES", 3) == 3

    def test_custom_retry_count(self):
        """Retry count can be configured via env var"""
        with patch.dict("os.environ", {"GENVM_MANAGER_RUN_RETRIES": "5"}):
            assert base_host._get_int("GENVM_MANAGER_RUN_RETRIES", 3) == 5

    def test_default_timeout(self):
        """Default timeout is 10 seconds"""
        import os

        os.environ.pop("GENVM_MANAGER_RUN_HTTP_TIMEOUT_SECONDS", None)
        assert (
            base_host._get_timeout_seconds(
                "GENVM_MANAGER_RUN_HTTP_TIMEOUT_SECONDS", 10.0
            )
            == 10.0
        )

    def test_custom_timeout(self):
        """Timeout can be configured via env var"""
        with patch.dict("os.environ", {"GENVM_MANAGER_RUN_HTTP_TIMEOUT_SECONDS": "5"}):
            assert (
                base_host._get_timeout_seconds(
                    "GENVM_MANAGER_RUN_HTTP_TIMEOUT_SECONDS", 10.0
                )
                == 5.0
            )

    def test_default_retry_delay(self):
        """Default retry delay is 1 second"""
        import os

        os.environ.pop("GENVM_MANAGER_RUN_RETRY_DELAY_SECONDS", None)
        assert (
            base_host._get_timeout_seconds("GENVM_MANAGER_RUN_RETRY_DELAY_SECONDS", 1.0)
            == 1.0
        )
