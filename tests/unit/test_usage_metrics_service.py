from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.services.usage_metrics_service import UsageMetricsService


@pytest.mark.asyncio
async def test_system_health_metrics_include_max_recovery_events():
    service = UsageMetricsService()
    service._enabled = True
    service._send_to_api = AsyncMock()

    health_cache = SimpleNamespace(
        status="degraded",
        genvm_healthy=True,
        uptime_percent=100.0,
        pending_transactions=1,
        total_decisions=2,
        total_users=3,
        issues=["max_recovery_cycles_exhausted"],
        pending_contracts=[],
        services={
            "consensus": {
                "active_workers": 1,
                "max_recovery_exhausted_transactions": [
                    {
                        "hash": "0xabc",
                        "contract_address": "0xcontract",
                        "recovery_count": 3,
                        "exhausted_at": 1779938084,
                    }
                ],
            },
            "memory": {"percent": 4.0, "cpu_percent": 5.0},
        },
    )

    await service.send_system_health_metrics(health_cache)

    service._send_to_api.assert_awaited_once()
    payload = service._send_to_api.await_args.args[0]
    assert payload["systemHealth"]["instanceHealthEvents"] == [
        {
            "type": "max_recovery_cycles_exhausted",
            "transactionHash": "0xabc",
            "contractAddress": "0xcontract",
            "recoveryCount": 3,
            "occurredAt": 1779938084,
        }
    ]
