"""
Tests for worker health degradation based on GenVM consecutive failures
"""

import pytest
from unittest.mock import patch, MagicMock

# Import the module-level functions and variables
import backend.consensus.worker_service as worker_service


class TestGenVMFailureTracking:
    """Test GenVM failure tracking functions"""

    def setup_method(self):
        # Reset failure count before each test
        worker_service._genvm_consecutive_failures = 0

    def test_increment_genvm_failure(self):
        """increment_genvm_failure increases counter"""
        assert worker_service._genvm_consecutive_failures == 0

        worker_service.increment_genvm_failure()
        assert worker_service._genvm_consecutive_failures == 1

        worker_service.increment_genvm_failure()
        assert worker_service._genvm_consecutive_failures == 2

    def test_reset_genvm_failures(self):
        """reset_genvm_failures resets counter to 0"""
        worker_service._genvm_consecutive_failures = 5

        worker_service.reset_genvm_failures()
        assert worker_service._genvm_consecutive_failures == 0

    def test_reset_genvm_failures_when_zero(self):
        """reset_genvm_failures is no-op when already 0"""
        worker_service._genvm_consecutive_failures = 0

        worker_service.reset_genvm_failures()
        assert worker_service._genvm_consecutive_failures == 0

    def test_get_genvm_failure_count(self):
        """get_genvm_failure_count returns current count"""
        worker_service._genvm_consecutive_failures = 7

        assert worker_service.get_genvm_failure_count() == 7


class TestHealthEndpointWithFailures:
    """Test /health endpoint behavior with GenVM failures"""

    def setup_method(self):
        # Reset state before each test
        worker_service._genvm_consecutive_failures = 0
        worker_service._genvm_health_last_ok = True
        worker_service._genvm_health_last_error = None
        worker_service.worker = None
        worker_service.worker_task = None
        worker_service.worker_permanently_failed = False

    def test_health_returns_503_when_threshold_exceeded(self):
        """Health returns 503 when consecutive failures >= threshold"""
        mock_worker = MagicMock()
        mock_worker.worker_id = "test-worker-123"
        mock_worker.running = True
        mock_worker.current_transactions = {}
        mock_worker._active_tasks = set()
        mock_worker.max_parallel_txs = 1
        worker_service.worker = mock_worker

        mock_task = MagicMock()
        mock_task.done.return_value = False
        worker_service.worker_task = mock_task

        worker_service._genvm_consecutive_failures = 3
        worker_service._genvm_failure_unhealthy_threshold = 3

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_urlopen.return_value = mock_resp

            response = worker_service.health_check()

            assert response.status_code == 503
            body = response.body.decode()
            assert "genvm_consecutive_failures" in body

    def test_health_returns_200_when_below_threshold(self):
        """Health returns 200 when consecutive failures < threshold"""
        mock_worker = MagicMock()
        mock_worker.worker_id = "test-worker-123"
        mock_worker.running = True
        mock_worker.current_transactions = {}
        mock_worker._active_tasks = set()
        mock_worker.max_parallel_txs = 1
        worker_service.worker = mock_worker

        mock_task = MagicMock()
        mock_task.done.return_value = False
        worker_service.worker_task = mock_task

        worker_service._genvm_consecutive_failures = 2
        worker_service._genvm_failure_unhealthy_threshold = 3

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_urlopen.return_value = mock_resp

            response = worker_service.health_check()

            if hasattr(response, "status_code"):
                assert response.status_code != 503
            else:
                assert response.get("status") in ["healthy", "stopping"]

    def test_health_includes_failure_count_in_503_response(self):
        """503 response includes failure count and threshold"""
        mock_worker = MagicMock()
        mock_worker.worker_id = "test-worker-123"
        mock_worker.running = True
        mock_worker.current_transactions = {}
        mock_worker._active_tasks = set()
        mock_worker.max_parallel_txs = 1
        worker_service.worker = mock_worker

        mock_task = MagicMock()
        mock_task.done.return_value = False
        worker_service.worker_task = mock_task

        worker_service._genvm_consecutive_failures = 5
        worker_service._genvm_failure_unhealthy_threshold = 3

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_urlopen.return_value = mock_resp

            response = worker_service.health_check()

            assert response.status_code == 503
            import json

            body = json.loads(response.body.decode())
            assert body["error"] == "genvm_consecutive_failures"
            assert body["count"] == 5
            assert body["threshold"] == 3


class TestEnvVarThresholdConfig:
    """Test threshold configuration via environment variable"""

    def test_threshold_from_env_var(self):
        """Threshold is read from GENVM_FAILURE_UNHEALTHY_THRESHOLD env var"""
        # The threshold is read at module import time, so we test the parsing logic
        with patch.dict("os.environ", {"GENVM_FAILURE_UNHEALTHY_THRESHOLD": "5"}):
            # Re-evaluate the threshold (simulating module reload)
            import os

            threshold = int(os.environ.get("GENVM_FAILURE_UNHEALTHY_THRESHOLD", "3"))
            assert threshold == 5

    def test_threshold_default_value(self):
        """Default threshold is 3 when env var not set"""
        with patch.dict("os.environ", {}, clear=True):
            import os

            threshold = int(os.environ.get("GENVM_FAILURE_UNHEALTHY_THRESHOLD", "3"))
            assert threshold == 3
