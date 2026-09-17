from types import SimpleNamespace

from backend.services.usage_metrics_service import UsageMetricsService


def test_usage_metrics_preserves_error_from_serialized_leader_receipt():
    """Finalization metrics must not turn a VM error into a successful decision."""
    consensus_data = SimpleNamespace(
        leader_receipt=[{"execution_result": "ERROR"}],
    )

    result = UsageMetricsService()._extract_execution_result(
        finalization_data={},
        consensus_data=consensus_data,
        consensus_history=None,
    )

    assert result == "error"
