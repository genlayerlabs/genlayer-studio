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


@pytest.mark.asyncio
async def test_get_providers_and_models_marks_failed_checks_unavailable():
    providers = await endpoints.get_providers_and_models(
        FakeLLMProviderRegistry(),
        FakeGenVMManager(),
    )

    assert providers[0]["is_model_available"] is False
    assert providers[1]["is_model_available"] is True
