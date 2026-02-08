"""
Tests for GenVM execution failure tracking in JSON-RPC health check.
Mirrors test_worker_health_degradation.py pattern for jsonrpc process.
"""

import pytest
from unittest.mock import patch, MagicMock

import backend.protocol_rpc.health as health_module


class TestGenVMExecutionFailureTracking:
    """Test GenVM execution failure tracking functions."""

    def setup_method(self):
        health_module._genvm_consecutive_failures = 0

    def test_record_failure_increments(self):
        assert health_module._genvm_consecutive_failures == 0

        health_module.record_genvm_execution_failure()
        assert health_module._genvm_consecutive_failures == 1

        health_module.record_genvm_execution_failure()
        assert health_module._genvm_consecutive_failures == 2

    def test_record_success_resets(self):
        health_module._genvm_consecutive_failures = 5

        health_module.record_genvm_execution_success()
        assert health_module._genvm_consecutive_failures == 0

    def test_record_success_noop_when_zero(self):
        health_module._genvm_consecutive_failures = 0

        health_module.record_genvm_execution_success()
        assert health_module._genvm_consecutive_failures == 0

    def test_get_failure_count(self):
        health_module._genvm_consecutive_failures = 7

        assert health_module.get_genvm_execution_failure_count() == 7


class TestHealthEndpointWithExecutionFailures:
    """Test /health endpoint behavior with GenVM execution failures."""

    def setup_method(self):
        health_module._genvm_consecutive_failures = 0
        health_module._genvm_failure_unhealthy_threshold = 3
        # Set up a valid health cache so the endpoint doesn't 503 for other reasons
        health_module._health_cache.genvm_healthy = True
        health_module._health_cache.genvm_error = None
        health_module._health_cache.last_check = 1000000.0
        health_module._health_cache.status = "healthy"
        health_module._health_cache.issues = []
        health_module._health_cache.services = {}
        health_module._health_cache.error = None

    @pytest.mark.asyncio
    async def test_health_returns_503_when_threshold_exceeded(self):
        health_module._genvm_consecutive_failures = 3

        response = await health_module.health_check()

        assert response.status_code == 503
        body = response.body.decode()
        assert "genvm_consecutive_failures" in body

    @pytest.mark.asyncio
    async def test_health_returns_200_when_below_threshold(self):
        health_module._genvm_consecutive_failures = 2

        response = await health_module.health_check()

        # Healthy returns a dict, not JSONResponse
        assert isinstance(response, dict)
        assert response["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_health_503_includes_count_and_threshold(self):
        health_module._genvm_consecutive_failures = 5

        response = await health_module.health_check()

        assert response.status_code == 503
        import json

        body = json.loads(response.body.decode())
        assert body["error"] == "genvm_consecutive_failures"
        assert body["count"] == 5
        assert body["threshold"] == 3

    @pytest.mark.asyncio
    async def test_genvm_status_probe_failure_still_503(self):
        """If /status probe fails, that still returns 503 (existing behavior)."""
        health_module._health_cache.genvm_healthy = False

        response = await health_module.health_check()

        assert response.status_code == 503
        import json

        body = json.loads(response.body.decode())
        assert body["error"] == "genvm_manager_unresponsive"


class TestReadinessWithExecutionFailures:
    """Test /ready endpoint behavior with GenVM execution failures."""

    def setup_method(self):
        health_module._genvm_consecutive_failures = 0
        health_module._genvm_failure_unhealthy_threshold = 3

    @pytest.mark.asyncio
    async def test_ready_returns_not_ready_when_threshold_exceeded(self):
        health_module._genvm_consecutive_failures = 3

        check_fn = health_module.create_readiness_check_with_state(MagicMock())
        response = await check_fn()

        assert response["status"] == "not_ready"
        assert response["genvm_execution_failures"] == 3

    @pytest.mark.asyncio
    async def test_ready_returns_ready_when_below_threshold(self):
        health_module._genvm_consecutive_failures = 2

        check_fn = health_module.create_readiness_check_with_state(MagicMock())
        response = await check_fn()

        assert response["status"] == "ready"
        assert "genvm_execution_failures" not in response

    @pytest.mark.asyncio
    async def test_ready_not_ready_when_rpc_router_missing(self):
        check_fn = health_module.create_readiness_check_with_state(None)
        response = await check_fn()

        assert response["status"] == "not_ready"
        assert response["rpc_router_initialized"] is False


class TestThresholdConfig:
    """Test threshold configuration via environment variable."""

    def test_threshold_from_env_var(self):
        with patch.dict("os.environ", {"GENVM_FAILURE_UNHEALTHY_THRESHOLD": "5"}):
            import os

            threshold = int(os.environ.get("GENVM_FAILURE_UNHEALTHY_THRESHOLD", "3"))
            assert threshold == 5

    def test_threshold_default_value(self):
        with patch.dict("os.environ", {}, clear=True):
            import os

            threshold = int(os.environ.get("GENVM_FAILURE_UNHEALTHY_THRESHOLD", "3"))
            assert threshold == 3
