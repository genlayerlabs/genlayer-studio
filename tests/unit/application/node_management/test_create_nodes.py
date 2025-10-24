import asyncio
from typing import Callable, Awaitable
import pytest
from backend.domain.types import LLMProvider
from backend.node.create_nodes.create_nodes import random_validator_config


def plugins_mock(
    plugs: list[tuple[str, bool, list[str]]],
) -> Callable[[LLMProvider], Awaitable[bool]]:
    async def impl(p: LLMProvider):
        for name, available, models in plugs:
            if not available:
                continue
            if name != p.provider:
                continue
            if p.model not in models:
                continue
            return True
        return False

    return impl


@pytest.mark.parametrize(
    "stored_providers,plugins,limit_providers,limit_models,expected",
    [
        pytest.param(
            [
                LLMProvider(
                    provider="ollama",
                    model="llama3",
                    config={},
                    plugin="ollama",
                    plugin_config={},
                )
            ],
            plugins_mock([("ollama", True, ["llama3"])]),
            None,
            None,
            [
                LLMProvider(
                    provider="ollama",
                    model="llama3",
                    config={},
                    plugin="ollama",
                    plugin_config={},
                )
            ],
            id="only ollama",
        ),
        pytest.param(
            [
                LLMProvider(
                    provider="ollama",
                    model="llama3.1",
                    config={},
                    plugin="ollama",
                    plugin_config={},
                ),
                LLMProvider(
                    provider="openai",
                    model="gpt-4-1106-preview",
                    config={},
                    plugin="openai-compatible",
                    plugin_config={},
                ),
                LLMProvider(
                    provider="openai",
                    model="gpt-4o",
                    config={},
                    plugin="openai-compatible",
                    plugin_config={},
                ),
                LLMProvider(
                    provider="heuristai",
                    model="heuristai",
                    config={},
                    plugin="heuristai",
                    plugin_config={},
                ),
            ],
            plugins_mock(
                [
                    ("ollama", True, ["llama3", "llama3.1"]),
                    ("openai", False, []),
                    ("heuristai", True, ["other"]),
                ]
            ),
            None,
            None,
            [
                LLMProvider(
                    provider="ollama",
                    model="llama3.1",
                    config={},
                    plugin="ollama",
                    plugin_config={},
                )
            ],
            id="only ollama available",
        ),
        pytest.param(
            [
                LLMProvider(
                    provider="ollama",
                    model="llama3",
                    config={},
                    plugin="ollama",
                    plugin_config={},
                ),
                LLMProvider(
                    provider="openai",
                    model="gpt-4-1106-preview",
                    config={},
                    plugin="openai-compatible",
                    plugin_config={},
                ),
                LLMProvider(
                    provider="openai",
                    model="gpt-4o",
                    config={},
                    plugin="openai-compatible",
                    plugin_config={},
                ),
                LLMProvider(
                    provider="heuristai",
                    model="a",
                    config={},
                    plugin="heuristai",
                    plugin_config={},
                ),
                LLMProvider(
                    provider="heuristai",
                    model="b",
                    config={},
                    plugin="heuristai",
                    plugin_config={},
                ),
                LLMProvider(
                    provider="heuristai",
                    model="c",
                    config={},
                    plugin="heuristai",
                    plugin_config={},
                ),
            ],
            plugins_mock(
                [
                    ("ollama", True, ["llama3", "llama3.1"]),
                    ("openai", True, ["gpt-4-1106-preview", "gpt-4o"]),
                    ("heuristai", True, ["other"]),
                ]
            ),
            ["openai"],
            None,
            [
                LLMProvider(
                    provider="openai",
                    model="gpt-4-1106-preview",
                    config={},
                    plugin="openai-compatible",
                    plugin_config={},
                ),
                LLMProvider(
                    provider="openai",
                    model="gpt-4o",
                    config={},
                    plugin="openai-compatible",
                    plugin_config={},
                ),
            ],
            id="only openai",
        ),
        pytest.param(
            [
                LLMProvider(
                    provider="openai",
                    model="gpt-4-1106-preview",
                    config={},
                    plugin="openai-compatible",
                    plugin_config={},
                ),
                LLMProvider(
                    provider="openai",
                    model="gpt-4o",
                    config={},
                    plugin="openai-compatible",
                    plugin_config={},
                ),
                LLMProvider(
                    provider="heuristai",
                    model="a",
                    config={},
                    plugin="heuristai",
                    plugin_config={},
                ),
                LLMProvider(
                    provider="heuristai",
                    model="b",
                    config={},
                    plugin="heuristai",
                    plugin_config={},
                ),
            ],
            plugins_mock(
                [
                    ("ollama", False, ["llama3", "llama3.1"]),
                    ("openai", False, ["gpt-4-1106-preview", "gpt-4o"]),
                    ("heuristai", True, ["a", "b"]),
                ]
            ),
            ["heuristai"],
            ["a"],
            [
                LLMProvider(
                    provider="heuristai",
                    model="a",
                    config={},
                    plugin="heuristai",
                    plugin_config={},
                ),
            ],
            id="only heuristai",
        ),
        pytest.param(
            [
                LLMProvider(
                    provider="ollama",
                    model="llama3.1",
                    config={},
                    plugin="ollama",
                    plugin_config={},
                ),
                LLMProvider(
                    provider="openai",
                    model="gpt-4-1106-preview",
                    config={},
                    plugin="openai-compatible",
                    plugin_config={},
                ),
                LLMProvider(
                    provider="openai",
                    model="gpt-4o",
                    config={},
                    plugin="openai-compatible",
                    plugin_config={},
                ),
                LLMProvider(
                    provider="heuristai",
                    model="a",
                    config={},
                    plugin="heuristai",
                    plugin_config={},
                ),
                LLMProvider(
                    provider="heuristai",
                    model="b",
                    config={},
                    plugin="heuristai",
                    plugin_config={},
                ),
            ],
            plugins_mock(
                [
                    ("ollama", True, ["llama3", "llama3.1"]),
                    ("openai", True, ["gpt-4-1106-preview", "gpt-4o"]),
                    ("heuristai", True, ["a", "b"]),
                ]
            ),
            None,
            None,
            [
                LLMProvider(
                    provider="ollama",
                    model="llama3.1",
                    config={},
                    plugin="ollama",
                    plugin_config={},
                ),
                LLMProvider(
                    provider="openai",
                    model="gpt-4-1106-preview",
                    config={},
                    plugin="openai-compatible",
                    plugin_config={},
                ),
                LLMProvider(
                    provider="openai",
                    model="gpt-4o",
                    config={},
                    plugin="openai-compatible",
                    plugin_config={},
                ),
                LLMProvider(
                    provider="heuristai",
                    model="a",
                    config={},
                    plugin="heuristai",
                    plugin_config={},
                ),
                LLMProvider(
                    provider="heuristai",
                    model="b",
                    config={},
                    plugin="heuristai",
                    plugin_config={},
                ),
            ],
            id="all available",
        ),
    ],
)
@pytest.mark.asyncio
async def test_random_validator_config(
    stored_providers,
    plugins,
    limit_providers,
    limit_models,
    expected,
):
    result = await random_validator_config(
        lambda: stored_providers,
        plugins,
        limit_providers,
        limit_models,
        10,
    )

    result_set = set(result)
    expected_set = set(expected)

    assert result_set.issubset(expected_set)


@pytest.mark.parametrize(
    "stored_providers,plugins,limit_providers,limit_models,exception",
    [
        pytest.param(
            [
                LLMProvider(
                    provider="ollama",
                    model="llama3",
                    config={},
                    plugin="",
                    plugin_config={},
                )
            ],
            {},
            ["heuristai", "openai"],
            None,
            ValueError,
            id="no match",
        ),
        pytest.param(
            [
                LLMProvider(
                    provider="ollama",
                    model="llama3",
                    config={},
                    plugin="",
                    plugin_config={},
                )
            ],
            plugins_mock(
                [
                    ("ollama", False, ["llama3", "llama3.1"]),
                    ("openai", True, ["gpt-4-1106-preview", "gpt-4o"]),
                    ("heuristai", True, ["a", "b"]),
                ]
            ),
            ["ollama"],
            None,
            Exception,
            id="no intersection",
        ),
    ],
)
@pytest.mark.asyncio
async def test_random_validator_config_fail(
    stored_providers,
    plugins,
    limit_providers,
    limit_models,
    exception,
):
    with pytest.raises(exception):
        await random_validator_config(
            lambda: stored_providers,
            plugins,
            limit_providers,
            limit_models,
            10,
        )
