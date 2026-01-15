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
