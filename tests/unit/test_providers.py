import pytest
from backend.domain.types import LLMProvider
from backend.node.create_nodes.providers import get_default_providers, validate_provider


def test_default_providers_valid():
    providers = get_default_providers()

    assert len(providers) > 0


@pytest.mark.parametrize(
    "llm_provider",
    [
        pytest.param(
            LLMProvider(
                plugin="openai-compatible",
                provider="custom provider",
                model="custom model",
                config={},
                plugin_config={
                    "api_key_env_var": "some api key",
                    "api_url": None,
                },
            ),
            id="custom openai",
        ),
        pytest.param(
            LLMProvider(
                plugin="openai-compatible",
                provider="heuristai",
                model="mistralai/mixtral-8x7b-instruct",
                config={
                    "max_tokens": 100,
                    "temperature": 0.5,
                },
                plugin_config={
                    "api_key_env_var": "some api key",
                    "api_url": "https://llm-gateway.heurist.xyz",
                },
            ),
            id="heuristai",
        ),
        pytest.param(
            LLMProvider(
                plugin="ollama",
                provider="custom provider",
                model="custom model",
                config={
                    "mirostat": 0,
                    "mirostat_eta": 0.1,
                    "microstat_tau": 5,
                    "num_ctx": 2048,
                    "num_qga": 8,
                    "num_gpu": 0,
                    "num_thread": 2,
                    "repeat_last_n": 64,
                    "repeat_penalty": 1.1,
                    "temprature": 0.8,
                    "seed": 0,
                    "stop": "",
                    "tfs_z": 1.0,
                    "num_predict": 128,
                    "top_k": 40,
                    "top_p": 0.9,
                },
                plugin_config={
                    "api_url": "http://localhost:8000",
                },
            ),
            id="custom ollama",
        ),
    ],
)
def test_validate_provider(llm_provider):
    validate_provider(llm_provider)


def test_get_default_provider_for_returns_isolated_copy():
    """Regression: get_default_provider_for returned the shared cached
    LLMProvider instance on an exact (provider, model) match. update_validator
    then did `llm_provider.config = config` on that shared object, so every
    later caller in the process silently received the caller-supplied config
    instead of the real default. The exact-match path must return a copy, like
    the fallback-template path already does."""
    from backend.node.create_nodes.providers import (
        get_default_provider_for,
        get_default_providers,
    )

    sample = get_default_providers()[0]

    a = get_default_provider_for(sample.provider, sample.model)
    if a.config is None:
        a.config = {}
    a.config["__pollution_marker__"] = 12345

    b = get_default_provider_for(sample.provider, sample.model)
    assert (b.config or {}).get("__pollution_marker__") is None, (
        "get_default_provider_for leaked a shared cached instance; mutating one "
        "caller's config polluted the process-wide default"
    )
