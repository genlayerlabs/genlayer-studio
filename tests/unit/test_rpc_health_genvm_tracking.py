"""
Tests for GenVM execution failure tracking in JSON-RPC health check.
Mirrors test_worker_health_degradation.py pattern for jsonrpc process.
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

import backend.protocol_rpc.health as health_module


class TestGenVMExecutionFailureTracking:
    """Test GenVM execution failure tracking functions."""

    def setup_method(self):
        health_module._genvm_consecutive_failures = 0
        health_module._health_cache.genvm_healthy = True

    def test_record_failure_increments_when_manager_unhealthy(self):
        """Failures count toward liveness only when manager /status is also down."""
        health_module._health_cache.genvm_healthy = False
        assert health_module._genvm_consecutive_failures == 0

        health_module.record_genvm_execution_failure()
        assert health_module._genvm_consecutive_failures == 1

        health_module.record_genvm_execution_failure()
        assert health_module._genvm_consecutive_failures == 2

    def test_record_failure_ignored_when_manager_healthy(self):
        """When manager /status is healthy, failures are capacity-related — don't count."""
        health_module._health_cache.genvm_healthy = True

        health_module.record_genvm_execution_failure()
        assert health_module._genvm_consecutive_failures == 0

    def test_record_failure_resets_counter_when_manager_healthy(self):
        """If counter was previously elevated, it resets when manager is healthy."""
        health_module._health_cache.genvm_healthy = False
        health_module.record_genvm_execution_failure()
        health_module.record_genvm_execution_failure()
        assert health_module._genvm_consecutive_failures == 2

        health_module._health_cache.genvm_healthy = True
        health_module.record_genvm_execution_failure()
        assert health_module._genvm_consecutive_failures == 0

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
        health_module._health_cache.genvm_healthy = True
        health_module._health_cache.genvm_available_permits = None

    @pytest.mark.asyncio
    async def test_ready_returns_not_ready_when_threshold_exceeded(self):
        health_module._genvm_consecutive_failures = 3

        check_fn = health_module.create_readiness_check_with_state(MagicMock())
        response = await check_fn()

        # not-ready uses HTTP 503 so Kubernetes stops routing traffic
        import json

        assert response.status_code == 503
        payload = json.loads(response.body.decode())
        assert payload["status"] == "not_ready"
        assert payload["genvm_execution_failures"] == 3

    @pytest.mark.asyncio
    async def test_ready_returns_ready_when_below_threshold(self):
        health_module._genvm_consecutive_failures = 2

        check_fn = health_module.create_readiness_check_with_state(MagicMock())
        response = await check_fn()

        assert isinstance(response, dict)
        assert response["status"] == "ready"
        assert "genvm_execution_failures" not in response

    @pytest.mark.asyncio
    async def test_ready_not_ready_when_rpc_router_missing(self):
        check_fn = health_module.create_readiness_check_with_state(None)
        response = await check_fn()

        import json

        assert response.status_code == 503
        payload = json.loads(response.body.decode())
        assert payload["status"] == "not_ready"
        assert payload["rpc_router_initialized"] is False


class TestBackgroundHealthGenVMOrdering:
    """GenVM readiness state must be initialized before slower alert queries."""

    def setup_method(self):
        health_module._health_cache = health_module.HealthCache()
        health_module._genvm_consecutive_failures = 0

    @pytest.mark.asyncio
    async def test_genvm_cache_updates_before_database_and_consensus_checks(
        self, monkeypatch
    ):
        events = []

        async def fake_genvm_health():
            events.append("genvm")
            return (
                True,
                None,
                {
                    "permits": {"current": 4, "max": 5},
                    "executions": {"tx-1": {}},
                },
            )

        async def fake_database_health():
            events.append(
                (
                    "database",
                    health_module._health_cache.genvm_healthy,
                    health_module._health_cache.genvm_available_permits,
                )
            )
            return {
                "status": "healthy",
                "connection_pool": {"size": 10, "checked_out": 0},
            }

        async def fake_consensus_health():
            events.append(
                (
                    "consensus",
                    health_module._health_cache.genvm_healthy,
                    health_module._health_cache.genvm_available_permits,
                )
            )
            return {
                "status": "healthy",
                "total_processing_transactions": 0,
                "total_orphaned_transactions": 0,
                "stuck_finalization_count": 0,
                "active_workers": 0,
            }

        async def fake_llm_health():
            return {
                "status": "no_data",
                "alert_providers": [],
                "window_minutes": 15,
                "total_samples": 0,
            }

        async def fake_memory_health():
            return {
                "status": "healthy",
                "memory_usage_mb": 1,
                "memory_percent": 1,
                "cpu_percent": 0,
            }

        monkeypatch.setattr(health_module, "_check_genvm_health", fake_genvm_health)
        monkeypatch.setattr(
            health_module, "_check_database_health", fake_database_health
        )
        monkeypatch.setattr(
            health_module, "_check_consensus_health", fake_consensus_health
        )
        monkeypatch.setattr(
            health_module, "_check_llm_provider_health", fake_llm_health
        )
        monkeypatch.setattr(health_module, "_check_memory_health", fake_memory_health)
        monkeypatch.setattr(
            health_module, "_check_redis_health", AsyncMock(return_value="healthy")
        )
        monkeypatch.setattr(
            health_module,
            "_get_aggregate_counts",
            AsyncMock(return_value=(1, 2, 3)),
        )
        monkeypatch.setattr(
            health_module, "_get_pending_contracts", AsyncMock(return_value=[])
        )

        await health_module._run_health_checks()

        assert events == [
            "genvm",
            ("database", True, 4),
            ("consensus", True, 4),
        ]
        assert health_module._health_cache.genvm_healthy is True
        assert health_module._health_cache.genvm_max_permits == 5
        assert health_module._health_cache.genvm_available_permits == 4
        assert health_module._health_cache.genvm_active_executions == 1


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
