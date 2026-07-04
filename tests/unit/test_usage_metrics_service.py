from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.node.genvm import base as genvm_base
from backend.node.genvm.origin import public_abi
from backend.services.usage_metrics_service import UsageMetricsService


def _receipt(node_config, execution_stats=None):
    return SimpleNamespace(
        node_config=node_config,
        execution_stats=execution_stats,
    )


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


@pytest.mark.asyncio
async def test_system_health_metrics_include_stuck_head_events():
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
        issues=["orphaned_transactions"],
        pending_contracts=[],
        services={
            "consensus": {
                "active_workers": 0,
                "stuck_head_transactions": [
                    {
                        "hash": "0xstuck",
                        "contract_address": "0xcontract",
                        "status": "COMMITTING",
                        "created_at": 1780933606,
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
            "type": "orphaned_transactions",
            "transactionHash": "0xstuck",
            "contractAddress": "0xcontract",
            "status": "COMMITTING",
            "occurredAt": 1780933606,
        }
    ]


def test_extract_llm_token_metrics_keeps_only_llm_tokens():
    metrics = {
        "llm": {
            "tokens": {
                "node-0xprimary/policy:dev-openai": {
                    "input": 12,
                    "output": 5,
                    "total": 17,
                }
            },
            "scripting": {"requests": 1},
        },
        "web": {"requests": 3},
    }

    assert genvm_base._extract_llm_token_metrics(metrics) == {
        "node-0xprimary/policy:dev-openai": {
            "input": 12,
            "output": 5,
            "total": 17,
        }
    }


def test_provide_result_preserves_llm_token_metrics():
    host = object.__new__(genvm_base.Host)
    host._nondet_disagreement = None

    res = SimpleNamespace(
        result_kind=public_abi.ResultCode.RETURN,
        result_data={"ok": True},
        result_storage_changes=[],
        result_emissions=[],
        result_nondet_results=[],
        stdout="",
        stderr="",
        genvm_log=[],
        metrics={
            "llm": {
                "tokens": {
                    "node-0xprimary/policy:dev-openai": {
                        "input": 12,
                        "output": 5,
                        "total": 17,
                    }
                }
            }
        },
        vm_error_description=None,
        data_fees_remaining=0,
    )

    result = host.provide_result(res, SimpleNamespace(), genvm_base.Context())

    assert result.execution_stats == {
        "llm": {
            "tokens": {
                "node-0xprimary/policy:dev-openai": {
                    "input": 12,
                    "output": 5,
                    "total": 17,
                }
            }
        }
    }


def test_extract_llm_calls_uses_primary_token_metrics():
    service = UsageMetricsService()
    receipt = _receipt(
        {
            "address": "0xprimary",
            "primary_model": {
                "provider": "llm-router",
                "model": "policy:dev-openai",
            },
            "secondary_model": None,
        },
        {
            "llm": {
                "tokens": {
                    "node-0xprimary/policy:dev-openai": {
                        "input": 12,
                        "output": 5,
                        "total": 17,
                    }
                }
            }
        },
    )

    assert service._extract_llm_calls_from_receipt(receipt) == [
        {
            "provider": "llm-router",
            "model": "policy:dev-openai",
            "inputTokens": 12,
            "outputTokens": 5,
            "costUsd": 0,
        }
    ]


def test_extract_llm_calls_skips_receipts_without_model_config():
    service = UsageMetricsService()

    assert service._extract_llm_calls_from_receipt(None) == []
    assert service._extract_llm_calls_from_receipt(_receipt(None)) == []
    assert service._extract_llm_calls_from_receipt(_receipt({})) == []
    assert service._build_llm_call(None) is None
    assert service._build_llm_call({"provider": "unknown", "model": "unknown"}) is None


def test_extract_llm_call_legacy_and_defensive_paths():
    service = UsageMetricsService()
    dict_receipt = {
        "node_config": {
            "address": "0xprimary",
            "primary_model": {
                "provider": "llm-router",
                "model": "policy:dev-openai",
            },
        },
        "execution_stats": {"llm": "invalid"},
    }

    assert service._extract_llm_call_from_receipt(dict_receipt) == {
        "provider": "llm-router",
        "model": "policy:dev-openai",
        "inputTokens": 0,
        "outputTokens": 0,
        "costUsd": 0,
    }
    assert service._extract_llm_call_from_receipt(None) is None
    assert service._configured_model_from_token_key(
        {"provider": "llm-router", "model": "policy:dev-openai"},
        "malformed-token-key",
    ) == {
        "provider": "llm-router",
        "model": "policy:dev-openai",
    }


def test_extract_llm_calls_uses_fallback_token_metrics():
    service = UsageMetricsService()
    receipt = _receipt(
        {
            "address": "0xprimary",
            "primary_model": {
                "provider": "llm-router",
                "model": "policy:dev-openai",
            },
            "secondary_model": {
                "address": "0xfallback",
                "provider": "openrouter",
                "model": "anthropic/claude-sonnet-4.5",
            },
        },
        {
            "llm": {
                "tokens": {
                    "node-0xfallback/anthropic/claude-sonnet-4.5": {
                        "input": 9,
                        "output": 4,
                        "total": 13,
                    }
                }
            }
        },
    )

    assert service._extract_llm_calls_from_receipt(receipt) == [
        {
            "provider": "openrouter",
            "model": "anthropic/claude-sonnet-4.5",
            "inputTokens": 9,
            "outputTokens": 4,
            "costUsd": 0,
        }
    ]


def test_extract_llm_calls_falls_back_to_primary_provider_for_unknown_token_key():
    service = UsageMetricsService()
    receipt = _receipt(
        {
            "address": "0xprimary",
            "primary_model": {
                "provider": "llm-router",
                "model": "policy:dev-openai",
            },
            "secondary_model": None,
        },
        {
            "llm": {
                "tokens": {
                    "node-0xother/policy:unexpected": {
                        "input": "7",
                        "output": "bad-value",
                        "total": 7,
                    }
                }
            }
        },
    )

    assert service._extract_llm_calls_from_receipt(receipt) == [
        {
            "provider": "llm-router",
            "model": "policy:unexpected",
            "inputTokens": 7,
            "outputTokens": 0,
            "costUsd": 0,
        }
    ]


def test_extract_llm_calls_falls_back_to_primary_without_token_metrics():
    service = UsageMetricsService()
    receipt = _receipt(
        {
            "address": "0xprimary",
            "primary_model": {
                "provider": "llm-router",
                "model": "policy:dev-openai",
            },
            "secondary_model": {
                "address": "0xfallback",
                "provider": "openrouter",
                "model": "anthropic/claude-sonnet-4.5",
            },
        }
    )

    assert service._extract_llm_calls_from_receipt(receipt) == [
        {
            "provider": "llm-router",
            "model": "policy:dev-openai",
            "inputTokens": 0,
            "outputTokens": 0,
            "costUsd": 0,
        }
    ]
