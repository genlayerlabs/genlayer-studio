"""
Tests for GenVM execution failure tracking in JSON-RPC health check.
Mirrors test_worker_health_degradation.py pattern for jsonrpc process.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

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
        health_module._no_progress_scan_suppressed_until = 0.0

    def _install_fake_consensus_db(
        self,
        monkeypatch,
        *,
        recovery_storm_count=0,
        max_recovery_count=0,
        exhausted_rows=None,
        backlog_count=0,
        oldest_created_at=None,
        oldest_backlog_age_seconds=None,
        progress_epoch=1,
        progress_error=False,
    ):
        class FakeResult:
            def __init__(self, row=None, rows=None):
                self.row = row
                self.rows = rows or []

            def fetchone(self):
                return self.row

            def fetchall(self):
                return self.rows

        class FakeConnection:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def execute(self, statement, params=None):
                query = str(statement)
                if "COUNT(DISTINCT worker_id)" in query:
                    return FakeResult(SimpleNamespace(n=0))
                if "COUNT(*) AS stuck_heads" in query:
                    return FakeResult(SimpleNamespace(stuck_heads=0))
                if "timestamp_awaiting_finalization" in query:
                    return FakeResult(SimpleNamespace(n=0))
                if "COALESCE(MAX(recovery_count), 0)" in query:
                    return FakeResult(
                        SimpleNamespace(
                            n=recovery_storm_count,
                            max_recovery_count=max_recovery_count,
                        )
                    )
                if "max_recovery_cycles_exceeded" in query:
                    return FakeResult(rows=exhausted_rows or [])
                if "WHERE status IN ('ACTIVATED'" in query:
                    return FakeResult(SimpleNamespace(n=0))
                if "SET LOCAL statement_timeout" in query:
                    return FakeResult()
                if "last_progress_epoch" in query:
                    if progress_error:
                        raise TimeoutError("progress scan timed out")
                    return FakeResult(
                        SimpleNamespace(last_progress_epoch=progress_epoch)
                    )
                if "backlog_count" in query:
                    return FakeResult(
                        SimpleNamespace(
                            backlog_count=backlog_count,
                            oldest_created_at=oldest_created_at,
                            oldest_backlog_age_seconds=oldest_backlog_age_seconds,
                        )
                    )
                raise AssertionError(f"unexpected query: {query}")

        class FakeEngine:
            def connect(self):
                return FakeConnection()

        import backend.database_handler.session_factory as session_factory

        monkeypatch.setattr(
            session_factory,
            "get_database_manager",
            lambda: SimpleNamespace(engine=FakeEngine()),
        )
        monkeypatch.setattr(health_module, "_rpc_router_ref", object())

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

    @pytest.mark.asyncio
    async def test_consensus_health_includes_max_recovery_exhaustion_events(
        self, monkeypatch
    ):
        exhausted_tx = SimpleNamespace(
            total_count=1,
            tx_hash="0xabc",
            to_address="0xcontract",
            recovery_count=3,
            exhausted_at_epoch=1779938084,
        )

        self._install_fake_consensus_db(
            monkeypatch,
            recovery_storm_count=1,
            max_recovery_count=2,
            exhausted_rows=[exhausted_tx],
            backlog_count=3,
            oldest_created_at=object(),
            oldest_backlog_age_seconds=3600,
            progress_epoch=1,
        )

        result = await health_module._check_consensus_health()

        assert result["status"] == "degraded"
        assert result["recovery_storm_count"] == 1
        assert result["max_recovery_count"] == 2
        assert result["no_consensus_progress"] is True
        assert result["seconds_since_consensus_progress"] is not None
        assert result["max_recovery_exhausted_count"] == 1
        assert result["max_recovery_exhausted_transactions"] == [
            {
                "hash": "0xabc",
                "contract_address": "0xcontract",
                "recovery_count": 3,
                "exhausted_at": 1779938084,
            }
        ]

    @pytest.mark.asyncio
    async def test_consensus_health_suppresses_no_progress_scan_during_cooldown(
        self, monkeypatch
    ):
        monkeypatch.setattr(health_module.time, "time", lambda: 1000)
        health_module._no_progress_scan_suppressed_until = 2000
        self._install_fake_consensus_db(
            monkeypatch,
            backlog_count=3,
            oldest_created_at=object(),
            oldest_backlog_age_seconds=3600,
        )

        result = await health_module._check_consensus_health()

        assert result["status"] == "healthy"
        assert result["no_consensus_progress"] is False
        assert result["no_progress_check_error"] is True
        assert result["no_progress_scan_suppressed"] is True
        assert result["seconds_since_consensus_progress"] is None

    @pytest.mark.asyncio
    async def test_consensus_health_cooldowns_after_no_progress_scan_error(
        self, monkeypatch
    ):
        monkeypatch.setattr(health_module.time, "time", lambda: 1000)
        self._install_fake_consensus_db(
            monkeypatch,
            backlog_count=3,
            oldest_created_at=object(),
            oldest_backlog_age_seconds=3600,
            progress_error=True,
        )

        result = await health_module._check_consensus_health()

        assert result["status"] == "healthy"
        assert result["no_consensus_progress"] is False
        assert result["no_progress_check_error"] is True
        assert result["no_progress_scan_suppressed"] is False
        assert (
            health_module._no_progress_scan_suppressed_until
            == 1000 + health_module.get_no_progress_scan_error_cooldown_seconds()
        )

    @pytest.mark.asyncio
    async def test_consensus_health_skips_no_progress_scan_without_old_backlog(
        self, monkeypatch
    ):
        health_module._no_progress_scan_suppressed_until = 123
        self._install_fake_consensus_db(
            monkeypatch,
            backlog_count=1,
            oldest_created_at=None,
            oldest_backlog_age_seconds=None,
        )

        result = await health_module._check_consensus_health()

        assert result["status"] == "healthy"
        assert result["no_consensus_progress"] is False
        assert result["no_progress_check_error"] is False
        assert result["no_progress_scan_suppressed"] is False
        assert result["no_progress_backlog_count"] == 1
        assert result["oldest_backlog_age_seconds"] is None
        assert health_module._no_progress_scan_suppressed_until == 0.0


class TestDetailedConsensusHealthEndpoint:
    """Unit coverage for the detailed /health/consensus endpoint."""

    @pytest.mark.asyncio
    async def test_health_consensus_reports_stuck_head_details(self, monkeypatch):
        now = datetime.now(timezone.utc)
        stuck_created_at = now - timedelta(minutes=90)
        blocked_at = now - timedelta(minutes=5)

        contract_rows = [
            SimpleNamespace(
                contract_address="0xcontract1",
                processing_count=4,
                pending_count=2,
                created_last_1h=1,
                created_last_3h=4,
                created_last_6h=4,
                created_last_12h=4,
                created_last_1d=4,
                oldest_blocked_at=blocked_at,
                oldest_processing_created_at=stuck_created_at,
                stuck_head_hash="0xstuck",
                stuck_head_status="COMMITTING",
                stuck_head_created_at=stuck_created_at,
            ),
            SimpleNamespace(
                contract_address="0xcontract2",
                processing_count=3,
                pending_count=1,
                created_last_1h=0,
                created_last_3h=3,
                created_last_6h=3,
                created_last_12h=3,
                created_last_1d=3,
                oldest_blocked_at=None,
                oldest_processing_created_at=None,
                stuck_head_hash=None,
                stuck_head_status=None,
                stuck_head_created_at=None,
            ),
        ]

        class FakeResult:
            def __init__(self, row=None, rows=None):
                self.row = row
                self.rows = rows or []

            def fetchone(self):
                return self.row

            def __iter__(self):
                return iter(self.rows)

        class FakeConnection:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def execute(self, statement, params=None):
                query = str(statement)
                if "COUNT(DISTINCT worker_id)" in query:
                    return FakeResult(SimpleNamespace(n=2))
                if "WITH contract_stats AS" in query:
                    assert params == {"head_stuck_minutes": 15, "claim_window": 30}
                    return FakeResult(rows=contract_rows)
                raise AssertionError(f"unexpected query: {query}")

        class FakeEngine:
            def connect(self):
                return FakeConnection()

        import backend.database_handler.session_factory as session_factory

        monkeypatch.setattr(
            session_factory,
            "get_database_manager",
            lambda: SimpleNamespace(engine=FakeEngine()),
        )

        result = await health_module.health_consensus(rpc_router=object())

        assert result["status"] == "healthy"
        assert result["total_processing_transactions"] == 7
        assert result["total_orphaned_transactions"] == 1
        assert result["active_workers"] == 2

        stuck_contract = result["contracts"][0]
        assert stuck_contract["orphaned_transactions"] == 1
        assert stuck_contract["orphaned_transaction_hashes"] == ["0xstuck"]
        assert stuck_contract["stuck_head_transaction"] == {
            "hash": "0xstuck",
            "status": "COMMITTING",
            "created_at": stuck_created_at.isoformat(),
            "elapsed": "1h",
        }

        active_contract = result["contracts"][1]
        assert active_contract["orphaned_transactions"] == 0
        assert "stuck_head_transaction" not in active_contract


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
