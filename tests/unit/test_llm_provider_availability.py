import asyncio
from unittest.mock import MagicMock

import pytest

from backend.protocol_rpc import endpoints


class FakeLLMProviderRegistry:
    async def get_all_dict(self):
        return [
            {
                "provider": "bad-provider",
                "model": "bad-model",
                "config": {},
                "plugin": "openai-compatible",
                "plugin_config": {
                    "api_key_env_var": "BAD_KEY",
                    "api_url": "https://example.invalid/api",
                },
            },
            {
                "provider": "good-provider",
                "model": "good-model",
                "config": {},
                "plugin": "openai-compatible",
                "plugin_config": {
                    "api_key_env_var": "GOOD_KEY",
                    "api_url": "https://example.test/api",
                },
            },
        ]


class FakeGenVMManager:
    def __init__(self):
        self.logger = MagicMock()

    async def try_llms(self, providers, prompt):
        if providers[0]["model"] == "bad-model":
            raise RuntimeError("provider unavailable")
        return [{"response": "ok"}]


class SlowGenVMManager:
    def __init__(self):
        self.logger = MagicMock()

    async def try_llms(self, providers, prompt):
        await asyncio.sleep(1)
        return [{"response": "ok"}]


class CapturingGenVMManager:
    def __init__(self):
        self.logger = MagicMock()
        self.prompt = None

    async def try_llms(self, providers, prompt):
        self.prompt = prompt
        return [{"response": "ok"}]


@pytest.mark.asyncio
async def test_get_providers_and_models_marks_failed_checks_unavailable():
    providers = await endpoints.get_providers_and_models(
        FakeLLMProviderRegistry(),
        FakeGenVMManager(),
    )

    assert providers[0]["is_model_available"] is False
    assert providers[1]["is_model_available"] is True


@pytest.mark.asyncio
async def test_provider_availability_timeout_returns_unavailable(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_AVAILABILITY_TIMEOUT_SECONDS", "0.01")
    manager = SlowGenVMManager()

    available = await endpoints.check_provider_is_available(
        manager,
        {
            "provider": "slow-provider",
            "model": "slow-model",
            "config": {},
            "plugin": "openai-compatible",
            "plugin_config": {
                "api_key_env_var": "SLOW_KEY",
                "api_url": "https://example.test/api",
            },
        },
    )

    assert available is False
    manager.logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_provider_availability_forwards_extra_config():
    manager = CapturingGenVMManager()
    policy_ir = [
        "policy",
        ["and", ["meets_req"], ["not", ["is", "disabled"]]],
        ["zero"],
        ["argmax"],
        ["id"],
        ["always", {"action": "next_candidate"}],
    ]

    available = await endpoints.check_provider_is_available(
        manager,
        {
            "provider": "llm-router",
            "model": "policy:auto",
            "config": {
                "temperature": 0.5,
                "max_tokens": 250,
                "use_max_completion_tokens": True,
                "policy_ir": policy_ir,
            },
            "plugin": "openai-compatible",
            "plugin_config": {
                "api_key_env_var": "LLM_ROUTER_API_KEY",
                "api_url": "https://internal-router.genlayer.com",
            },
        },
    )

    assert available is True
    assert manager.prompt["temperature"] == 0.5
    assert manager.prompt["max_tokens"] == 250
    assert manager.prompt["use_max_completion_tokens"] is True
    assert manager.prompt["extra"] == {"policy_ir": policy_ir}
