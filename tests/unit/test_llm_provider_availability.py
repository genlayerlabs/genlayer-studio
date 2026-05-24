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
