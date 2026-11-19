import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.domain.types import LLMProvider
from backend.protocol_rpc import endpoints


class FakeLLMProviderRegistry:
    PROVIDERS = [
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

    async def get_all_dict(self):
        return [dict(p) for p in self.PROVIDERS]


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
async def test_get_providers_and_models_does_not_probe_llms(monkeypatch):
    async def fail_probe(*_args, **_kwargs):
        raise AssertionError("listing providers must not call LLMs")

    monkeypatch.setattr(endpoints, "check_provider_is_available", fail_probe)

    providers = await endpoints.get_providers_and_models(FakeLLMProviderRegistry())

    assert [p["model"] for p in providers] == ["bad-model", "good-model"]


def _validator_test_setup(monkeypatch, provider_dict):
    session = object()
    account = SimpleNamespace(address="0xabc", key="priv")
    accounts_manager = MagicMock()
    accounts_manager.create_new_account.return_value = account
    registry = SimpleNamespace(
        create_validator=AsyncMock(return_value={"address": account.address}),
        update_validator=AsyncMock(return_value={"address": account.address}),
    )
    monkeypatch.setattr(endpoints, "validate_provider", lambda _provider: None)
    monkeypatch.setattr(endpoints, "AccountsManager", lambda _session: accounts_manager)
    monkeypatch.setattr(
        endpoints,
        "get_default_provider_for",
        lambda provider, model: LLMProvider(
            provider=provider,
            model=model,
            config=provider_dict["config"],
            plugin=provider_dict["plugin"],
            plugin_config=provider_dict["plugin_config"],
        ),
    )
    return session, SimpleNamespace(registry=registry)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_dict, expected",
    [
        (FakeLLMProviderRegistry.PROVIDERS[0], False),
        (FakeLLMProviderRegistry.PROVIDERS[1], True),
    ],
)
async def test_create_validator_probes_provider_without_blocking(
    monkeypatch, provider_dict, expected
):
    session, validators_manager = _validator_test_setup(monkeypatch, provider_dict)

    result = await endpoints.create_validator(
        session,
        validators_manager,
        FakeGenVMManager(),
        stake=1,
        provider=provider_dict["provider"],
        model=provider_dict["model"],
    )

    # The validator is saved even when its provider fails the probe.
    validators_manager.registry.create_validator.assert_awaited_once()
    assert result == {"address": "0xabc", "llm_provider_available": expected}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_dict, expected",
    [
        (FakeLLMProviderRegistry.PROVIDERS[0], False),
        (FakeLLMProviderRegistry.PROVIDERS[1], True),
    ],
)
async def test_update_validator_probes_provider_without_blocking(
    monkeypatch, provider_dict, expected
):
    session, validators_manager = _validator_test_setup(monkeypatch, provider_dict)

    result = await endpoints.update_validator(
        session,
        validators_manager,
        FakeGenVMManager(),
        validator_address="0xabc",
        stake=1,
        provider=provider_dict["provider"],
        model=provider_dict["model"],
    )

    validators_manager.registry.update_validator.assert_awaited_once()
    assert result == {"address": "0xabc", "llm_provider_available": expected}


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
